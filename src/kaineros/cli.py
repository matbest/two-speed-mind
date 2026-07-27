"""The two-speed chat shell.

Run with ``kaineros`` or ``python -m kaineros``. Plain text is a conversation turn; lines starting
with ``/`` are slash commands. Wired with the deterministic fakes for now — swap in a cloud/local
model (Slice 5) without touching this file.

The session owns the buffer and the compiled marker (spec §17): each turn, only the not-yet-compiled
tail is handed to the slow model — a turn is extracted exactly once, ever.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from .compiler import Compiler
from .fakes import FakeFastModel, FakeJudge, FakeSlowModel
from .interfaces import FastModel, Judge, SlowModel
from .runtime import Runtime
from .schema import CompileReport, Page, Response, Turn
from .store import Store

BANNER = (
    "kaineros - a two-speed mind that compiles experience into its kainome.\n"
    "Type to chat. Slash commands: /help  /notebook  /why  /model  /forget  /quit\n"
)

HELP = (
    "  /help      show this\n"
    "  /notebook  show the kainome (promoted pages - the clean knowledge base)\n"
    "  /why       the grounded reason behind the last answer\n"
    "  /questions the disambiguation questions the mind has queued to ask\n"
    "  /cleanup   force the deep brain's cross-page cleanup now (fusion + conflict check)\n"
    "  /model     show or switch models: /model deep|fast [model-id]  (--cloud only)\n"
    "  /profile   list profiles, or /profile <name> to switch (each has its own wiki)\n"
    "  /persona   /persona <name> builds a fresh wiki from a scripted person (watch it grow)\n"
    "  /metrics   run a benchmark live and score it - pick a family (conflict / retrieval)\n"
    "  /bench     external benchmarks (PersonaMem): sample / sample-big / slice-smoke / slice\n"
    "  /prompt    print the static system prompts + schemas we send (extract / judge / phrase)\n"
    "  /clear     wipe the screen and refresh the panels (keeps the mind)\n"
    "  /debug     set the live model-call panel depth (off / 5 / 10 / 15 / 20 lines)\n"
    "  /forget    clear the short-term buffer   (/forget all erases the whole mind)\n"
    "  /quit      exit\n"
)


_Q_WORDS = frozenset(
    "who what where when why how which whose whom do does did is are am was were can could will "
    "would should have has had tell list name give".split()
)


def _prompt(session) -> str:
    """The input prompt reflects the deep brain's state: 'thinking (N)>' while it still has turns
    to compile in the background, else 'you>'. The two speeds, visible at the prompt."""
    n = session.backlog() if session.background else 0
    return f"thinking ({n})> " if n > 0 else "you> "


def _is_question(text: str) -> bool:
    """A cheap intent split: is this turn asking, or just telling? Statements skip retrieval."""
    t = text.strip().lower()
    if not t:
        return False
    if t.endswith("?"):
        return True
    return t.split()[0].strip(",.'\"") in _Q_WORDS


class Session:
    """One conversation: buffer + compiled marker + the two brains over one store."""

    def __init__(
        self,
        store: Store | None = None,
        judge: Judge | None = None,
        slow: SlowModel | None = None,
        fast: FastModel | None = None,
        store_dir: str | None = None,
        background: bool = False,
        cleanup_every: int = 1,
    ) -> None:
        self.store_dir = store_dir
        # how often the deep brain runs its expensive cross-page cleanup (fusion + conflict
        # detection). 1 = every pass (thorough; tests, metrics). Higher = save tokens in chat.
        self.cleanup_every = cleanup_every
        self._passes = 0
        if store is None and store_dir is not None:
            from .persist import load_store

            store = load_store(store_dir)  # spec §23: corrupt files raise here, loudly
        self.store = store or Store()
        self.slow = slow or FakeSlowModel()
        self.compiler = Compiler(self.store, judge or FakeJudge())
        self.runtime = Runtime(self.store, fast or FakeFastModel())
        self.buffer: list[Turn] = []
        self.compiled_upto = 0  # spec §17: turns before this index have been extracted
        self.last_response: Response | None = None
        self.cloud = False  # set by main() when wired with the cloud adapters
        # the posture the status bar reports (spec §27): default is the offline fakes — nothing
        # runs and nothing leaves the device. main() overrides for real backends.
        self.backend_label = "OFFLINE"
        self.backend_detail = "deterministic fakes"
        self.offdevice = False
        self.profile_name = "(default)"  # which named mind is loaded (spec §34)
        # background consolidation (spec §24-27): one daemon worker drains the buffer tail;
        # concurrency relies on CPython atomics — the worker is the sole store mutator, retrieval
        # reads atomic snapshots (store.pages()), pages are replaced never mutated in place
        self.background = background
        from .metering import TokenMeter

        # one meter per brain (spec §33) — a proxy for local compute load. Adapters add to these;
        # cli wires them into the models. Fakes never touch them, so they read zero.
        self.deep_meter = TokenMeter()
        self.fast_meter = TokenMeter()
        self.on_compiled = None  # callback fired after each background pass (cockpit repaint)
        self._asked = None       # the question surfaced last turn, awaiting the user's answer
        self.running_note = None  # when set, the header shows a "running metric" banner
        self.calls_lines = 5      # depth of the live model-call debug panel (0 = hidden); /debug
        self.retry_backoff = 0.5  # base seconds between failed-pass retries (tests shrink it)
        self._wake = threading.Event()
        if background:
            threading.Thread(target=self._worker_loop, daemon=True).start()

    def backlog(self) -> int:
        return len(self.buffer) - self.compiled_upto

    def consolidate(self) -> list[Page]:
        """Run the slow brain over the un-compiled tail only (exactly-once), then housekeep.

        The expensive CROSS-PAGE work (fusion/conflicts) runs only on the `cleanup_every` cadence;
        set that high (as the bench does during ingest) to keep ingest cheap and defer cross-page
        grooming to an explicit later pass (spec §49) — otherwise ingesting a growing mind gets
        slower each session."""
        upto = len(self.buffer)  # snapshot: turns arriving mid-pass belong to the next pass
        fresh = self.buffer[self.compiled_upto : upto]
        candidates = self.slow.extract(fresh)
        # marker advances only after extraction succeeds: a failed/cancelled call must not lose
        # the turns. A re-run may re-extract (at-least-once); housekeeping's dedup merges that.
        self.compiled_upto = upto
        if self.store_dir is not None and fresh:
            # the PRIMARY SOURCE (spec §46): every turn crossing the exactly-once boundary is
            # captured verbatim, append-only — the pool and wiki are derived state; this is the
            # ground truth they could be re-derived from
            from .persist import append_turns

            append_turns(self.store_dir, fresh)
        for cand in candidates:
            self.compiler.insert(cand)
        self._passes += 1
        cleanup = self._passes % self.cleanup_every == 0  # cross-page work only on the cadence
        promoted = self.compiler.housekeep(cleanup=cleanup)
        self.compiler.last_report.backlog = self.backlog()
        if self.store_dir is not None:
            from .persist import save_store

            save_store(self.store, self.store_dir)  # spec §22: the mind hits disk every pass
        return promoted

    def _worker_loop(self) -> None:
        """Drain the un-compiled tail off the interactive path (spec §25-26)."""
        failures = 0
        while True:
            self._wake.wait()
            self._wake.clear()
            while self.backlog() > 0:
                try:
                    self.consolidate()
                    failures = 0
                except Exception as exc:  # marker didn't advance — nothing is lost
                    failures += 1
                    self.compiler.last_report = CompileReport(
                        backlog=self.backlog(), error=str(exc)
                    )
                    if self.on_compiled:
                        self.on_compiled()
                    if failures >= 3:
                        break  # park; the next turn (or flush) re-wakes and retries
                    time.sleep(self.retry_backoff * (2 ** (failures - 1)))
                    continue
                if self.on_compiled:
                    self.on_compiled()

    def flush(self, timeout: float = 60.0) -> bool:
        """Wait for the backlog to drain; True if it did. No-op when synchronous."""
        if not self.background:
            return True
        deadline = time.time() + timeout
        while self.backlog() > 0 and time.time() < deadline:
            self._wake.set()  # re-wakes a worker parked after repeated failures
            time.sleep(0.05)
        return self.backlog() == 0

    def cleanup(self) -> None:
        """Force a full cross-page cleanup pass now (fusion + conflict detection) — spec §41."""
        self.compiler.housekeep(cleanup=True)
        if self.store_dir is not None:
            from .persist import save_store

            save_store(self.store, self.store_dir)

    def wipe(self) -> None:
        """Erase the whole mind — buffer, store, and (if persisted) the files on disk."""
        self.store.pool.clear()
        self.store.clean.clear()
        self.forget()
        if self.store_dir is not None:
            from .persist import erase

            erase(self.store_dir)

    def turn(self, text: str) -> Response:
        # a turn following a question the mind asked IS the answer to it (spec §39) — mark it
        # answered; the fact itself lands through the normal extract → compete pipeline
        if self._asked is not None and self._asked.status == "asked":
            self._asked.status = "answered"
            self._asked.answered_at = time.time()
        self._asked = None
        self.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        if self.background:
            self._wake.set()  # the answer never waits for the slow brain (spec §24)
        else:
            self.consolidate()
        if _is_question(text):
            resp = self.runtime.respond(text, self.buffer)
        else:
            # a statement, not a question: acknowledge (no retrieval, no phrasing call) — the deep
            # brain stores it in the background. Cheaper, and no more "I don't know" to a statement.
            resp = Response(answer="OK", why="statement - the deep brain will store it", used=[])
        self.last_response = resp
        return resp

    def take_question(self):
        """The fast brain asks the oldest pending question (spec §38), marking it asked. One only."""
        pending = self.store.pending_questions()
        if not pending:
            return None
        q = pending[0]
        q.status = "asked"
        q.asked_at = time.time()
        self._asked = q
        return q

    def forget(self) -> None:
        self.buffer.clear()
        self.compiled_upto = 0

    def load_profile(self, mind_dir: str) -> None:
        """Swap the whole mind to another profile's home (spec §34). Between turns only."""
        from .persist import load_store

        self.flush(timeout=120)  # let any in-flight compilation finish into the current mind
        if self.store_dir is not None:
            from .persist import save_store

            save_store(self.store, self.store_dir)
        new = load_store(mind_dir)  # corrupt file raises here, loudly — never a silent empty mind
        self.store = new
        self.compiler.store = new
        self.runtime.store = new
        self.store_dir = mind_dir
        self.forget()  # a new person, a fresh conversation


def _timed(fn, show: bool):
    """Run `fn` while showing an elapsed-seconds indicator; Ctrl+C cancels the wait.

    The work runs in a daemon thread so the main thread stays responsive to Ctrl+C. On cancel
    the in-flight call is abandoned (its result discarded); the session stays usable.
    """
    if not show:
        return fn()
    import threading

    outcome: dict = {}

    def work():
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # delivered to the caller below
            outcome["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    start = time.time()
    worker.start()
    try:
        while worker.is_alive():
            worker.join(0.25)
            if worker.is_alive():
                print(f"\r  thinking... {time.time() - start:3.0f}s  (Ctrl+C to cancel)", end="", flush=True)
    finally:
        print("\r" + " " * 45 + "\r", end="", flush=True)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


PANEL_HEIGHT = 9              # the two brain boxes
BAR_HEIGHT = 1               # the posture strip above them


def _calls_panel_height(session: Session) -> int:
    """Total rows the live-call debug panel occupies, or 0 if hidden. Cloud-only (the fakes ask
    nothing); `session.calls_lines` (0/5/10/15/20 via /debug) sets its content depth."""
    if not session.cloud or session.calls_lines <= 0:
        return 0
    return session.calls_lines + 2  # + top/bottom border


def _header_height(session: Session) -> int:
    """Rows the pinned header occupies — grows/shrinks with the debug panel's configured depth."""
    return BAR_HEIGHT + PANEL_HEIGHT + _calls_panel_height(session)


def _print_cockpit(console, session: Session) -> None:
    """The pinned header: posture bar, deep brain LEFT / fast brain RIGHT, then (cloud) the
    live text we're sending the models."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .view import calls_panel, deep_panel, fast_panel, status_bar

    if session.running_note:  # a metric is running — the header says so (spec: visible tests)
        bar = "> RUNNING METRIC - " + session.running_note
        style = "bold black on yellow"
    else:
        bar = status_bar(session.backend_label, session.backend_detail, session.offdevice)
        style = "bold white on red" if session.offdevice else "bold white on green4"
    line = bar[: console.width].ljust(console.width)  # exactly one full-width row
    console.print(Text(line, style=style), no_wrap=True, overflow="crop")

    resp = session.last_response
    dm, fm = session.deep_meter, session.fast_meter
    deep = deep_panel(
        session.compiler.last_report, session.store, dm.total, dm.last_hour(),
        pending_questions=len(session.store.pending_questions()),
    )
    fast = fast_panel(resp.trace if resp else None, resp, session.buffer, fm.total, fm.last_hour())

    def _model_name(brain) -> str:
        m = getattr(brain, "model", None)
        return m.split("/")[-1] if isinstance(m, str) and m else "fakes"

    deep_title = f"deep brain · {_model_name(session.slow)}"
    fast_title = f"fast brain · {_model_name(getattr(session.runtime, 'model', None))}"
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Panel(deep, title=deep_title, height=PANEL_HEIGHT),
        Panel(fast, title=fast_title, height=PANEL_HEIGHT),
    )
    console.print(grid)
    calls_h = _calls_panel_height(session)
    if calls_h:  # the live wire: what we're actually asking the models, right now (/debug)
        from . import calllog

        console.print(
            Panel(
                calls_panel(calllog.recent()),
                title=f"asking the models - live  (/debug · {session.calls_lines} lines)",
                height=calls_h,
            )
        )


def _enable_vt() -> bool:
    """Switch the Windows console into VT-escape mode; True if escapes will be honoured.

    Windows Terminal has it on already; classic conhost (plain PowerShell windows) needs
    SetConsoleMode. Elsewhere (non-Windows) VT is a given.
    """
    import os

    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # stdout
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _paint_header(console, session: Session) -> None:
    """Repaint the pinned panels in place, leaving the cursor where it was."""
    with console.capture() as cap:
        _print_cockpit(console, session)
    sys.stdout.write("\x1b7\x1b[1;1H")  # save cursor, jump to the top-left
    sys.stdout.write(cap.get())
    sys.stdout.write("\x1b8")  # restore cursor into the scroll region
    sys.stdout.flush()


def _enter_cockpit_screen(console, session: Session) -> None:
    """Pin the panels: clear, set the scroll region to the rows BELOW the header (VT DECSTBM) —
    conversation scrolls up and disappears underneath the boxes; the header never moves."""
    import atexit

    rows = console.size.height
    sys.stdout.write("\x1b[2J")                        # clear screen
    sys.stdout.write(f"\x1b[{_header_height(session) + 1};{rows}r")  # scrolling below the header
    sys.stdout.write(f"\x1b[{rows};1H")                # cursor to the bottom line
    sys.stdout.flush()
    # belt-and-braces: a constrained scroll region left behind (crash, window close, kill) makes
    # the NEXT program's cursor maths go negative and crashes PSReadLine — always restore, even
    # when the REPL's own try/finally never runs
    atexit.register(_exit_cockpit_screen)
    _paint_header(console, session)


def _exit_cockpit_screen() -> None:
    sys.stdout.write("\x1b[r\n")  # restore full-screen scrolling
    sys.stdout.flush()


def _watch_compile(console, session: Session) -> None:
    """After a statement, let the user WATCH the deep brain compile it: repaint the header on the
    MAIN thread (safe) every ~0.4s while the backlog drains, so the extract call appears in the
    live panel and the backlog counts down to zero. Ctrl-C bails to the prompt (the compile keeps
    running in the background). Only the next prompt waits — the answer was already given."""
    deadline = time.time() + 45  # a hard cap so a slow/rate-limited model never hangs the prompt
    try:
        while session.backlog() > 0 and time.time() < deadline:
            session._wake.set()  # make sure the worker is actually chewing
            _paint_header(console, session)
            time.sleep(0.4)
        _paint_header(console, session)  # final frame: backlog 0, the last call still shown
    except KeyboardInterrupt:
        print("  (watching stopped - compile continues in the background)")


def _clear_screen(console, session: Session, pinned: bool) -> None:
    """/clear — wipe the conversation area and repaint. Keeps the mind (that's /forget); doubles
    as a refresh, since it redraws the header with the current backlog and live-call panel."""
    if pinned and console is not None:
        rows = console.size.height
        sys.stdout.write("\x1b[2J")                                        # clear everything
        sys.stdout.write(f"\x1b[{_header_height(session) + 1};{rows}r")    # re-arm scroll region
        sys.stdout.write(f"\x1b[{rows};1H")                                # cursor to the bottom
        sys.stdout.flush()
        _paint_header(console, session)
    else:
        sys.stdout.write("\x1b[2J\x1b[H")  # plain clear + home
        sys.stdout.flush()
        if console is not None:
            _print_cockpit(console, session)


def _wait_for_reset(console, vt_ok: bool, reset_at: float) -> bool:
    """Non-interactive countdown header until the rate limit resets. True when it passes (retry),
    False if the user quits with Ctrl+C. The app 'starts up' but waits instead of dying."""
    pinned = console is not None and vt_ok and getattr(console, "legacy_windows", False) is False
    if pinned:
        sys.stdout.write("\x1b[2J")  # clear; the countdown owns the whole screen
        sys.stdout.flush()
    try:
        while True:
            remaining = reset_at - time.time()
            if remaining <= 0:
                print()  # move off the countdown line
                return True
            h, rem = divmod(int(remaining), 3600)
            m, s = divmod(rem, 60)
            bar = (
                f"! RATE-LIMITED - free tier daily limit spent - "
                f"resets in {h:02d}:{m:02d}:{s:02d}  (Ctrl+C to quit, or run offline: kaineros)"
            )
            if pinned:
                line = bar[: console.width].ljust(console.width)
                from rich.text import Text

                sys.stdout.write("\x1b7\x1b[1;1H")  # save cursor, home
                console.print(Text(line, style="bold white on red"), no_wrap=True, overflow="crop", end="")
                sys.stdout.write("\x1b8")  # restore cursor
                sys.stdout.flush()
            else:
                print("\r" + bar[:120].ljust(120), end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        return False


def _cmd_model(session: Session, args: list[str]) -> None:
    """/model — show or switch the per-brain models (arguments-first, picker as fallback)."""
    if not session.cloud:
        print("  /model needs --cloud or --openrouter (the fakes have no models to pick)")
        return
    judge, slow, fast = session.compiler.judge, session.slow, session.runtime.model
    if not args:
        print(f"  deep: {slow.model} (extractor + judge)   fast: {fast.model}")
        print("  usage: /model deep|fast [model-id]")
        return
    role = args[0].lower()
    if role not in ("deep", "fast"):
        print("  usage: /model deep|fast [model-id]")
        return
    if len(args) >= 2:
        name = args[1]
    else:
        ids = slow.list_models()
        for i, mid in enumerate(ids, 1):
            print(f"  {i}) {mid}")
        pick = input("  pick> ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(ids):
            name = ids[int(pick) - 1]
        elif pick in ids:
            name = pick
        else:
            print("  (unchanged)")
            return
    if role == "deep":
        judge.model = name
        slow.model = name
    else:
        fast.model = name
    print(f"  {role} -> {name}")


def _run_metric_scenario(session: Session, sc: dict, pinned: bool, console, correct) -> None:
    """One conflict scenario, live: feed it turn by turn, watch the deep brain review it down to
    zero backlog, then probe and score (AA / SEH / CRS / cost)."""
    session.wipe()  # fresh slate — the point is to watch this scenario build from nothing
    ctype = sc.get("conflict_type", "plain")
    turns = sc["turns"]
    deep0, fast0 = session.deep_meter.total, session.fast_meter.total
    print(f"\n== metric: {ctype} - {sc['name']}")
    for i, text in enumerate(turns, 1):
        session.running_note = f"{ctype}: feeding turn {i}/{len(turns)}  ({session.backlog()} to review)"
        if pinned:
            _paint_header(console, session)
        print("you> " + text)
        resp = session.turn(text)
        print("mind> " + resp.answer)
        if pinned:
            _paint_header(console, session)
    # wait for the deep brain to review everything — the "to review" count drains to zero.
    # visible progress + a stall/overall timeout so it never looks (or actually) hung on a slow
    # or rate-limited free tier.
    if session.backlog() > 0:
        which = session.backend_detail or "the deep model"
        print(f"  (deep brain reviewing {session.backlog()} turn(s) via {which}...)")
    deadline = time.time() + 300
    last = session.backlog()
    stalled_since = time.time()
    while session.backlog() > 0 and time.time() < deadline:
        session.running_note = f"{ctype}: reviewing... {session.backlog()} left"
        if pinned:
            _paint_header(console, session)
        session._wake.set()
        time.sleep(0.5)
        now_backlog = session.backlog()
        if now_backlog < last:  # made progress
            print(f"     ...{now_backlog} left")
            last, stalled_since = now_backlog, time.time()
        elif time.time() - stalled_since > 90:  # no progress for 90s -> worker likely parked
            print("  (review stalled - the deep model may be rate-limited or slow; scoring what compiled)")
            break
    if session.backlog() > 0:
        print(f"  (proceeding with {session.backlog()} turn(s) still uncompiled - partial score)")
    session.running_note = f"{ctype}: scoring"
    if pinned:
        _paint_header(console, session)

    aa = seh = probes = 0
    for exp in sc.get("expect", []):
        if "question" not in exp:
            continue
        probes += 1
        kws = [k.lower() for k in exp["keywords"]]
        absent = [a.lower() for a in exp.get("absent", [])]
        r = session.runtime.respond(exp["question"], [])
        ok = correct(r, kws, absent)
        aa += ok
        seh += any(all(k in p.content.lower() for k in kws) for p in r.used)
        print(f"probe: {exp['question']}")
        print(f"   -> {r.answer}   [{'OK' if ok else 'MISS'}]")
    recognized = bool(session.store.questions) or any(len(p) > 1 for p in session.store.pool.values())
    dtok, ftok = session.deep_meter.total - deep0, session.fast_meter.total - fast0
    # if turns never compiled (model rate-limited/unavailable), the mind never learned the facts -
    # a MISS here is NOT a mechanism failure, so mark it inconclusive rather than scoring it
    uncompiled = session.backlog()
    if uncompiled and not session.store.pages():
        print(
            f"  INCONCLUSIVE [{ctype}] - {uncompiled} turn(s) never compiled (deep model "
            f"unavailable/rate-limited); the mind was empty, so this is NOT a real score. "
            f"cost {dtok:,}+{ftok:,} tok"
        )
        return
    line = f"  SCORE [{ctype}] AA {aa}/{probes}"
    if probes:
        line += f" ({aa / probes:.2f})"
    line += f" | SEH {seh}/{probes}"
    if uncompiled:
        line += f" | PARTIAL ({uncompiled} turn(s) uncompiled)"
    if ctype == "static":
        if sc.get("expect_conflict", True):
            line += f" | CRS {'1.00' if recognized else '0.00'} (field ceiling ~0.25)"
        else:
            # ground truth says there is NO genuine conflict - not recognising one is correct,
            # and flagging one (a queued question) would be the error
            flagged = len(session.store.questions)
            line += f" | CRS n/a (no real conflict) | false-flag {flagged}"
    if ctype == "conditional":
        line += f" | false-Q {len(session.store.questions)}"
    line += f" | cost {dtok:,}+{ftok:,} tok"
    print(line)


# metric families: name -> (corpus file, conflict-type submenu or None, one-line description)
_METRIC_FAMILIES = {
    "conflict": ("conflicts", ["static", "dynamic", "conditional", "all"],
                 "serve the right fact when facts contradict"),
    "conflict-hard": ("conflicts-hard", ["static", "dynamic", "conditional", "all"],
                      "harder conflicts - implicit, lexically-similar, idiomatic (tries to break it)"),
    "retrieval": ("retrieval", None, "pull the right page from a crowded, distractor-heavy mind"),
}


def _pick(prompt: str, options: list[str], given: str | None) -> str | None:
    if given is not None:
        return given if given in options else None
    print(f"  {prompt}")
    for i, o in enumerate(options, 1):
        print(f"    {i}) {o}")
    raw = input("  > ").strip().lower()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return raw if raw in options else None


def _cmd_metrics(session: Session, args: list[str], pinned: bool, console) -> None:
    """/metrics — pick a metric family, then a metric, and run its benchmark live and scored."""
    from .evals import _correct, corpus_path, load_corpus

    fam_names = list(_METRIC_FAMILIES)
    if args:
        family = args[0].lower()
    else:
        print("  which metric family?")
        for i, n in enumerate(fam_names, 1):
            print(f"    {i}) {n} - {_METRIC_FAMILIES[n][2]}")
        raw = input("  > ").strip().lower()
        family = fam_names[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(fam_names) else raw
    if family not in _METRIC_FAMILIES:
        print("  (cancelled)")
        return
    corpus, submenu, _ = _METRIC_FAMILIES[family]
    scenarios = load_corpus(corpus_path(corpus))

    tag = family
    if submenu:  # a second level (conflict: static / dynamic / conditional / all)
        sub = _pick(f"which {family} metric?", submenu, args[1].lower() if len(args) > 1 else None)
        if sub is None:
            print("  (cancelled)")
            return
        if sub != "all":
            scenarios = [s for s in scenarios if s.get("conflict_type") == sub]
        tag = f"{family}-{sub}"
    if not scenarios:
        print("  no scenarios for that metric")
        return

    from . import profiles

    prev = session.profile_name
    session.load_profile(profiles.mind_dir(f"metrics-{tag}"))  # a throwaway mind, never your own
    session.profile_name = f"metrics-{tag}"
    try:
        for sc in scenarios:
            _run_metric_scenario(session, sc, pinned, console, _correct)
    except KeyboardInterrupt:
        print("\n  (metrics cancelled)")
    finally:
        session.running_note = None
        if pinned:
            _paint_header(console, session)
    print(f"\n  done - ran in profile 'metrics-{tag}'. /profile {prev} to return to your mind")


def _cmd_prompt(_session: Session, args: list[str]) -> None:
    """/prompt — print the static system prompts + schemas we send the models, so the rules the
    live panel doesn't show (they're in the system prompt) are inspectable on demand."""
    from .cloud import (
        EXTRACT_SCHEMA,
        EXTRACT_SYSTEM,
        JUDGE_SYSTEM,
        PHRASE_SYSTEM,
        better_prompt,
        conflicts_prompt,
        phrase_user,
        same_account_prompt,
        same_claim_prompt,
    )
    from .schema import Candidate, Page, Provenance

    topics = ["extract", "judge", "phrase", "all"]
    which = args[0].lower() if args else None
    if which not in topics:
        print("  which prompt?  " + " / ".join(topics[:-1]) + "  (or 'all')")
        raw = input("  > ").strip().lower()
        which = raw if raw in topics else None
    if which is None:
        print("  (cancelled)")
        return

    import json as _json

    prov = Provenance(stated=True)
    a = Candidate(gene="user.food.pref", content="The user loves Thai food.", provenance=prov,
                  tags=("food", "cuisine"))
    b = Candidate(gene="user.food.pref", content="The user now prefers Japanese food.",
                  provenance=prov, tags=("food", "cuisine"))
    page = Page(gene="user.home_city", content="The user lives in St Leonards.", provenance=prov)

    def section(title: str, system: str, user: str) -> None:
        print(f"\n===== {title} =====")
        print("--- system ---")
        print(system)
        print("--- user (example) ---")
        print(user)

    if which in ("extract", "all"):
        section("EXTRACT (deep)", EXTRACT_SYSTEM,
                "Conversation turns:\n[0] I drive a Tesla and I'm allergic to nuts"
                "\n\n(+ the JSON schema below, appended for backends without native schema forcing)")
        print("--- schema ---")
        print(_json.dumps(EXTRACT_SCHEMA, indent=2))
    if which in ("judge", "all"):
        print(f"\n===== JUDGE (deep) =====\n--- system ---\n{JUDGE_SYSTEM}")
        print("--- the four questions (example A/B) ---")
        print("\n[better]\n" + better_prompt("user.food.pref", a, b))
        print("\n[same_claim]\n" + same_claim_prompt("user.food.pref", a, b))
        print("\n[same_account]\n" + same_account_prompt("user.food.pref", a, b))
        print("\n[conflicts]\n" + conflicts_prompt(a, b))
    if which in ("phrase", "all"):
        section("PHRASE (fast)", PHRASE_SYSTEM, phrase_user("Where do I live?", [page], []))
    print()


_DEBUG_CHOICES = [("off", 0), ("5", 5), ("10", 10), ("15", 15), ("20", 20)]


def _cmd_debug(session: Session, args: list[str]) -> bool:
    """/debug — set the live model-call panel's depth (off / 5 / 10 / 15 / 20 lines).

    Returns True if the value changed (the caller re-arms the pinned scroll region + repaints,
    since the header's height moved)."""
    if not session.cloud:
        print("  the debug panel shows model calls - only shown on a cloud backend "
              "(--claude / --openrouter). Nothing to configure offline.")
        return False
    labels = [f"{name} lines" if name != "off" else "off (hide the panel)" for name, _ in _DEBUG_CHOICES]
    given = args[0].lower() if args else None
    if given is None:
        print(f"  live model-call panel is {session.calls_lines} lines. Set depth:")
        for i, lab in enumerate(labels, 1):
            print(f"    {i}) {lab}")
        given = input("  > ").strip().lower()
    value = None
    if given.isdigit() and 1 <= int(given) <= len(_DEBUG_CHOICES):
        value = _DEBUG_CHOICES[int(given) - 1][1]  # menu position
    else:
        for name, v in _DEBUG_CHOICES:  # or a direct arg: /debug 10, /debug off
            if given == name:
                value = v
    if value is None:
        print("  (unchanged)")
        return False
    changed = value != session.calls_lines
    session.calls_lines = value
    print(f"  debug panel: {'hidden' if value == 0 else f'{value} lines'}")
    return changed


def _cmd_bench(session: Session, args: list[str], pinned: bool, console) -> None:
    """/bench — run an external benchmark slice in a quarantined profile.

    'sample' is the checked-in fixture (offline, seconds); 'slice' is a slice of the real
    PersonaMem dataset (needs the data downloaded, spends deep tokens - see the printed hint).
    """
    from .bench import personamem

    benches = {
        "sample": "tiny built-in fixture (5 turns, 4 probes) - a smoke test, cheap",
        "sample-big": "bigger built-in fixture (8 sessions, ~20 turns, 10 probes) - a real workout",
        "slice-smoke": "real PersonaMem, TINY slice (3 sessions, 2 questions) - de-risks the loader",
        "slice": "real PersonaMem, full 32k persona (~15-30 min on the sub, Ctrl-C-able) - the real thing",
    }
    names = list(benches)
    pos = [a for a in args if "=" not in a]  # positional args; key=value flags (groom=) excluded
    if pos:
        which = pos[0].lower()
        if which.isdigit() and 1 <= int(which) <= len(names):
            which = names[int(which) - 1]
    else:
        print("  which benchmark?")
        for i, n in enumerate(names, 1):
            print(f"    {i}) {n} - {benches[n]}")
        raw = input("  > ").strip().lower()
        which = names[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(names) else raw
    if which not in benches:
        print("  (cancelled)")
        return
    try:
        if which == "sample":
            sl = personamem.load_sample()
        elif which == "sample-big":
            sl = personamem.load_sample(personamem.SAMPLE_BIG)
        elif which == "slice-smoke":
            # fixed tiny params, no prompts — just prove the real-data loader works end to end
            sl = personamem.load_dataset_slice(limit=2, max_sessions=3)
        else:  # slice — the full persona
            persona = pos[1] if len(pos) > 1 else (input("  persona id (blank = first)> ").strip() or None)
            raw = pos[2] if len(pos) > 2 else input("  how many questions? [10]> ").strip()
            limit = int(raw) if raw.isdigit() else 10
            sl = personamem.load_dataset_slice(persona=persona, limit=limit)
    except (RuntimeError, OSError) as exc:
        print(f"  {exc}")
        return

    from . import profiles

    prev = session.profile_name
    target = f"bench-{sl.name}"
    session.load_profile(profiles.mind_dir(target))  # a throwaway mind, never your own
    session.profile_name = target
    session.wipe()

    # checkpoint the (expensive, deterministic) ingest so you can iterate on grooming fast:
    # `ingest=cached` boots from the saved post-ingest snapshot and skips extraction entirely.
    ckpt = str(Path(profiles.mind_dir(target)).parent / "ingest-checkpoint")
    skip_ingest = False
    if any(a == "ingest=cached" for a in args):
        from .persist import load_store

        if (Path(ckpt) / "pages.json").exists():
            loaded = load_store(ckpt)
            session.store = loaded
            session.compiler.store = loaded
            session.runtime.store = loaded
            skip_ingest = True
            print(f"  (ingest=cached: booting from saved checkpoint at {ckpt})")
        else:
            print("  (ingest=cached requested but no checkpoint yet - ingesting fresh + saving one)")

    d0, f0 = session.deep_meter.total, session.fast_meter.total
    run_start = time.time()  # wall-clock for the whole run (it's subprocess-latency bound)
    from . import version as _kaineros_version

    build = _kaineros_version()
    n_turns = sum(len(s) for s in sl.sessions)
    print(f"  kaineros {build}")
    print(f"  {sl.name}: {len(sl.sessions)} session(s), {n_turns} turn(s), {len(sl.probes)} probe(s)")

    def drain() -> None:  # background worker: wait visibly, with stall detection (as /metrics)
        if session.backlog() == 0:
            return
        which_model = session.backend_detail or "the deep model"
        print(f"  (deep brain reviewing {session.backlog()} turn(s) via {which_model}...)")
        last, stalled_since = session.backlog(), time.time()
        deadline = time.time() + 600
        while session.backlog() > 0 and time.time() < deadline:
            session.running_note = f"bench: reviewing... {session.backlog()} left"
            if pinned:
                _paint_header(console, session)
            session._wake.set()
            time.sleep(0.5)
            now = session.backlog()
            if now < last:
                print(f"     ...{now} left")
                last, stalled_since = now, time.time()
            elif time.time() - stalled_since > 90:
                print("  (review stalled - the deep model may be rate-limited or slow; continuing)")
                break

    # heartbeat: a real deep model means minutes-long silent stretches (each judge comparison is
    # its own model call) — tick elapsed time + token spend so it never looks hung. Plain prints
    # only; NEVER repaint the pinned header off-thread (that's the PSReadLine crash).
    beat_stop = threading.Event()
    last_out = [time.time()]

    def _say(s: str) -> None:
        last_out[0] = time.time()
        print(f"  {s}")

    def _beat() -> None:
        from . import calllog

        start = time.time()
        while not beat_stop.wait(3.0):
            # a single session's compile can fire MANY judge calls with no % movement — tick the
            # running deep-call count so a busy cleanup pass never reads as hung
            if time.time() - last_out[0] >= 8:
                last_out[0] = time.time()
                print(f"     ...still compiling ({time.time() - start:.0f}s, "
                      f"{calllog.deep_calls()} deep calls, {session.deep_meter.total - d0:,} tok)")

    # watch the calls live: bench compiles synchronously on THIS (main) thread, so the call log's
    # observer fires here and can safely repaint the debug panel between each request/reply. When
    # the panel is hidden, fall back to the background heartbeat instead (they'd fight over stdout).
    watch_live = pinned and session.cloud and session.calls_lines > 0
    main_thread = threading.main_thread()

    def _observe() -> None:
        if threading.current_thread() is main_thread:  # only the main thread may touch the cursor
            _paint_header(console, session)

    rows: list[dict] = []
    stats: dict = {}
    beater = None
    if watch_live:
        from . import calllog

        calllog.set_observer(_observe)
    elif session.cloud:
        beater = threading.Thread(target=_beat, daemon=True)
        beater.start()
    try:
        # how long the deep brain THINKS (grooms) before probing, in seconds (spec §49). Default
        # 60; make it long to simulate an overnight think, e.g. `/bench 4 think=600`.
        think = next((float(a.split("=")[1]) for a in args if a.startswith("think=")
                      and a.split("=")[1].replace(".", "", 1).isdigit()), 60.0)
        # routek=N: how many pages the fast brain retrieves per probe (default 3). Higher surfaces
        # the needed fact when the mind has many pages, at a slightly longer fast-brain prompt.
        routek = next((int(a.split("=")[1]) for a in args if a.startswith("routek=")
                       and a.split("=")[1].isdigit()), None)
        if routek:
            session.runtime.route_k = routek
            print(f"  (retrieving top-{routek} pages per probe)")
        rows, stats = personamem.run_slice(
            session, sl, say=_say, wait_idle=drain if session.background else None,
            think_seconds=think, skip_ingest=skip_ingest,
            checkpoint_dir=None if skip_ingest else ckpt,
        )
    except KeyboardInterrupt:
        print("\n  (bench cancelled - scoring what ran)")
    finally:
        if watch_live:
            from . import calllog

            calllog.set_observer(None)
        beat_stop.set()
        if beater is not None:
            beater.join(timeout=6)
        session.running_note = None
        if pinned:
            _paint_header(console, session)

    for r in rows:
        mark = "OK" if r["correct"] else ("ABSTAIN" if r["abstained"] else "MISS")
        used = f", read {len(r['used'])} page(s): {', '.join(r['used'])}" if r.get("used") else ""
        print(f"probe [{r['type']}]: expected {r['expected']}")
        print(f"   -> {r['answer']}   [{mark}{used}]")
    print()
    for line in personamem.summarise(rows, stats):
        print("  " + line)
    dtok, ftok = session.deep_meter.total - d0, session.fast_meter.total - f0
    elapsed = time.time() - run_start
    calls = (stats.get("judge_calls", {}) or {}).get("total", 0) + len(sl.sessions)  # judge + extracts
    per = f", ~{elapsed / calls:.1f}s/call" if calls else ""
    mins = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
    right = sum(r["correct"] for r in rows)
    score = f"{right}/{len(rows)} = {right / len(rows):.2f}" if rows else "no probes"
    print(f"  cost {dtok:,} deep + {ftok:,} fast tok")
    ph = stats.get("phase_seconds") or {}
    if ph:
        print(f"  split ingest {ph.get('ingest', 0)}s · groom {ph.get('groom', 0)}s · "
              f"probe {ph.get('probe', 0)}s  ({'cached ingest' if skip_ingest else 'fresh ingest'})")
    print(f"  time {mins}  ({calls} deep calls{per} - wall-clock is bound by sequential claude -p)")
    print(f"  build kaineros {build}")
    out = Path(__file__).resolve().parents[2] / "bench-results"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"{sl.name}-{stamp}.jsonl"
    def _mname(brain) -> str:
        m = getattr(brain, "model", None)
        return m.split("/")[-1] if isinstance(m, str) and m else "fakes"

    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {
            "build": build, "score": score, "think": think,
            "route_k": session.runtime.route_k,
            "fast": _mname(getattr(session.runtime, "model", None)),
            "deep": _mname(session.slow)}}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"  rows -> {path}")
    from . import calllog

    if calllog.path() is not None:
        print(f"  calls -> {calllog.path()}  (raw deep/fast model transcript)")
    if session.store_dir is not None:
        wiki = Path(session.store_dir) / "kainome"
        print(f"  wiki -> {wiki}")
        if wiki.exists():  # ctrl+clickable in Windows Terminal / VS Code
            index = wiki / "index.md"
            print(f"          {(index if index.exists() else wiki).as_uri()}  (ctrl+click to open)")
    print(f"  done in {mins}  ({score}) - ran in profile '{target}'. "
          f"/profile {prev} to return to your mind")


def _cmd_persona(session: Session, args: list[str], pinned: bool, console) -> None:
    """/persona <name> — wipe a dedicated profile and rebuild it from a scripted conversation,
    so you watch a wiki form from nothing. Never touches the user's own profiles."""
    from . import persona, profiles

    if not args:
        print("  personas: " + (", ".join(persona.list_personas()) or "(none)"))
        print("  /persona <name>  - build a fresh wiki (in profile 'persona-<name>')")
        return
    name = args[0]
    try:
        convo = persona.load(name)
    except RuntimeError as exc:
        print(f"  {exc}")
        return
    target = f"persona-{name}"
    session.load_profile(profiles.mind_dir(target))
    session.profile_name = target
    session.wipe()  # clean slate — the whole point is to watch it build from nothing
    turns = convo["turns"]
    print(f"  building '{name}' fresh in profile '{target}' - {len(turns)} turns, watch it grow")
    if pinned:
        _paint_header(console, session)
    for text in turns:
        print("you> " + text)
        resp = session.turn(text)
        session.flush()  # let the deep brain finish this turn before the next (see it build)
        if pinned:
            _paint_header(console, session)
        print("mind> " + resp.answer)
    print(
        f"  done - {len(session.store.pages())} pages "
        f"(deep {session.deep_meter.total:,} tok). /notebook to read, /profile default to leave"
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "evals":  # `kaineros evals [--free|--openrouter|--fakes|--only X]`
        from .evals import main as evals_main

        return evals_main(args[1:])
    if args and args[0] == "persona":  # `kaineros persona <name> [--free|--openrouter|--fakes]`
        from .persona import main as persona_main

        return persona_main(args[1:])
    plain = "--plain" in args or not sys.stdout.isatty()

    # the mind's home on disk (spec §20): default under LOCALAPPDATA, --mind / env override
    import os
    from pathlib import Path

    from . import profiles

    if "--mind" in args:  # explicit path escape hatch (tests, one-offs)
        mind = args[args.index("--mind") + 1]
        profile = "(custom)"
    elif os.environ.get("KAINEROS_MIND"):  # explicit env path
        mind = os.environ["KAINEROS_MIND"]
        profile = "(custom)"
    elif "--profile" in args:  # a named profile — its own wiki (spec §34)
        profile = args[args.index("--profile") + 1]
        mind = profiles.mind_dir(profile)
    else:  # no name given -> the 'default' profile (migrating any pre-profiles mind into it)
        profile = profiles.DEFAULT
        mind = profiles.default_mind_dir()

    console = None
    vt_ok = False
    if not plain:
        # VT mode must be on BEFORE rich builds its Console: otherwise rich decides this is a
        # legacy Windows console and renders via a path whose captured output can't be replayed
        # into the pinned header
        vt_ok = _enable_vt()
        try:
            from rich.console import Console

            console = Console()
        except ImportError:
            print("(rich not installed - running plain; pip install rich for the cockpit)")

    def _build() -> Session:
        if "--cloud" in args:
            from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

            preflight()
            s = Session(store_dir=mind, background=True)
            s.compiler.judge = CloudJudge(meter=s.deep_meter)
            s.slow = CloudSlowModel(meter=s.deep_meter)
            s.compiler.summariser = s.slow  # enable consolidation (spec §50)
            s.runtime.model = CloudFastModel(meter=s.fast_meter)
            s.cloud = True
            s.backend_label, s.backend_detail, s.offdevice = "DEBUG - CLOUD", "Claude API", True
            return s
        if "--claude" in args:
            # mixed build: deep brain on the Claude subscription (claude -p), fast brain on the
            # OpenRouter free tier. The deep brain is off the interactive path, so the seconds
            # of CLI startup per call are invisible; the fast brain stays a quick HTTP call.
            #
            # --fast=<model> points the fast brain at a specific OpenRouter model — used to
            # STAND IN for a local model (e.g. --fast=qwen/qwen3-32b, a dense 32B that fits in
            # 64GB): it answers "would the model I'd run locally clear this?" before building
            # local inference. NB: a non-free model spends OpenRouter credits (cents for probes).
            from .claude_cli import DEEP_MODEL as CLI_DEEP
            from .claude_cli import ClaudeCLIJudge, ClaudeCLISlowModel
            from .claude_cli import preflight as cli_preflight
            from .openrouter import FREE_FAST_MODEL, OpenRouterFastModel, ensure_key
            from .openrouter import preflight as or_preflight

            fast = next((a.split("=", 1)[1] for a in args if a.startswith("--fast=")), FREE_FAST_MODEL)
            cli_preflight()  # proves the binary + login before we accept any turns
            ensure_key()
            or_preflight(fast)  # RateLimitedError → the countdown loop waits it out
            s = Session(store_dir=mind, background=True, cleanup_every=4)
            s.compiler.judge = ClaudeCLIJudge(meter=s.deep_meter)
            s.slow = ClaudeCLISlowModel(meter=s.deep_meter)
            s.compiler.summariser = s.slow  # enable consolidation (spec §50)
            s.runtime.model = OpenRouterFastModel(fast, meter=s.fast_meter)
            s.cloud = True
            s.backend_label = "DEBUG - CLOUD"
            detail = "free" if fast == FREE_FAST_MODEL else fast.split("/")[-1] + " (local stand-in)"
            s.backend_detail = f"Claude sub ({CLI_DEEP}) + OpenRouter {detail}"
            s.offdevice = True
            return s
        if "--openrouter" in args or "--free" in args:
            from .openrouter import (
                DEEP_MODEL,
                FAST_MODEL,
                FREE_DEEP_MODEL,
                FREE_FAST_MODEL,
                OpenRouterFastModel,
                OpenRouterJudge,
                OpenRouterSlowModel,
                ensure_key,
                preflight,
            )

            deep = FREE_DEEP_MODEL if "--free" in args else DEEP_MODEL
            fast = FREE_FAST_MODEL if "--free" in args else FAST_MODEL
            ensure_key()
            preflight(fast)  # RateLimitedError here → the countdown loop below waits it out
            # real chat: run the expensive cross-page cleanup only every 4th pass (save tokens);
            # /cleanup forces it. Cloud/free models make this matter.
            s = Session(store_dir=mind, background=True, cleanup_every=4)
            s.compiler.judge = OpenRouterJudge(deep, meter=s.deep_meter)
            s.slow = OpenRouterSlowModel(deep, meter=s.deep_meter)
            s.compiler.summariser = s.slow  # enable consolidation (spec §50)
            s.runtime.model = OpenRouterFastModel(fast, meter=s.fast_meter)
            s.cloud = True
            s.backend_label = "DEBUG - CLOUD"
            s.backend_detail = "OpenRouter free tier" if "--free" in args else "OpenRouter"
            s.offdevice = True
            return s
        return Session(store_dir=mind)

    from .openrouter import RateLimitedError

    while True:
        try:
            session = _build()
            if session.cloud:
                # the raw transcript of every deep/fast model call this run (spec: observation
                # only) — one JSONL per app run, next to the profiles
                from . import calllog, profiles as _p

                log_path = calllog.enable(_p._base() / "logs")
                print(f"(call log: {log_path})")
            break
        except RateLimitedError as exc:
            if not _wait_for_reset(console, vt_ok, exc.reset_at):
                return 0  # user quit the wait
            print("free tier reset - starting up...")  # retry the build
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
    # pinned panels on by default where the terminal supports VT (off on legacy consoles, and with
    # --plain). We deliberately do NOT repaint the header from the background worker thread —
    # concurrent cursor writes while the main thread is in input() corrupt the terminal (PSReadLine
    # crash). The header repaints only on the main thread, after each turn.
    pinned = console is not None and vt_ok and not console.legacy_windows and "--no-cockpit" not in args
    if pinned:
        _enter_cockpit_screen(console, session)
    session.profile_name = profile
    from .view import status_bar

    print(status_bar(session.backend_label, session.backend_detail, session.offdevice))
    print(f"(profile: {profile})")
    if session.cloud:
        print(f"(models: deep={session.slow.model}, fast={session.runtime.model.model})")
    print(f"(mind: {mind} - {len(session.store.pages())} pages)")
    print(BANNER)
    try:
      while True:
        try:
            line = input(_prompt(session)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line.startswith("/"):
            cmd = line.split()[0].lower()
            if cmd in ("/quit", "/exit"):
                break
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/notebook":
                pages = session.store.pages()
                if not pages:
                    print("  (empty — nothing promoted yet)")
                for p in pages:
                    print(f"  [{p.gene}] {p.content}")
            elif cmd == "/why":
                why = session.last_response.why if session.last_response else "(no answer yet)"
                print("  " + why)
            elif cmd == "/model":
                _cmd_model(session, line.split()[1:])
            elif cmd == "/questions":
                pend = session.store.pending_questions()
                if not pend:
                    print("  (no open questions)")
                for qq in pend:
                    print("  ? " + qq.text)
            elif cmd == "/cleanup":
                print("  (deep brain: cross-page cleanup - fusion + conflict check...)")
                session.cleanup()
                r = session.compiler.last_report
                print(f"  done - {r.fused} fused, {r.queued} question(s) queued")
            elif cmd == "/metrics":
                _cmd_metrics(session, line.split()[1:], pinned, console)
            elif cmd == "/bench":
                _cmd_bench(session, line.split()[1:], pinned, console)
            elif cmd == "/persona":
                _cmd_persona(session, line.split()[1:], pinned, console)
            elif cmd == "/profile":
                from . import profiles

                pargs = line.split()[1:]
                if not pargs:
                    names = profiles.list_profiles()
                    print(f"  current: {session.profile_name}")
                    print("  profiles: " + (", ".join(names) if names else "(none named yet)"))
                    print("  switch/create: /profile <name>")
                else:
                    name = pargs[0]
                    session.load_profile(profiles.mind_dir(name))
                    session.profile_name = name
                    print(f"  switched to '{name}' - {len(session.store.pages())} pages")
                    if pinned:
                        _paint_header(console, session)
            elif cmd == "/prompt":
                _cmd_prompt(session, line.split()[1:])
            elif cmd == "/clear":
                _clear_screen(console, session, pinned)
            elif cmd == "/debug":
                if _cmd_debug(session, line.split()[1:]) and pinned:
                    _clear_screen(console, session, pinned)  # header height moved: re-arm + repaint
            elif cmd == "/forget":
                if line.split()[1:] == ["all"]:
                    sure = input("  really erase the whole mind from disk? type yes: ").strip()
                    if sure.lower() == "yes":
                        session.wipe()
                        print("  (the mind is erased)")
                    else:
                        print("  (unchanged)")
                else:
                    session.forget()
                    print("  (buffer cleared - /forget all erases the whole mind)")
            else:
                print(f"  unknown command: {cmd}  (try /help)")
            continue

        try:
            resp = _timed(lambda: session.turn(line), show=sys.stdout.isatty())
        except KeyboardInterrupt:
            print("\n  (cancelled - that turn was abandoned; it will be re-read next time)")
            continue
        except RuntimeError as exc:
            print(f"  [model error: {exc}]")
            continue
        if pinned:
            _paint_header(console, session)
        elif console is not None:
            _print_cockpit(console, session)  # console can't pin: panels print inline
        print("mind> " + resp.answer)
        q = session.take_question()  # the mind asks a queued disambiguation, if any (spec §38)
        if q is not None:
            print("mind? " + q.text)
        # with the live panel open, pause after a STATEMENT to watch the deep brain compile it
        # (a question already gave you an answer to act on — don't make you wait to read it)
        if (
            pinned
            and session.background
            and session.calls_lines > 0
            and not _is_question(line)
            and session.backlog() > 0
        ):
            _watch_compile(console, session)

      if session.background and session.backlog() > 0:
          print(f"(compiling {session.backlog()} remaining turn(s) before quitting...)")
          try:
              if not session.flush(timeout=120):
                  print("(some turns could not be compiled - they are lost with this session)")
          except KeyboardInterrupt:
              print("(abandoned - un-compiled turns are lost with this session)")
    finally:
        if pinned:  # ALWAYS restore the terminal, even on a crash — never leave a scroll region
            _exit_cockpit_screen()
    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

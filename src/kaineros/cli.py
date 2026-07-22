"""The two-speed chat shell.

Run with ``kaineros`` or ``python -m kaineros``. Plain text is a conversation turn; lines starting
with ``/`` are slash commands. Wired with the deterministic fakes for now — swap in a cloud/local
model (Slice 5) without touching this file.

The session owns the buffer and the compiled marker (spec §17): each turn, only the not-yet-compiled
tail is handed to the slow model — a turn is extracted exactly once, ever.
"""
from __future__ import annotations

import sys
import threading
import time

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
    "  /model     show or switch models: /model deep|fast [model-id]  (--cloud only)\n"
    "  /profile   list profiles, or /profile <name> to switch (each has its own wiki)\n"
    "  /persona   /persona <name> builds a fresh wiki from a scripted person (watch it grow)\n"
    "  /metrics   run a conflict-benchmark scenario live and score it (AA/SEH/CRS/cost)\n"
    "  /forget    clear the short-term buffer   (/forget all erases the whole mind)\n"
    "  /quit      exit\n"
)


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
    ) -> None:
        self.store_dir = store_dir
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
        self.retry_backoff = 0.5  # base seconds between failed-pass retries (tests shrink it)
        self._wake = threading.Event()
        if background:
            threading.Thread(target=self._worker_loop, daemon=True).start()

    def backlog(self) -> int:
        return len(self.buffer) - self.compiled_upto

    def consolidate(self) -> list[Page]:
        """Run the slow brain over the un-compiled tail only (exactly-once), then housekeep."""
        upto = len(self.buffer)  # snapshot: turns arriving mid-pass belong to the next pass
        fresh = self.buffer[self.compiled_upto : upto]
        candidates = self.slow.extract(fresh)
        # marker advances only after extraction succeeds: a failed/cancelled call must not lose
        # the turns. A re-run may re-extract (at-least-once); housekeeping's dedup merges that.
        self.compiled_upto = upto
        for cand in candidates:
            self.compiler.insert(cand)
        promoted = self.compiler.housekeep()
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
        resp = self.runtime.respond(text, self.buffer)
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
HEADER_HEIGHT = BAR_HEIGHT + PANEL_HEIGHT  # rows the pinned header occupies


def _print_cockpit(console, session: Session) -> None:
    """The pinned header: posture bar on top, then deep brain LEFT / fast brain RIGHT."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .view import deep_panel, fast_panel, status_bar

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
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Panel(deep, title="deep brain - slow, off the clock", height=PANEL_HEIGHT),
        Panel(fast, title="fast brain - this turn", height=PANEL_HEIGHT),
    )
    console.print(grid)


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
    rows = console.size.height
    sys.stdout.write("\x1b[2J")                        # clear screen
    sys.stdout.write(f"\x1b[{HEADER_HEIGHT + 1};{rows}r")  # scrolling only below the header
    sys.stdout.write(f"\x1b[{rows};1H")                # cursor to the bottom line
    sys.stdout.flush()
    _paint_header(console, session)


def _exit_cockpit_screen() -> None:
    sys.stdout.write("\x1b[r\n")  # restore full-screen scrolling
    sys.stdout.flush()


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
    # wait for the deep brain to review everything — the "to review" count drains to zero
    while session.backlog() > 0:
        session.running_note = f"{ctype}: reviewing... {session.backlog()} left"
        if pinned:
            _paint_header(console, session)
        session._wake.set()
        time.sleep(0.3)
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
    line = f"  SCORE [{ctype}] AA {aa}/{probes}"
    if probes:
        line += f" ({aa / probes:.2f})"
    line += f" | SEH {seh}/{probes}"
    if ctype == "static":
        line += f" | CRS {'1.00' if recognized else '0.00'} (field ceiling ~0.25)"
    if ctype == "conditional":
        line += f" | false-Q {len(session.store.questions)}"
    line += f" | cost {dtok:,}+{ftok:,} tok"
    print(line)


def _cmd_metrics(session: Session, args: list[str], pinned: bool, console) -> None:
    """/metrics — pick a conflict type and run its benchmark scenario live, scoring in front of you."""
    from .evals import _correct, corpus_path, load_corpus

    try:
        scenarios = load_corpus(corpus_path("conflicts"))
    except FileNotFoundError:
        print("  no conflicts corpus found")
        return
    types = ["static", "dynamic", "conditional", "all"]
    if args:
        choice = args[0].lower()
    else:
        print("  which conflict metric?")
        for i, t in enumerate(types, 1):
            print(f"    {i}) {t}")
        pick = input("  metric> ").strip().lower()
        choice = types[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(types) else pick
    if choice not in types:
        print("  (cancelled)")
        return
    selected = scenarios if choice == "all" else [s for s in scenarios if s.get("conflict_type") == choice]
    if not selected:
        print(f"  no '{choice}' scenario")
        return

    from . import profiles

    prev = session.profile_name
    session.load_profile(profiles.mind_dir(f"metrics-{choice}"))  # a throwaway mind, never your own
    session.profile_name = f"metrics-{choice}"
    try:
        for sc in selected:
            _run_metric_scenario(session, sc, pinned, console, _correct)
    except KeyboardInterrupt:
        print("\n  (metrics cancelled)")
    finally:
        session.running_note = None
        if pinned:
            _paint_header(console, session)
    print(f"\n  done - metrics ran in profile 'metrics-{choice}'. /profile {prev} to return to your mind")


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
            s.runtime.model = CloudFastModel(meter=s.fast_meter)
            s.cloud = True
            s.backend_label, s.backend_detail, s.offdevice = "DEBUG - CLOUD", "Claude API", True
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
            s = Session(store_dir=mind, background=True)
            s.compiler.judge = OpenRouterJudge(deep, meter=s.deep_meter)
            s.slow = OpenRouterSlowModel(deep, meter=s.deep_meter)
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
            break
        except RateLimitedError as exc:
            if not _wait_for_reset(console, vt_ok, exc.reset_at):
                return 0  # user quit the wait
            print("free tier reset - starting up...")  # retry the build
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
    pinned = console is not None and vt_ok and not console.legacy_windows
    if pinned:
        _enter_cockpit_screen(console, session)
        if session.background:
            # a landed background pass repaints the header in place — the answer didn't wait,
            # the panels catch up (spec §27)
            session.on_compiled = lambda: _paint_header(console, session)
    session.profile_name = profile
    from .view import status_bar

    print(status_bar(session.backend_label, session.backend_detail, session.offdevice))
    print(f"(profile: {profile})")
    if session.cloud:
        print(f"(models: deep={session.slow.model}, fast={session.runtime.model.model})")
    print(f"(mind: {mind} - {len(session.store.pages())} pages)")
    print(BANNER)
    while True:
        try:
            line = input("you> ").strip()
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
            elif cmd == "/metrics":
                _cmd_metrics(session, line.split()[1:], pinned, console)
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

    if session.background and session.backlog() > 0:
        print(f"(compiling {session.backlog()} remaining turn(s) before quitting...)")
        try:
            if not session.flush(timeout=120):
                print("(some turns could not be compiled - they are lost with this session)")
        except KeyboardInterrupt:
            print("(abandoned - un-compiled turns are lost with this session)")
    if pinned:
        _exit_cockpit_screen()
    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

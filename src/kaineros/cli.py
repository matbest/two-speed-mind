"""The two-speed chat shell.

Run with ``kaineros`` or ``python -m kaineros``. Plain text is a conversation turn; lines starting
with ``/`` are slash commands. Wired with the deterministic fakes for now — swap in a cloud/local
model (Slice 5) without touching this file.

The session owns the buffer and the compiled marker (spec §17): each turn, only the not-yet-compiled
tail is handed to the slow model — a turn is extracted exactly once, ever.
"""
from __future__ import annotations

import sys
import time

from .compiler import Compiler
from .fakes import FakeFastModel, FakeJudge, FakeSlowModel
from .interfaces import FastModel, Judge, SlowModel
from .runtime import Runtime
from .schema import Page, Response, Turn
from .store import Store

BANNER = (
    "kaineros - a two-speed mind that compiles experience into its kainome.\n"
    "Type to chat. Slash commands: /help  /notebook  /why  /model  /forget  /quit\n"
)

HELP = (
    "  /help      show this\n"
    "  /notebook  show the kainome (promoted pages - the clean knowledge base)\n"
    "  /why       the grounded reason behind the last answer\n"
    "  /model     show or switch models: /model deep|fast [model-id]  (--cloud only)\n"
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

    def backlog(self) -> int:
        return len(self.buffer) - self.compiled_upto

    def consolidate(self) -> list[Page]:
        """Run the slow brain over the un-compiled tail only (exactly-once), then housekeep."""
        fresh = self.buffer[self.compiled_upto :]
        candidates = self.slow.extract(fresh)
        # marker advances only after extraction succeeds: a failed/cancelled call must not lose
        # the turns. A re-run may re-extract (at-least-once); housekeeping's dedup merges that.
        self.compiled_upto = len(self.buffer)
        for cand in candidates:
            self.compiler.insert(cand)
        promoted = self.compiler.housekeep()
        self.compiler.last_report.backlog = self.backlog()
        if self.store_dir is not None:
            from .persist import save_store

            save_store(self.store, self.store_dir)  # spec §22: the mind hits disk every pass
        return promoted

    def wipe(self) -> None:
        """Erase the whole mind — buffer, store, and (if persisted) the files on disk."""
        self.store.pool.clear()
        self.store.clean.clear()
        self.forget()
        if self.store_dir is not None:
            from .persist import erase

            erase(self.store_dir)

    def turn(self, text: str) -> Response:
        self.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        self.consolidate()
        resp = self.runtime.respond(text, self.buffer)
        self.last_response = resp
        return resp

    def forget(self) -> None:
        self.buffer.clear()
        self.compiled_upto = 0


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


def _print_cockpit(console, session: Session) -> None:
    """The two panels (spec §18): deep brain LEFT, fast brain RIGHT — always side by side."""
    from rich.panel import Panel
    from rich.table import Table

    from .view import deep_panel, fast_panel

    resp = session.last_response
    deep = deep_panel(
        session.compiler.last_report, session.store, session.compiler.promote_after
    )
    fast = fast_panel(resp.trace if resp else None, resp, session.buffer)
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Panel(deep, title="deep brain - slow, off the clock"),
        Panel(fast, title="fast brain - this turn"),
    )
    console.print(grid)


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


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "evals":  # `kaineros evals [--free|--openrouter|--fakes|--only X]`
        from .evals import main as evals_main

        return evals_main(args[1:])
    plain = "--plain" in args or not sys.stdout.isatty()

    # the mind's home on disk (spec §20): default under LOCALAPPDATA, --mind / env override
    import os
    from pathlib import Path

    mind = os.environ.get("KAINEROS_MIND") or str(
        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "kaineros" / "mind"
    )
    if "--mind" in args:
        mind = args[args.index("--mind") + 1]

    console = None
    if not plain:
        try:
            from rich.console import Console

            console = Console()
        except ImportError:
            print("(rich not installed - running plain; pip install rich for the cockpit)")

    try:
        if "--cloud" in args:
            from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

            preflight()
            session = Session(
                judge=CloudJudge(), slow=CloudSlowModel(), fast=CloudFastModel(), store_dir=mind
            )
            session.cloud = True
        elif "--openrouter" in args or "--free" in args:
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
            preflight(fast)
            session = Session(
                judge=OpenRouterJudge(deep),
                slow=OpenRouterSlowModel(deep),
                fast=OpenRouterFastModel(fast),
                store_dir=mind,
            )
            session.cloud = True
        else:
            session = Session(store_dir=mind)
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 1
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
        if console is not None:
            _print_cockpit(console, session)
        print("mind> " + resp.answer)

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

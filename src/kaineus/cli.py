"""The two-speed chat shell.

Run with ``kaineus`` or ``python -m kaineus``. Plain text is a conversation turn; lines starting
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
    "kaineus - a two-speed mind that compiles experience into its kainome.\n"
    "Type to chat. Slash commands: /help  /notebook  /why  /model  /forget  /quit\n"
)

HELP = (
    "  /help      show this\n"
    "  /notebook  show the kainome (promoted pages - the clean knowledge base)\n"
    "  /why       the grounded reason behind the last answer\n"
    "  /model     show or switch models: /model deep|fast [model-id]  (--cloud only)\n"
    "  /forget    clear the short-term buffer\n"
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
    ) -> None:
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
        self.compiled_upto = len(self.buffer)
        for cand in self.slow.extract(fresh):
            self.compiler.insert(cand)
        promoted = self.compiler.housekeep()
        self.compiler.last_report.backlog = self.backlog()
        return promoted

    def turn(self, text: str) -> Response:
        self.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        self.consolidate()
        resp = self.runtime.respond(text, self.buffer)
        self.last_response = resp
        return resp

    def forget(self) -> None:
        self.buffer.clear()
        self.compiled_upto = 0


def _print_cockpit(console, session: Session) -> None:
    """The two panels (spec §18): deep brain top-left, fast brain top-right."""
    from rich.columns import Columns
    from rich.panel import Panel

    from .view import deep_panel, fast_panel

    resp = session.last_response
    deep = deep_panel(
        session.compiler.last_report, session.store, session.compiler.promote_after
    )
    fast = fast_panel(resp.trace if resp else None, resp, session.buffer)
    console.print(
        Columns(
            [
                Panel(deep, title="deep brain - slow, off the clock"),
                Panel(fast, title="fast brain - this turn"),
            ],
            equal=True,
            expand=True,
        )
    )


def _cmd_model(session: Session, args: list[str]) -> None:
    """/model — show or switch the per-brain models (arguments-first, picker as fallback)."""
    if not session.cloud:
        print("  /model needs --cloud (the fakes have no models to pick)")
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
        ids = [m.id for m in slow.client.models.list()]
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
    plain = "--plain" in args or not sys.stdout.isatty()

    console = None
    if not plain:
        try:
            from rich.console import Console

            console = Console()
        except ImportError:
            print("(rich not installed - running plain; pip install rich for the cockpit)")

    if "--cloud" in args:
        try:
            from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

            preflight()
            session = Session(judge=CloudJudge(), slow=CloudSlowModel(), fast=CloudFastModel())
            session.cloud = True
            print("(cloud models: deep=" + session.slow.model + ", fast=" + session.runtime.model.model + ")")
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1
    else:
        session = Session()
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
                session.forget()
                print("  (buffer cleared)")
            else:
                print(f"  unknown command: {cmd}  (try /help)")
            continue

        resp = session.turn(line)
        if console is not None:
            _print_cockpit(console, session)
        print("mind> " + resp.answer)

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

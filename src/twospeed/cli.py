"""The two-speed chat shell.

Run with ``twospeed`` or ``python -m twospeed``. Plain text is a conversation turn; lines starting
with ``/`` are slash commands. Wired with the deterministic fakes for now — swap in a cloud/local
model (Slice 5) without touching this file.

The session owns the buffer and the compiled marker (spec §17): each turn, only the not-yet-compiled
tail is handed to the slow model — a turn is extracted exactly once, ever.
"""
from __future__ import annotations

import sys

from .compiler import Compiler
from .fakes import FakeFastModel, FakeJudge, FakeSlowModel
from .interfaces import FastModel, Judge, SlowModel
from .runtime import Runtime
from .schema import Page, Response, Turn
from .store import Store

BANNER = (
    "two-speed mind - a local assistant that reads what it has learned.\n"
    "Type to chat. Slash commands: /help  /notebook  /why  /forget  /quit\n"
)

HELP = (
    "  /help      show this\n"
    "  /notebook  show the clean knowledge base (promoted pages)\n"
    "  /why       the grounded reason behind the last answer\n"
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

    def backlog(self) -> int:
        return len(self.buffer) - self.compiled_upto

    def consolidate(self) -> list[Page]:
        """Run the slow brain over the un-compiled tail only (exactly-once), then housekeep."""
        fresh = self.buffer[self.compiled_upto :]
        self.compiled_upto = len(self.buffer)
        for cand in self.slow.extract(fresh):
            self.compiler.insert(cand)
        return self.compiler.housekeep()

    def turn(self, text: str) -> Response:
        self.buffer.append(Turn(text=text, speaker="user"))
        self.consolidate()
        resp = self.runtime.respond(text, self.buffer)
        self.last_response = resp
        return resp

    def forget(self) -> None:
        self.buffer.clear()
        self.compiled_upto = 0


def main(argv: list[str] | None = None) -> int:
    # --plain is the line-based REPL below; it becomes the fallback once the
    # cockpit lands (T7.5) and is what the scripted smoke tests drive.
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
            elif cmd == "/forget":
                session.forget()
                print("  (buffer cleared)")
            else:
                print(f"  unknown command: {cmd}  (try /help)")
            continue

        resp = session.turn(line)
        print("mind> " + resp.answer)

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

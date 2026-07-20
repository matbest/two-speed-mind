"""The two-speed chat shell.

Run with ``twospeed`` or ``python -m twospeed``. Plain text is a conversation turn; lines starting
with ``/`` are slash commands. Wired with the deterministic fakes for now — swap in a local model
(Slice 5) without touching this file.
"""
from __future__ import annotations

import sys

from .compiler import Compiler
from .fakes import FakeFastModel, FakeJudge, FakeSlowModel
from .runtime import Runtime
from .schema import Turn
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


def main(argv: list[str] | None = None) -> int:
    store = Store()
    slow = FakeSlowModel()
    compiler = Compiler(store, FakeJudge())
    runtime = Runtime(store, FakeFastModel())

    buffer: list[Turn] = []
    last_why = "(no answer yet)"

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
                pages = store.pages()
                if not pages:
                    print("  (empty — nothing promoted yet)")
                for p in pages:
                    print(f"  [{p.gene}] {p.content}")
            elif cmd == "/why":
                print("  " + last_why)
            elif cmd == "/forget":
                buffer.clear()
                print("  (buffer cleared)")
            else:
                print(f"  unknown command: {cmd}  (try /help)")
            continue

        # a conversation turn
        turn = Turn(text=line, speaker="user")
        buffer.append(turn)
        try:
            for cand in slow.extract([turn]):
                compiler.insert(cand)
            compiler.housekeep()
            resp = runtime.respond(line, buffer)
            last_why = resp.why
            print("mind> " + resp.answer)
        except NotImplementedError as exc:
            print(f"  [the mind isn't wired yet: {exc}]")
            print("  Build it: work through docs/tasks.md and run pytest.")

    print("bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

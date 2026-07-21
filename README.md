# Kaineus

**Kaineus** compiles experience into its **kainome** — a prototype of the **Two-Speed Mind** (White
Paper 1): a *slow-deep* model that compiles conversation into a self-selecting knowledge genome (the
kainome), and a *fast* model that reads it to answer in real time — all local, inspectable, and
honest about *why* it says what it says.

The app is a command-line chat (`kaineus`) with slash commands, backed by the two-speed core.

## Quickstart

```bash
python -m venv .venv && . .venv/Scripts/activate   # (or source .venv/bin/activate)
pip install -e ".[dev]"

pytest            # the spec, as runnable tests — most are RED until built
kaineus          # start the chat shell (type /help)
```

## How this repo is meant to be built

It is **spec-driven and test-first**, designed for an AI coding agent (Claude Code) to build:

- `docs/spec.md` — what and why, with acceptance criteria.
- `docs/plan.md` — the architecture (the White Paper 1 components) and the CLI.
- `docs/tasks.md` — small, ordered, independently testable tasks.
- `tests/` — the acceptance/unit tests. **The tests are the contract.** A task is done when its
  tests are green.

The slow/fast models sit behind interfaces (`kaineus/interfaces.py`) so the whole pipeline is tested
with **deterministic fakes** (`kaineus/fakes.py`) — no real model needed to build or verify the
architecture. A real local model is just another adapter, added last.

Start with `docs/tasks.md`, top to bottom.

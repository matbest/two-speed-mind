# CLAUDE.md

Context for Claude Code sessions in this repo.

## What this is

**Kaineros** — an assistant that compiles experience into its **kainome** (its evolving knowledge
genome). A prototype of the **Two-Speed Mind** (see `docs/plan.md`, which distils White Paper 1). Two models
at two speeds over one knowledge base:

- **Slow-deep compiler** — turns raw conversation into ranked candidate facts, and promotes the
  fittest into a clean, readable knowledge base. Runs off the interactive path.
- **Fast runtime** — answers the user by *retrieving* from the clean base, not by reasoning from
  scratch. Abstains when it has nothing good.

Delivered as a CLI chat app (`kaineros`) with slash commands.

## How to work here (important)

This project is **test-first**. The tests in `tests/` are the spec.

- Work through `docs/tasks.md` in order. Each task names the tests it must turn green.
- `pytest` is the loop: run it, implement until green, repeat. Do not weaken a test to pass it —
  fix the code, or raise the mismatch with the user.
- The slow/fast models are **interfaces** (`kaineros/interfaces.py`). Build and test everything
  against the **deterministic fakes** in `kaineros/fakes.py`. A real local model (llama.cpp / HF) is the
  *last* task, added as an adapter — never a dependency of the core logic or the tests.

## The one principle that must not be violated

**The reason is grounded; the words only phrase it.** Behaviour and the *why* come from real state
(which pages were retrieved, their provenance) — never from the model's prose. The fast model
phrases the answer; it does not decide what is true. `Runtime.respond` returns both, kept apart
(`Response.answer` vs `Response.why`). Keep them apart.

## Structure

```
src/kaineros/
  schema.py       Turn, Provenance, Candidate, Page, Response  (the data model — the "schema" the paper calls the next artifact)
  interfaces.py   Judge, SlowModel, FastModel  (Protocols; the model boundary)
  store.py        Store — the gene pool (competing candidates) + the clean layer (promoted pages)
  compiler.py     Compiler — slow-deep: binary-insert by pairwise judge, promote settled winners
  runtime.py      Runtime — fast: retrieve above a confidence floor, abstain, phrase, explain
  fakes.py        deterministic Judge/SlowModel/FastModel — build & test against these (no real model)
  claude_cli.py   deep brain on the Claude subscription via `claude -p`  (mixed build: `kaineros --claude`)
  cli.py          the chat REPL + slash commands
tests/            the contract (test_store.py + test_runtime.py)
docs/             spec.md, plan.md, tasks.md
```

## Commands

- `pip install -e ".[dev]"` — install (editable) with pytest.
- `pytest` — run the suite. `pytest -k store` to scope.
- `kaineros` (or `python -m kaineros`) — run the chat shell.

## Vocabulary (from the paper, kept in the code)

gene = a concept/topic key · candidate (allele) = a competing version of a fact · gene pool = the
candidates for a gene · selection = pairwise ranking + promotion · page = a promoted clean entry ·
kainome = the clean layer the fast model reads (the evolving knowledge genome) · tags = the fact's
own vocabulary (the words a QUESTION would use), minted at extraction, routing retrieval and
scoping the conflicts sweep — hints, never verdicts (spec §44-45) · gist = the bare answer the
gene resolves to (gene=question, gist=value → a triple the fast brain reads without parsing a
sentence; spec §48) · arc = a promoted narrative page capturing how ONE thread of the user evolved
(initially X → now Y, because Z) — the deep brain's consolidated "story", built additively beside
the facts, never replacing them; grounded, never confabulated (spec §51).
Provenance/confidence/stakes travel on every fact.

# Plan — architecture

The mind is a **compiler/runtime split** over one store. Nothing requires the slow and fast work to
happen at the same moment.

```
you talk ─► short-term buffer ─────────────────────────────► fast Runtime ─► answer (+ grounded why)
                    │                                              ▲
                    │ (later, off the interactive path)            │ reads
                    ▼                                              │
             slow Compiler ─► gene pool (competing candidates) ─► clean layer (promoted pages)
                                 ranked by pairwise judge              = the "genome" the fast model reads
```

## Components (files)

- **schema.py** — the data model (the paper's "next artifact"):
  - `Turn` — one verbatim conversation line (buffer entry).
  - `Provenance` — `source_turn_ids`, `created_at`, `stated: bool`, `confidence`, `stakes`.
  - `Candidate` — an *allele*: a competing version of a fact about a `gene`, with provenance and a
    `wins` counter (how many passes it has held #1).
  - `Page` — a promoted clean entry (gene, content, provenance, `rank_history`).
  - `Response` — `answer` (words) + `why` (grounded reason) + `used: list[Page]` + `abstained: bool`.

- **interfaces.py** — the model boundary (Protocols):
  - `Judge.better(gene, a, b) -> bool` — the reliable primitive: *is A a better account than B?*
  - `SlowModel.extract(turns) -> list[Candidate]` — compile raw turns into candidate facts.
  - `FastModel.answer(question, pages, buffer) -> str` — phrase an answer from retrieved pages.

- **store.py** — `Store`: the `pool` (`dict[gene, list[Candidate]]`, ranked best-first) and the
  `clean` layer (`dict[gene, Page]`). Dumb data holder; logic lives in the compiler/runtime.

- **compiler.py** — `Compiler(store, judge, promote_after=3)` (slow-deep):
  - `insert(candidate)` — binary-insert into the gene's pool by the judge; incumbent defends.
  - `housekeep() -> list[Page]` — promote any gene whose top has held #1 for `promote_after` passes.

- **runtime.py** — `Runtime(store, fast_model, confidence_floor="low")` (fast):
  - `respond(question, buffer=None) -> Response` — retrieve pages above the floor; abstain if none;
    else phrase via the fast model. `why` is built from the retrieved pages' genes + provenance.

- **cli.py** — the chat REPL. Plain text → a turn (buffered; compiler consolidates). Slash commands:
  `/help`, `/notebook` (show pages), `/why` (last response's grounded reason), `/forget`, `/quit`.

## Testing strategy

Everything is tested against **deterministic fakes** (`twospeed/fakes.py`):
- `FakeJudge(key)` — `better = key(a) > key(b)`, so ranking is predictable in tests.
- `FakeFastModel` — returns a fixed template from the pages (so we can assert the *why* is grounded
  independently of the words).
- `FakeSlowModel` — one candidate per turn, deterministic.

A real local model (llama.cpp via `llama-cpp-python`, or an HF pipeline) is the **last** task: a
`LocalJudge` / `LocalFastModel` adapter behind the same interfaces. The core never imports it.

## Build order

Vertical slices, smallest first — see `docs/tasks.md`. Slice 1 is the schema + pool + ranking; the
chat app becomes real as the slices land.

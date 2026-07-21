# Plan — architecture

The mind is a **compiler/runtime split** over one store. Nothing requires the slow and fast work to
happen at the same moment.

```
you talk ─► short-term buffer ─────────────────────────────► fast Runtime ─► answer (+ grounded why)
                    │                                              ▲
                    │ (later, off the interactive path)            │ reads
                    ▼                                              │
             slow Compiler ─► gene pool (competing candidates) ─► clean layer (promoted pages)
                                 ranked by pairwise judge              = the "kainome" the fast model reads
```

## Components (files)

- **schema.py** — the data model (the paper's "next artifact"):
  - `Turn` — one verbatim conversation line (buffer entry).
  - `Provenance` — `source_turn_ids`, `created_at`, `stated: bool`, `confidence`, `stakes`.
  - `Candidate` — an *allele*: a competing version of a fact about a `gene`, with provenance and a
    `wins` counter (how many passes it has held #1).
  - `Page` — a promoted clean entry (gene, content, provenance, `rank_history`).
  - `Response` — `answer` (words) + `why` (grounded reason) + `used: list[Page]` + `abstained: bool`
    + `trace: Lookup` (the retrieval record the `why` and the lookup panel render from).
  - `Lookup` — one retrieval's real record: the query terms probed, per-page match strength, and
    each page's floor decision (admitted / blocked / no match); plus the floor in force. Funds the
    fast brain's cockpit panel.
  - `CompileReport` — one housekeeping pass's real tally: candidates inserted/merged, pools
    split/fused, pages promoted, backlog (turns awaiting compilation). Funds the deep brain's
    cockpit panel; the compiler keeps its latest as `last_report`.

- **interfaces.py** — the model boundary (Protocols):
  - `Judge.better(gene, a, b) -> bool` — the reliable primitive: *is A a better account than B?*
  - `Judge.same_claim(gene, a, b) -> bool` — the fission/fusion primitive: *are A and B rival
    accounts of one claim?* (Slice 4.5; also pairwise — never a score, never a cluster.)
  - `Judge.same_account(gene, a, b) -> bool` — the dedup primitive, one grain finer: *do A and B
    assert the same thing?* (Slice 4.5; housekeeping merges restatements — insert never asks this.)
  - `SlowModel.extract(turns) -> list[Candidate]` — compile raw turns into candidate facts.
  - `FastModel.answer(question, pages, buffer) -> str` — phrase an answer from retrieved pages.

- **store.py** — `Store`: the `pool` (`dict[gene, list[Candidate]]`, ranked best-first) and the
  `clean` layer (`dict[gene, Page]`). Dumb data holder; logic lives in the compiler/runtime.

- **compiler.py** — `Compiler(store, judge, promote_after=3, split_after=8)` (slow-deep):
  - `insert(candidate)` — binary-insert into the gene's pool by the judge; incumbent defends.
    Deliberately cheap (~log n comparisons, no identity checks) — repair work belongs to housekeep.
    Low-stakes candidates (spec §4) skip judging entirely: newest straight to #1, promoted next
    pass.
  - `housekeep() -> list[Page]` — the slow brain's maintenance pass, in order (order matters —
    each step keeps the next step's signal honest): **dedup** each pool (Slice 4.5: merge
    `same_account` candidates into the best-ranked; receipts accumulate, `created_at` refreshes,
    survivor keeps rank/`wins`); **fission** (Slice 4.5: split any pool still past `split_after` —
    top keeps the gene, the rest re-keyed to a fresh gene and re-inserted, page retired, `wins`
    reset); **fusion** (Slice 4.5: merge genes whose *promoted pages* state one claim — older key
    survives, pools re-insert, pages retire); **promotion** (any gene whose top has held #1 for
    `promote_after` passes).

- **runtime.py** — `Runtime(store, fast_model, confidence_floor="low", stale_after=30d)` (fast):
  - `respond(question, buffer=None) -> Response` — retrieve pages above the floor; abstain if none;
    else phrase via the fast model. `why` and `trace` are built from the retrieved pages' genes +
    provenance. **Hedging is state-driven** (spec §6): if any used page is inferred, low-confidence,
    or older than `stale_after`, the runtime prefixes the answer as memory ("If I remember
    rightly:") and the `why` labels each page fresh or stale — the model never decides the hedge.

- **view.py** — pure renderers (state in, text/renderables out — no I/O, no model): the
  **deep-brain panel** (a `CompileReport` + the store: last pass's tallies, backlog, population
  with wins-bars toward `promote_after`, promoted pages marked) and the **fast-brain panel** (a
  `Lookup` trace + what was handed to the fast model: probed terms → hits → floor decisions, pages
  used, buffer fill). Pure functions so the cockpit is testable without a terminal.

- **cli.py** — the chat shell. Default is the **cockpit** (spec §18): `rich` panels redrawn after
  each turn — deep brain top-left, fast brain top-right, conversation below, `input()` at the
  bottom; the panels just print `view.py` output. `--plain` drops the panels for a line-based REPL
  (scripts, pipes, tests). Plain text → a turn (buffered; compiler consolidates). Slash commands:
  `/help`, `/notebook` (show pages), `/why` (last response's grounded reason), `/forget`, `/quit`.
  A live event-driven TUI (Textual) is a later upgrade; the renderers already suit it.
  The session owns the buffer and `compiled_upto` (spec §17, exactly-once): per turn — append the
  turn; `extract(buffer[compiled_upto:])`; advance the marker; `insert` each candidate;
  `housekeep`; `respond`. The backlog (`len(buffer) - compiled_upto`) and buffer fill are what the
  panels report.

## Testing strategy

Everything is tested against **deterministic fakes** (`kaineros/fakes.py`):
- `FakeJudge(key)` — `better = key(a) > key(b)`, so ranking is predictable in tests.
- `FakeFastModel` — returns a fixed template from the pages (so we can assert the *why* is grounded
  independently of the words).
- `FakeSlowModel` — one candidate per turn, deterministic.

Real models are the **last** task, as adapters behind the same interfaces — the core never imports
them. Two families, cloud first:

- **cloud.py** (`cloud` optional dependency → `anthropic`): `CloudJudge` / `CloudSlowModel` /
  `CloudFastModel` on the Claude API. Deep roles (extractor + judge) default to `claude-opus-4-8`;
  the fast phraser to `claude-haiku-4-5` — the two-speed split maps directly onto model tiers, and
  the phraser can be tiny *because it only phrases*. Judge calls are forced-choice (structured
  output, never parsed prose); `extract` fills a Candidate JSON schema. Auth via
  `ANTHROPIC_API_KEY`. The `/model` picker's cloud list comes from the Models API.
- **local.py** (`local` optional dependency): llama.cpp via `llama-cpp-python` + a small GGUF (or
  an HF pipeline), same Protocols — added once the cloud adapter has proven the architecture.

## Build order

Vertical slices, smallest first — see `docs/tasks.md`. Slice 1 is the schema + pool + ranking; the
chat app becomes real as the slices land.

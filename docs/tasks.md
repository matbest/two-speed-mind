# Tasks — build order (test-first)

Work top to bottom. Each task is done when its named tests are **green** (`pytest`). Don't weaken a
test; fix the code, or raise a real mismatch with the user. Build against the fakes in
`twospeed/fakes.py` — no real model until the last task.

---

## Slice 1 — the selective store  ·  `tests/test_store.py`

**T1. Schema is real.** Flesh out `schema.py` (`Turn`, `Provenance`, `Candidate`, `Page`,
`Response`) as dataclasses with the fields in `docs/plan.md`. `Candidate` gets a stable `id` and a
`wins` counter. *(These are already defined in the skeleton — confirm the tests import them.)*

**T2. Ranked insertion.** Implement `Compiler.insert`: binary-insert a candidate into
`store.pool[gene]` using `judge.better`, keeping the list best-first. The current top must be
*beaten* to be displaced (incumbent defends).
→ green: `test_insert_orders_by_judge`, `test_incumbent_defends`.

**T3. Losers survive.** A candidate that loses stays in the pool at its ranked position (never
dropped).
→ green: `test_loser_not_destroyed`.

**T4. Promotion threshold.** Implement `Compiler.housekeep`: a gene's top candidate is promoted to
`store.clean` as a `Page` only once it has held #1 for `promote_after` passes; return newly promoted
pages. Record a timestamped entry in the page's `rank_history`.
→ green: `test_promote_after_threshold`, `test_not_promoted_before_threshold`.

---

## Slice 2 — the fast runtime  ·  `tests/test_runtime.py`

**T5. Retrieval + abstention.** Implement `Runtime.respond`: retrieve pages relevant to the question
(naive keyword overlap on gene/content is fine for v1) that clear the `confidence_floor`. If none,
return a `Response` with `abstained=True` and a "don't know" answer.
→ green: `test_abstains_when_empty`, `test_confidence_floor_hides_weak_pages`.

**T6. Reason vs words.** When pages clear the floor, ask the `FastModel` to phrase the `answer`, but
build `why` yourself from the retrieved pages (their genes + provenance). Assert the `why` is
grounded even when the fake model returns a fixed string.
→ green: `test_answers_from_pages`, `test_reason_is_grounded`.

---

## Slice 3 — the chat app  ·  (manual + a smoke test you add)

**T7. Wire the REPL.** In `cli.py`, wire `Store` + `Compiler` + `Runtime` with the **fakes**. Plain
text → append a `Turn` to the buffer, run `SlowModel.extract` + `Compiler.insert` +
`Compiler.housekeep`, then `Runtime.respond` and print `answer`. Slash commands: `/help`,
`/notebook` (print `store.clean` pages), `/why` (print the last `Response.why`), `/forget` (clear
buffer/selection), `/quit`. `twospeed` should hold a real (if simple) conversation end-to-end.
Add `tests/test_cli.py` with a smoke test driving a scripted transcript.

---

## Slice 4 — provenance & honesty (deepen)

**T8.** Confidence tiers actually gate retrieval and hedging (recent+stated → assert plainly;
old/inferred → hedge). Stakes: only high-stakes facts get the full pool/competition; persona/style
are low-stakes and skip it.

## Slice 5 — the real model (last)

**T9.** Add `twospeed/local.py`: `LocalJudge` / `LocalFastModel` / `LocalSlowModel` behind the same
Protocols, backed by a local model (e.g. `llama-cpp-python` + a small GGUF). Put it behind the
`local` optional dependency. The core and all Slice 1–4 tests keep passing untouched.

## Later (from the paper's §9 — not yet)

Candidate identity / clustering (which candidates are about the *same* gene), the resource governor /
background scheduling, comparison-drift smoothing, an Elo fallback for intransitive judgments, and a
consolidation-triage scheduler. Each gets its own spec + tests when reached.

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
text → append a `Turn` to the buffer, run `SlowModel.extract` **on the not-yet-compiled tail only**
(spec §17, exactly-once: the session tracks `compiled_upto` and advances it after extraction — the
smoke test must assert a turn is never extracted twice), then `Compiler.insert` +
`Compiler.housekeep`, then `Runtime.respond` and print `answer`. Slash commands: `/help`,
`/notebook` (print `store.clean` pages), `/why` (print the last `Response.why`), `/forget` (clear
buffer/selection), `/quit`. `twospeed` should hold a real (if simple) conversation end-to-end.
Add `tests/test_cli.py` with a smoke test driving a scripted transcript (use `--plain` mode).

**T7.5. The cockpit.** Make the shell show the mind working (spec §15, §18–19).
1. Add `Lookup` to `schema.py` (query terms, per-page match strength, floor decisions, floor in
   force) and a `trace: Lookup` field on `Response`; fill it in `Runtime.respond` — the trace is
   recorded *as retrieval happens*, and `why` is built from it. Extend `tests/test_runtime.py`:
   the trace lists the blocked page (and why) when the floor hides it; abstention leaves a trace too.
2. Add `CompileReport` to `schema.py` (inserted/merged/split/fused/promoted tallies + backlog);
   `Compiler.housekeep` fills it and keeps the latest as `compiler.last_report` (return value stays
   `list[Page]` — the T4 tests are untouched). Test: the report's numbers match what the pass did.
3. Add `view.py` with pure renderers — `deep_panel(report, store)` (tallies, backlog, population
   with wins-bars toward `promote_after`, promoted pages marked) and `fast_panel(trace, response,
   buffer)` (probed terms, hits, floor decisions, pages used, buffer fill) — and
   `tests/test_view.py` asserting on their text output. No terminal needed.
4. Wire the cockpit in `cli.py` with `rich` (new core dependency): deep brain top-left, fast brain
   top-right, conversation below, input at the bottom, redrawn after each turn. `--plain` switches
   to the line-based REPL that `test_cli.py` drives.

---

## Slice 4 — provenance & honesty (deepen)

**T8.** Confidence tiers actually gate retrieval and hedging (recent+stated → assert plainly;
old/inferred → hedge). Stakes: only high-stakes facts get the full pool/competition; persona/style
are low-stakes and skip it. Time is evidence (spec §6): with a recency-aware judge, a newer stated
account displaces an older incumbent through the normal win path — test that the pool converges on
the newer fact, and that the `why` surfaces the age of what was used.

## Slice 4.5 — gene identity: dedup, fission & fusion  ·  `tests/test_identity.py` (you add)

**T8.4 Dedup (restatement merges, in cleanup).** Spec §11. Add `same_account(gene, a, b) -> bool`
to the `Judge` Protocol and `FakeJudge` (deterministic: an `account_of` key function). `insert`
stays untouched — duplicates pile up freely. In `Compiler.housekeep`, **first step** (before the
fission crowding check): within each pool, merge `same_account` candidates into the best-ranked of
them — union the `source_turn_ids`, refresh `created_at` to the newest statement, survivor keeps
rank and `wins`, the others are removed. Tests to write: restatements coexist until housekeep, then
merge to one (receipts accumulate); a *distinct* account survives cleanup; a pool inflated past
`split_after` purely by duplicates dedups without splitting (dedup-before-fission ordering).

**T8.5 Fission.** A gene must mean **one claim** (spec §7–9). Add `same_claim(gene, a, b) -> bool`
to the `Judge` Protocol and to `FakeJudge` (deterministic: a `claim_of` key function — same claim
iff keys are equal). In `Compiler.housekeep`, when a pool exceeds `split_after` (default 8), run the
greedy split: the top candidate keeps the gene; candidates not `same_claim` with it are re-keyed to
a fresh gene (next free `<gene>.2`, `<gene>.3`, …) and re-inserted through `insert` (fresh rank,
`wins` reset). Retire the gene's clean page, if any — each child pool re-earns promotion. No
recursion: a still-mixed child pool splits on a later pass. Tests to write: a mixed pool splits into
two claims; a large-but-pure pool does *not* split; `wins`/pages don't survive a split; a
three-claim pool converges over successive passes.

**T8.6 Fusion.** The mirror repair (spec §10): in `housekeep`, check promoted pages pairwise with
`same_claim`; on a match, merge the two genes — the older gene key survives, all candidates from
both pools re-insert into it (fresh ranks, `wins` reset), both pages retire. Tests to write: two
genes whose pages state one claim merge into the older key; their candidates then compete in one
pool; unrelated pages do *not* merge; nothing merges while the duplicates are still unpromoted
(fusion only reads the clean layer).

## Slice 5 — the real model (last)

**T9.** Add `twospeed/local.py`: `LocalJudge` / `LocalFastModel` / `LocalSlowModel` behind the same
Protocols, backed by a local model (e.g. `llama-cpp-python` + a small GGUF). Put it behind the
`local` optional dependency. The core and all Slice 1–4 tests keep passing untouched.

## Later (from the paper's §9 — not yet)

Closing the **freshness gap** (spec §16) — retrieval over the un-compiled buffer and the pools'
current tops as extra, provenance-marked strata, with a consolidation watermark ("compiled 4/6
turns") tracking how far the slow brain has got; model-named child genes (fission's `<gene>.2`
suffixes are placeholders); fusion for *unpromoted* duplicates (v1 fusion only reads the clean
layer, so split-brain below the promotion line goes unnoticed); decay/expiry for stale unrefreshed
facts (time currently enters selection only via the judge); the resource governor / background
scheduling; comparison-drift smoothing; an Elo fallback for intransitive judgments; and a
consolidation-triage scheduler. Each gets its own spec + tests when reached.

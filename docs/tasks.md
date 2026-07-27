# Tasks — build order (test-first)

Work top to bottom. Each task is done when its named tests are **green** (`pytest`). Don't weaken a
test; fix the code, or raise a real mismatch with the user. Build against the fakes in
`kaineros/fakes.py` — no real model until the last task.

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
buffer/selection), `/quit`. `kaineros` should hold a real (if simple) conversation end-to-end.
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

## Slice 5 — real models (last; adapters only)

**T9a. Cloud adapter (first).** Add `kaineros/cloud.py`: `CloudJudge` / `CloudSlowModel` /
`CloudFastModel` behind the same Protocols, on the Claude API (`anthropic` package, behind the
`cloud` optional dependency: `pip install -e ".[cloud]"`; auth via `ANTHROPIC_API_KEY`). Deep roles
(extract + the three judge questions) default to `claude-opus-4-8`; the fast phraser to
`claude-haiku-4-5`. Judge calls are **forced-choice** — the model answers only A-or-B / yes-or-no
via structured outputs, never parsed prose (the grounding rule extends to the model boundary); keep
judge prompts tiny, they run pairwise in housekeeping. `extract` fills a Candidate JSON schema
(structured outputs). `kaineros --cloud` runs the chat on cloud models; the core and every Slice
1–4 test stay on the fakes, untouched. Add `/model deep|fast [name]` (arguments-first, numbered
picker as fallback); the cloud model list comes from the Models API. Purpose: test the
*architecture* against strong models — if it misbehaves here the design is wrong, not the model.
The yardstick is the **eval corpus** at `evals/conversations.toml`: example conversations → the
knowledge they should become (keywords that must reach the clean layer + a probe question). Run
`python -m kaineros.evals` to grade the real models against it (spends tokens; not part of
pytest — pytest only checks the corpus is well-formed); `--fakes` runs it free as a harness smoke.

**T9b. Local adapter.** Add `kaineros/local.py`: `LocalJudge` / `LocalFastModel` / `LocalSlowModel`
backed by a local model (e.g. `llama-cpp-python` + a small GGUF) behind the `local` optional
dependency. Same Protocols; nothing else changes. `/model`'s local list comes from Ollama
(`/api/tags`) or a curated `models.toml`. If quality drops vs the cloud run, the delta is the
model, not the design — that comparison is the point of doing cloud first.

## Slice 6 — the mind persists  ·  `tests/test_persist.py` (you add)

**T10. Persistence.** Spec §20–23. Add `kaineros/persist.py`: `save_store` / `load_store` per
`docs/plan.md`. `Session(store_dir=...)` loads on start, saves after every consolidation
(atomic temp+rename). CLI: default home `%LOCALAPPDATA%\kaineros\mind`, `--mind PATH` /
`KAINEROS_MIND` override; `/forget all` erases the persisted mind after an explicit "yes".
Tests to write: a store round-trips exactly (pool order, `wins`, provenance, pages,
`rank_history`); two Sessions over one dir share a mind (facts said to the first are served by
the second); each promoted page is its own `kainome/<gene>.json` and a retired page's file
disappears on the next save; a corrupt `pool.json` raises with a clear message rather than
starting empty; `store_dir=None` touches no disk (the whole existing suite is the proof).

## Slice 7 — background consolidation  ·  `tests/test_background.py` (you add)

**T11. The slow brain gets off the interactive path.** Spec §24–27. `Session(background=True)`
starts one daemon worker; `turn()` buffers, wakes the worker, and responds immediately from the
current pages. The worker drains the un-compiled tail (marker advances on success only —
at-least-once, dedup cleans up), holds the write lock for mutations but never during model calls
it doesn't need to, saves after each pass, sets `CompileReport.error` on failure (bounded retries,
then wait for the next wake), and fires an `on_compiled` callback (the CLI repaints the pinned
header). `flush(timeout)` drains the backlog (the CLI calls it on quit if needed). Default stays
synchronous — the whole existing suite is the proof. Tests to write (event-driven, no sleeps): a
turn returns while extraction is still blocked (backlog visible, then flush lands the fact); every
turn extracted exactly once across several background passes; a failing extraction loses nothing
(error lands in the report, retry succeeds, marker catches up); the deep panel shows the failure.

## Slice 8 — the assistant acts (tools)  ·  `tests/test_tools.py` (you add)

**T12. Tools.** Spec §28–32. A design-first slice — get the grounded shape right on fakes before
any real capability.
- **interfaces.py**: a `Tool` Protocol — `name`, `description`, `locality` ("on-device" |
  "off-device"), `requires_confirmation: bool`, `input_schema`, `run(inputs) -> str`. And a
  `Toolbox` the runtime consults.
- **schema.py**: `ToolCall` (tool, inputs, result, at, confirmed) and an `Action` receipt; a
  `proposal: ToolCall | None` field on `Response`.
- **runtime.py**: after retrieval, the fast model may return a structured proposal (tool name +
  typed inputs) instead of / alongside an answer — schema-validated, never parsed from prose (spec
  §29). The runtime, not the model, decides execution: on-device read-only tools run; anything
  side-effecting or off-device is gated on confirmation (spec §31).
- **fakes.py**: a couple of on-device fake tools (e.g. `now`, `calc`) and one off-device
  (`web_search` stub) so the gate and the locality posture are testable with no network.
- **persist.py**: append-only `actions.jsonl` on disk (spec §32); the status bar's `offdevice`
  becomes `model_offdevice OR any(enabled tool is off-device)` (spec §30).
- **cli.py**: the cockpit shows the action receipt (proposed → confirmed? → result); a `/tools`
  command lists the toolbox with localities.
Tests to write (fakes only): a proposed on-device read-only tool runs and its result is recorded;
an off-device tool is NOT run without confirmation; the action log round-trips; the status bar
flips to off-device when an off-device tool is enabled; a malformed proposal is rejected, not
executed. Real tools (filesystem, shell, web) come after, one at a time, each declaring its
locality honestly.

## Token-cost debt (revisit — flagged 2026-07-21)

**Conflict detection is O(pages²) deep-model calls per pass.** Slice 9's `_curate` compares every
pair of promoted pages with `conflicts` (on top of fusion's existing pairwise `same_claim` sweep),
so deep-brain token spend grows quadratically with the wiki — against the goal of *reducing* tokens
and judging local feasibility. Same debt applies to fusion. Cheaper approaches to weigh when we
return: only compare **newly-promoted/changed** pages against the rest (O(changed·N), not O(N²));
**cache** verdicts keyed by (content_a, content_b); scope comparisons to **related genes** (shared
key prefix, e.g. `user.job.*`); **gate by stakes**; or fold conflict/claim detection into the
**extractor pass that already runs** instead of a separate sweep. No change yet — noted so the
token budget isn't quietly blown as wikis grow.

## Research findings — conflict benchmark + Basic Memory (2026-07-21)

Mapped Kaineros against the **MemConflict** benchmark (arxiv 2605.20926) and the "Don't Ask the LLM
to Track Freshness" paper (2606.01435); studied **Basic Memory** (local-first Markdown wiki) as the
nearest analog. Scenarios in `evals/conflicts.toml` (`kaineros evals --corpus conflicts`).

**Where Kaineros stands on the three conflict types:**
- **Static** (a stray contradiction must NOT overwrite a stable fact — everyone else's *worst*
  category; best benchmarked CRS ≈ 0.25): Kaineros's **structural strength**. "Recency is a vote,
  not a veto" means the incumbent defends and a single stray mention can't displace a many-pass
  winner. *Gap:* we win the answer by inertia but emit no **recognition signal** (a CRS-style "an
  incumbent was challenged and defended" event). Consider logging defended-challenges so the
  "noticed the contradiction" is explicit, not just implied.
- **Dynamic** (a real update should win): handled, but with a **displacement lag** — the newcomer
  must win the pairwise contest over passes while the old page keeps serving. The lag *is* the cost
  (we deliberately reject the freshness paper's deterministic `max(timestamp)` because not every
  new utterance is authoritative). Measurable via the eval.
- **Conditional** (two facts both valid in different contexts — coffee@morning, milk@evening): the
  **real weakness**. If both land in one gene pool the pairwise judge tries to pick *one* winner,
  destroying a valid context-scoped fact; and the disambiguation queue may **falsely fire** ("coffee
  or milk?") on facts that don't actually conflict. **Highest-value fix: condition-aware gene keys**
  (gene = `claim@condition`, e.g. `user.drink.morning` vs `user.drink.evening`) so the two never
  compete, and guard `conflicts` from firing when the pages carry different conditions.

**Borrow from Basic Memory (its file ergonomics; keep our selection engine):**
1. **Hand-edits flow back as candidates** — fixes the read-only wiki (spec §21). Ingest a hand-edit
   to `<gene>.md` as a new candidate with maximal-authority provenance (`stated`, high confidence,
   `source: human-edit`) that competes and almost always wins — preserving grounding (it competes,
   isn't blindly trusted) while ending "your edits get clobbered." Needs a file-watcher + checksum
   gate (Basic Memory's `file_version`/`db_version` pattern) so consolidation knows a file was
   touched externally before it overwrites.
2. **Typed wikilinks between gene files** (`- supersedes [[old-gene]]`, `- relates_to [[x]]`) — turns
   the kainome into a navigable graph and gives retrieval a cheap graph-traversal mode.
3. **Frontmatter** carrying provenance/confidence/stakes + a stable `permalink` per gene — makes the
   wiki self-describing and diff-friendly.
4. **git-commit the kainome each consolidation pass** — every promotion/demotion becomes a reviewable
   diff: a free, human-owned audit trail complementing in-fact provenance.

## Slice 10 — deep-brain modes & cheaper retrieval (spec §40-41)

**T14 (done): mode-gated cleanup.** `housekeep(cleanup=False)` does rank-only (dedup/fission/
promotion, per-pool); `cleanup=True` adds the cross-page fusion + conflict detection. `Session`
runs cleanup on a cadence (`cleanup_every`, =4 in chat, =1 for tests/metrics) and `/cleanup` forces
it. Cuts the O(pages²) token debt on the hot path. Next in this slice, not yet built:

**T15. Summarise mode.** Promotion should *distil* the pool into a concise, search-friendly page
(via the deep model), not copy the top candidate verbatim. Keeps provenance; improves what the
fast brain reads.

**T16. Index-as-router + two-step retrieval (the token win).** Cleanup mode regenerates `index.md`
as a router: one terse cue line per page (aliases/keywords the fast brain can match). Retrieval
becomes two-step — the fast (cheap) model reads the *index* to pick the right page, then reads only
that page — instead of pulling every keyword match. Fewer tokens per answer; the whole point of
spec §41. Needs: a router format the deep brain writes in cleanup, and a runtime path that consults
the index first. Measure the token drop with `evals --corpus retrieval --metrics`.

**T17 (done): update-signal detection (spec §42).** Provenance gains `supersedes`; the extractor
sets it for a deliberate update ("now", "as of today", "not X", "moved", "switched", "new") keyed
to the same gene; the compiler inserts a `supersedes` candidate at the top and promotes it
immediately (like low-stakes), so a real update displaces an entrenched incumbent fast while a
stray contradiction still fights. Closes the `dynamic-entrenched` hard-corpus failure without
touching static. Remaining hard-corpus gap: `dynamic-implicit` + retrieval semantics (needs
embeddings or alias-enriched pages — the fuller T16).

**T18 (done): verdict cache.** Measured on `/bench sample`: 123 judge calls for 5 elements
(24.6 rankings/element) — `better` cost 1 (insertion is O(log n) already); the waste was
`same_claim` (74) and `conflicts` (40) re-asking the SAME unchanged pairs every housekeeping
pass. The Compiler now memoises verdicts on `(kind, gene, content-a, content-b)`; repeats answer
from the cache; only never-seen pairs reach the model. Result: 123 → 18 calls, 3.6
rankings/element (6.8x), scores unchanged; tests/test_verdict_cache.py holds the contract
("repeat passes cost zero new calls"). Verdicts survive a mid-run judge swap by design — they're
about the facts, and re-litigating settled pairs would reintroduce the churn.

**T19 (done): deterministic pre-comparator.** `Compiler._shortcut` settles near-certain
first-time comparisons in code: identical normalised text → same account / same claim / no
conflict / never "better"; zero content-token overlap (the runtime's own tokeniser) → not the
same claim (a false negative merely skips a fusion — non-destructive). **conflicts gets NO
overlap shortcut**: tests/test_modes.py pins a zero-overlap semantic collision ("backend
engineer" / "platform team"), so conflict detection stays the model's call — the suite caught
the over-eager first draft, which is the point of the suite. Measured on `/bench sample`:
18 → 11 judge calls, 2.2 rankings/element (from 24.6 uninstrumented — 11x total with T18);
same_claim sweeps 8 → 1. Remaining cost frontier: the conflicts sweep — irreducibly semantic,
so the lever is scoping (only NEW/changed pages per pass; stakes-gating), not shortcutting.

**T20 (done): tags — the fact's own vocabulary (spec §44-45).** Every fact carries `tags` as a
child field in the population JSON: the words someone would use when ASKING about it, minted by
the extractor on the existing call (~15 tok). They travel for life (dedup unions them, promotion
copies them to the page, index.md renders them as cue lines) and serve both brains: retrieval
matches `content ∪ tags` (closes dynamic-implicit's vocabulary gap — "what car do I drive?"
reaches a page that only says "Tesla"), and the conflicts sweep skips page pairs whose declared
topics are disjoint (collapses O(pages²) when tagged; either side untagged → falls through to
the judge, so the zero-overlap collision spec test stays covered). Hints, never verdicts.
Verify on the real backend: `/metrics conflict-hard dynamic` (dynamic-implicit should flip) and
`/bench 2`'s conflicts count. Note: pre-tags pages on disk have no tags and always fall through
— the deep brain re-tagging old pages during cleanup is future work.

**T21 (done): grooming, not sweeping (spec §47).** The two cross-page sweeps no longer iterate
all pairs. `_pairs_to_check(changed_genes)` builds a bounded set: the **eager frontier** (changed
pages × plausible peers — same-tag or either-side-untagged) catches fresh conflicts the moment
their second page lands; plus `groom_rate` **random** pairs (tag-biased) grooming the long tail.
`_fusion`/`_curate` consume that set. Fusion moved AFTER promotion (so it's eager, no one-pass
lag — three timing tests updated to match). Proven on the fakes: judge calls stay flat as pages
grow 10→60 (tests/test_groomer.py), a fresh conflict is caught eagerly, an old one in a quiet
mind is caught within a bounded number of grooming passes. On real (tagged) minds the frontier
stays small; untagged minds fall back to the conservative check-every-peer path. Next: cap the
verdict cache (LRU) so its memory doesn't creep toward O(pages²) on a long-lived mind.

**T22. Wiki in Open Knowledge Format (deferred, low-risk).** Render the kainome in Google's OKF
(cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing)
— a portable/interoperable knowledge representation — instead of (or alongside) the bespoke
Markdown. **Safe to defer:** the wiki is *derived, write-only output* (spec §46) — every
comparison runs on the JSON layers (pool + pages), never on the rendered wiki — so the format is
a pure `persist._render_wiki` change with zero effect on the engine or its cost. Pick the target
representation, then it's a rendering task.

**T23. Arc synthesis — the deep brain dreams the story (spec §51)**  ·  `tests/test_arcs.py` (you add)

Why: the single-model baseline proved where a compiled memory loses to raw context — the
*narrative* question types. On PersonaMem (5 personas), Opus reading the raw history scores
reason-behind-update **0.93** and preference-evolution **1.00**; Kaineros, reading flat latest-value
facts, scores **0.64 / 0.50**. That's the whole gap to close, and grooming (T21) now settles in ~2
passes with time to spare (the convergence fix) — idle capacity this fills. This is the corrected,
*additive* successor to the destructive summarise (§50/T15, now off).

The interface: `SlowModel` gains `arc(thread) -> {gist, content, reasons}` — a deep call that
distils an ordered thread of facts into one narrative. The **fake** implements it deterministically
(joins the facts in time order, echoes only reasons present in the input — never invents one) so the
whole thing builds and tests against fakes, real adapter last (Slice 5 rule). `Page`/`Candidate`
gain `kind: "fact" | "arc"` (default `"fact"`). Compiler gains an `_arc()` cleanup step gated by
`arc_enabled` (default False, like `summarise_enabled`), which finds a thread with temporal
structure — a `supersedes` chain on a gene, or a same-tag cluster whose source turns span multiple
times — and promotes an arc page beside (not instead of) the facts.

The contract (name these tests):

- **test_arc_synthesised_from_a_supersedes_chain** — a gene that was updated (an incumbent + a
  `supersedes` candidate, or a promote history showing old→new) yields an arc page whose gist
  captures both endpoints ("initially X → now Y"), `kind == "arc"`.
- **test_arc_is_additive_not_destructive** — after `_arc()`, every atomic fact page that fed the arc
  still exists (contrast `test_summarise`, where fragments retire). Recall of an individual fact is
  unaffected.
- **test_arc_is_grounded_no_invented_reason** — given a thread whose turns state *what* changed but
  never *why*, the arc's content/gist contains no fabricated "because"; every `source_turn_id` on
  the arc's provenance is one that really fed it. (Fake `arc` that tried to invent a reason would
  fail this — the guard, not the model, enforces it.)
- **test_arc_routes_for_a_why_question** — a narrative probe ("why did you switch from X to Y?")
  retrieves the arc page (its narrative tags match), not just the scattered fact pages.
- **test_arc_is_rebuilt_when_the_thread_gains_a_new_fact** — a new fact on an arced thread marks the
  arc dirty (via the §47 dirty-set) and re-synthesis updates it; a stale arc never outlives its
  facts.

Then measure on the real bench: `/bench 4 personas=5 ingest=cached arc=on` (a new bench flag flips
`arc_enabled`, like `summarise=on`) — the target is lifting reason-behind-update and
preference-evolution toward the raw-context baseline while the additive rule keeps plain recall flat.
Optional follow-up: an adversarial verify pass (a second deep call that must confirm each "because"
against the sources before promotion).

## Later (from the paper's §9 — not yet)

Closing the **freshness gap** (spec §16) — retrieval over the un-compiled buffer and the pools'
current tops as extra, provenance-marked strata, with a consolidation watermark ("compiled 4/6
turns") tracking how far the slow brain has got; model-named child genes (fission's `<gene>.2`
suffixes are placeholders); fusion for *unpromoted* duplicates (v1 fusion only reads the clean
layer, so split-brain below the promotion line goes unnoticed); decay/expiry for stale unrefreshed
facts (time currently enters selection only via the judge); the resource governor / background
scheduling; comparison-drift smoothing; an Elo fallback for intransitive judgments; and a
consolidation-triage scheduler. Each gets its own spec + tests when reached.

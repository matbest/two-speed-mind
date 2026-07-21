# Spec — Kaineros, the two-speed mind (prototype)

## Goal

A local personal assistant that **understands you deeply and answers you fast**, by splitting the two
so they never have to happen at the same moment:

- a **slow-deep** model compiles the raw record of conversation into a clean, structured knowledge
  base, offline;
- a **fast** model answers each turn by *reading* that base.

The base is self-selecting (competing versions of a fact are ranked; the fittest is promoted) and
honest (every answer separates the **grounded reason** from the **phrased words**, and the system
abstains rather than guess).

This prototype proves the *architecture* — with the model faked — as a command-line chat.

## Non-goals (for the prototype)

- Real models in the core. Models arrive **last, as adapters** behind the Protocols — a **cloud
  adapter** first (Claude API: prove the architecture with strong models before local quantisation
  adds its own noise), then the **local** adapter. Either brain can run on either; the core and the
  tests depend on neither.
- Multi-user, networking (beyond the cloud adapter's API calls), persistence beyond a simple store,
  a GUI.
- The resource governor / scheduling (stubbed; single-threaded is fine for v1).

## Acceptance criteria (these become tests)

**Knowledge base as a selective store**
1. A fact carries **provenance**: which turns it came from, when, whether it was *stated* or
   *inferred*, a **confidence** tier, and a **stakes** class.
2. For one concept (**gene**), several competing versions (**candidates/alleles**) can coexist in a
   **pool**, kept ranked by **pairwise** comparison (the judge only ever answers "is A better than
   B?").
3. A new candidate is placed by **binary insertion**; the current top must be *beaten* to be
   displaced (the incumbent defends its position).
4. A candidate is **promoted** to the clean layer only once it has held the top rank across
   `promote_after` passes. A promoted entry is a **page**. That competition is for **high-stakes**
   facts. Low-stakes facts (persona/style) skip it entirely: insertion makes no judge calls (the
   newest account goes straight to the top — for style, recency *is* the right answer) and the top
   promotes on the next pass. Trivia is not worth judgment, but it still carries provenance and is
   served like any page.
5. A beaten candidate is **not destroyed** — it drops back in the pool and can win again later.
6. **Claims drift, so time is evidence.** Every fact is timestamped: `created_at` travels in
   provenance, and every promotion appends a timestamped entry to the page's `rank_history`. The
   judge sees provenance, so a newer stated account *can* displace an older incumbent (it still has
   to win — recency is a vote, not a veto), and the runtime hedges by age (recent → plain assertion;
   old → "as of …").

**Gene identity (a pool is one claim, its candidates distinct accounts)**
7. A gene stands for **one claim**, not one topic: the candidates in a pool are rival *accounts of
   the same claim* ("user lives in London" vs "user lives in Berlin"), never merely same-topic facts
   ("likes bananas" vs "allergic to bananas"). The store trusts the extractor's gene key at insert
   time — coarse keys are expected, not fatal, because of 8–11.
8. **Crowding is the symptom of a too-coarse key.** A pool of rival accounts of one claim stays
   small; a pool that keeps growing is a topic in disguise. When a pool exceeds `split_after`
   candidates, the slow compiler examines it during housekeeping — off the interactive path, like
   all slow work.
9. The examination is **pairwise**, like all judging: `same_claim(gene, a, b)` — "are A and B rival
   accounts of one claim?". A mixed pool is **split** (fission): the top candidate keeps the
   original gene; candidates that don't share its claim are re-keyed to a fresh gene and re-inserted
   through the normal path. History does not leak across a split — re-homed candidates re-rank from
   scratch (`wins` reset) and the gene's page, if any, is retired; each child pool re-earns
   promotion. A split-off pool that is itself still mixed simply splits again on a later pass.
10. The mirror failure — one claim scattered across two keys, so its accounts never meet in a pool
    (**split-brain**) — is healed by **fusion**: during housekeeping, the compiler checks *promoted
    pages* pairwise with `same_claim`; when two pages state the same claim, their genes merge. The
    older gene key survives, both pools re-insert into it through the normal path (fresh ranks,
    `wins` reset), both pages retire, and the merged pool re-earns promotion. Checking only pages
    keeps the cost bounded (the clean layer is small) and targets the failure that matters — both
    copies being *served*.
11. **Restatement is evidence, not a rival — and dedup is slow work.** Identity has a third,
    finest grain: `same_account(gene, a, b)` — "do A and B assert the same thing?" ("London" vs
    "Berlin" are rival accounts of one claim; "I love bananas" twice is one account, twice).
    Insert does **not** check this — insert stays cheap (~log n comparisons), and duplicates are
    allowed to pile up. During housekeeping, the compiler **cleans up**: within each pool it merges
    same-account candidates into the best-ranked of them — source turn ids accumulate (the receipt
    list grows), `created_at` refreshes to the latest statement (§6), the survivor keeps its rank
    and `wins`. Restatement thereby becomes evidence the judge can weigh, but never an automatic
    win. Dedup runs **before** the crowding check, so repetition doesn't masquerade as crowding
    (§8); un-cleaned duplicates are harmless in the meantime — they are `same_claim` by definition,
    and a pure pool never splits.

**Fast runtime (reading the base)**
12. On a question, the runtime **retrieves** relevant pages and, if any clear the **confidence
    floor**, asks the fast model to phrase an answer.
13. If nothing clears the floor, the runtime **abstains** ("I don't know") rather than answer from a
    weak match.
14. Every response separates **`answer`** (the model's words) from **`why`** (a readout of which
    pages/genes/provenance it used). The `why` is derived from state, never from the model's prose.
15. Every response also carries a **retrieval trace** — the machine-readable record of what the
    lookup actually did: the query terms probed, which pages matched at what strength, and what the
    confidence floor admitted or blocked (or that it abstained). The `why` prose and the shell's
    lookup panel are both rendered *from the trace*; nothing about retrieval is ever reconstructed
    after the fact or phrased by the model.

**The chat app**
16. `kaineros` starts a REPL; plain text is a conversation turn; `/help`, `/notebook`, `/why`,
    `/forget`, `/quit` work as slash commands.
17. After a turn, the slow compiler consolidates the buffer into the base (so the base grows as you
    talk). The contract is **exactly-once**: the session owns the buffer and a **compiled marker**;
    each pass hands the compiler only the turns beyond the marker, then advances it — a turn is
    extracted once, ever (saying a thing again is a *new* turn; §11's cleanup merges it later).
    The count of turns beyond the marker is the backlog the deep-brain panel shows (§18). The
    buffer itself is conversational context for the fast model's phrasing, not memory — memory
    lives in the store. Consolidation may lag, and a fact is only served once **promoted** — so
    there is a window where something you just said is not yet answerable (the mind abstains, or
    still serves the old page). This **freshness gap** is the accepted cost of selection in v1:
    stability is what earns a fact its place, and the lag is the price. (Closing it — retrieval
    over the un-compiled buffer and the pools — is designed but deferred; see Later in
    `docs/tasks.md`.)
18. The shell is a **two-speed cockpit**, stacked bottom-up the way attention flows: the input line
    at the bottom; the conversation (your turn + the answer) just above it; above that, one panel
    per brain. **Top-left, the deep brain's report** (bars and numbers — done, doing, next): what
    the last pass did (merged / split / fused / promoted tallies), the backlog (turns awaiting
    compilation), and the population (genes, pool sizes, each top's wins toward `promote_after`,
    promoted pages marked). **Top-right, the fast brain's report** (this turn): the lookup it
    issued (probed terms, hits, floor decisions — a rendering of the trace, §15) and what was
    handed to the fast model to phrase (pages used, buffer size / fill). You watch selection on
    the left and retrieval on the right while you chat.
19. Each brain funds its panel with a **receipt, not narration**: the fast brain's is the
    retrieval trace (§15); the deep brain's is a **compile report** — a machine-readable tally the
    compiler returns from each housekeeping pass (merged, split, fused, promoted, backlog). The
    panels are readouts of these plus the store — the same grounding rule as `why`: never the
    model's prose. A `--plain` mode keeps the line-based REPL (no panels) for scripts, pipes, and
    the CLI smoke tests.

See `docs/plan.md` for the components and `docs/tasks.md` for the build order.

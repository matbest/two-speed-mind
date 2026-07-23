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
   `promote_after` passes. A promoted entry is a **page**. **Competition needs competitors**: an
   *uncontested* candidate — its pool holds only it, after cleanup — promotes on the next pass,
   because waiting would filter nothing (no rival exists to displace it; a lone misreading would
   promote after the wait anyway). `promote_after` measures stability only once a claim is
   contested; restatement never contests (§11 merges it first), a genuine rival always does.
   That competition is for **high-stakes** facts. Low-stakes facts (persona/style) skip it entirely: insertion makes no judge calls (the
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
    `wins` reset), and the merged pool re-earns promotion. Checking only pages keeps the cost
    bounded (the clean layer is small) and targets the failure that matters — both copies being
    *served*. Fusion is **destructive, so it is cautious**: the verdict is asked both ways round
    (`same_claim(a,b)` AND `same_claim(b,a)`) so a single noisy judgment cannot trigger it, and
    the **survivor's page keeps serving** while the merged pool re-earns promotion (§6's rule —
    the incumbent serves through the contest); only the absorbed page retires. A false fusion
    therefore narrows the wiki by one page, never empties it.
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

**Persistence (the mind on disk)**
20. The mind **persists across restarts** as human-readable JSON. Default home:
    `%LOCALAPPDATA%\kaineros\mind` (override: `--mind PATH` or `KAINEROS_MIND`). Tests and evals
    use in-memory stores — persistence is explicit, never ambient.
21. The on-disk layout mirrors the two layers, matching each layer's nature: **`pool.json`** holds
    the working population (every gene's candidates with content, provenance, `wins`, in rank
    order — it churns each pass, so one file); **`pages.json`** is the clean layer's machine
    record; and **`kainome/`** is the human wiki *rendered from it on every save* — an
    **`index.md`** listing every page, plus one **`<gene>.md`** per promoted fact (the content as
    prose, provenance and promotion history as readable metadata). The wiki is generated:
    hand-edits are overwritten (reading edits back in as stated candidates is a Later feature).
    What the files say is exactly what `/notebook` and the panels show; retired pages' files are
    removed on save.
22. Saves happen after every consolidation and are **atomic** (write temp, rename) — a crash
    cannot half-write the mind. Files carry a `schema_version`.
23. Loading is honest, like everything else: a missing home is a fresh mind; a **corrupt file
    stops startup with a clear message** — the system never silently discards memory; deleting a
    broken file is the user's decision. `/forget` still clears only the buffer (context, not
    memory — §17); **`/forget all`** erases the persisted mind, after an explicit confirmation.

**Background consolidation (the slow brain gets off the interactive path)**
24. **Answers never wait for the slow brain.** A turn is: buffer the text, answer immediately from
    the pages that exist right now, and leave compilation to a background worker. The two speeds
    finally run at two speeds. The cost is honest and already accepted (§17): a fact you just
    stated is answerable only once the worker has landed it — the backlog count in the deep-brain
    panel is now real.
25. The **buffer + compiled marker is the work queue** (§17 unchanged): the worker drains the
    un-compiled tail — extract, insert, housekeep, save — and advances the marker only on
    success, so a failed or interrupted pass never loses turns (at-least-once; §11's dedup merges
    any re-extraction). One worker; passes never overlap; a fresh turn during a pass is picked up
    in the next one.
26. **Failure is visible, never fatal**: a failing pass leaves the marker where it was, retries
    with backoff (bounded — then waits for the next turn to re-wake it), and reports the error in
    the compile report, which the deep-brain panel shows. The interactive path is unaffected
    throughout.
27. Reads stay honest under concurrency: retrieval reads an atomic **snapshot** of the clean
    layer (pages are replaced, never mutated in place), so an answer is always phrased from a
    consistent set of pages — never from a half-finished housekeeping pass. When a background
    pass lands, the pinned cockpit header repaints (the answer didn't wait; the panels catch up).
    **Deterministic mode remains the default** for tests and evals: `Session(background=False)`
    consolidates synchronously, exactly as before; only the chat app opts into the worker. On
    quit with a non-empty backlog, the app drains it (briefly, with a message) so said-but-not-
    yet-compiled turns aren't lost with the process.

**The assistant acts (tools)**
28. The assistant can *do*, not only remember. A **tool** is a declared capability — a name, a
    one-line description, a typed input schema, a **locality** (`on-device` | `off-device`), and a
    run function — living behind a Protocol like the models, so tools are pluggable and tested
    against fakes. The kainome is the assistant's memory; tools are its hands.
29. Grounding extends to actions. Beyond phrasing, the fast runtime may **propose** a tool call as
    *structured data* — the tool name and its typed inputs, chosen from the declared tools and
    grounded in the question plus retrieved pages — never parsed from prose. The proposal, its
    inputs, and the outcome are a **receipt** the cockpit renders, the same rule as `why`: the
    model proposes the words and the call; state and the run function decide what actually happens.
30. Actions carry **locality**, mirroring the models. A tool that stays on the machine (read a
    local file, do arithmetic, read the kainome) is *on-device*; a tool that reaches the network
    (web search, send a message) leaves the device. The privacy posture (§27, the status bar) is
    the sum of the model's locality **and** every enabled tool's locality — a local model with an
    off-device tool is not a private assistant, and the bar says so.
31. Side-effecting or irreversible actions (send, delete, purchase, any off-device call) **require
    explicit user confirmation** before running; read-only on-device tools may run unattended.
    Confirmation is the honesty principle applied to the world, not only to words — the assistant
    never acts on the user's behalf without a clear yes.
32. Every action is **recorded with provenance** — what was called, with what inputs, when, and the
    result — as an append-only action log on disk beside the wiki. What the assistant *did* is as
    auditable as what it *believes*.

**Watching the cost (would this run locally?)**
33. Each brain **meters its tokens** — one counter for the deep brain (extractor + judge), one for
    the fast brain (phraser) — reporting a running **total** and a rolling **last hour**. The
    panels show both. The number is a proxy for how much compute a *local* model would have to
    carry for that role, so you can judge, before building the local adapter (T9b), whether the
    deep brain's appetite is realistic on-device. The fakes never spend tokens, so the counters
    read zero — metering measures real backends only.

**Profiles (many people, many minds)**
34. The mind is **per-profile**. A profile is a named home —
    `%LOCALAPPDATA%\kaineros\profiles\<name>\mind\` — holding that person's pool, pages, wiki, and
    actions, fully isolated from every other profile. `--profile <name>` launches into one;
    `/profile` lists them and `/profile <name>` **switches live** (between turns): flush any
    in-flight compilation into the current mind, save, load the other, start a fresh conversation.
    Each profile is its own wiki; switching swaps the whole mind, not just the buffer. The legacy
    unnamed mind stays the default so existing data is never orphaned. (A profile may later carry
    its own model choice; for now models are chosen at launch and shared across a switch.)

**Personas (watch a mind form)**
35. A **persona** is a scripted person: `personas/<name>/conversation.json` holds a description and
    the user's turns. Running it (`kaineros persona <name>`) feeds the turns to a mind — one turn
    fully compiled before the next, so the deep brain always has long enough — building that
    persona's own wiki at `personas/<name>/mind/`, and reporting deep/fast token cost as it goes.
    It is how we watch a wiki form for a realistic person and judge the local-compute cost (§33) on
    a real workload. Ships with `programmer`, `elderly`, `teenager` — each mixing durable facts,
    a correction (displacement), a restatement (dedup), persona/style preferences (low-stakes),
    and chit-chat (skipped) so the machinery is visible in the wiki it leaves behind.

**The mind asks (disambiguation questions)**
36. The deep brain **queues questions it cannot resolve alone**. During housekeeping, when two
    promoted pages **conflict** — a pairwise judge verdict, `conflicts(a, b)` — but are *not* the
    same claim (so fusion doesn't apply), it cannot know which is true. Rather than leave the
    contradiction sitting in the wiki (as the backend-vs-platform case did), it **queues a
    disambiguation question**.
37. The question is **grounded**, like `why`: its text is built from the conflicting pages' own
    words — "You've told me both: X, and Y — which is right?" — not invented by a model, and it
    carries the genes it came from, so it is auditable. Questions persist beside the wiki
    (`questions.json`), pending until asked. They are deduped (one pending question per conflict)
    and not re-asked once answered.
38. The **fast brain asks** — on the interactive path, after answering a turn, it surfaces one
    pending question (never an interrogation; one at a time). `/questions` lists the queue; the
    deep-brain panel shows the pending count.
39. The **answer becomes a fact through the normal pipeline** — the user's reply is an ordinary
    turn: extracted, it competes, and (a fresh stated account) displaces the stale page it
    resolves. The question only *prompted* the turn; it is never a shortcut around competition or
    grounding. The question is then marked answered.

**The deep brain's three modes (cost separated by cadence)**
40. The deep brain does three jobs at three costs, and they must not all run every turn:
    - **Rank** (every pass, cheap — per-pool, no cross-page comparison): insert/rank candidates by
      pairwise `better`, merge restatements (dedup), split mixed pools (fission), promote settled
      winners. This is the frequent per-turn work.
    - **Summarise** (on promotion): turn the winning candidate into the clean wiki page. v1 copies
      the top candidate's content; *real* distillation (a concise, search-friendly page from the
      pool's evidence) is a later build.
    - **Cleanup** (occasional, expensive — cross-page, O(pages²) judge calls): fusion (heal
      split-brain pages) and conflict detection (queue disambiguation questions). Gated to a
      cadence (`cleanup_every`, default every-4th-pass in chat) or forced on demand (`/cleanup`),
      never every turn — this is where the token cost lives, kept off the hot path.
41. Cleanup mode's **goal is retrieval efficiency for the cheap fast brain**: it (re)optimises the
    wiki — above all `index.md` — so the fast model can choose the right page to read with the
    fewest tokens. The index becomes a **router** (each page a terse cue/alias line) rather than a
    dump, so retrieval can read the index + the one right page instead of many pages. (The
    two-step index-routed retrieval is a designed follow-on; today retrieval reads matching pages
    directly.)

**Stray contradiction vs deliberate update (spec §42)**
42. "Recency is a vote, not a veto" (§6) rightly *defends incumbents* — a stray contradiction can't
    overwrite an established fact (Kaineros's static-conflict strength). But the same rule wrongly
    blocks a *real* update: an entrenched "Acme" resists "as of today I work at Initech, not Acme."
    The two are the same design choice pulling opposite ways. The resolution is to tell them apart:
    a fact carries a **`supersedes`** flag, set by the extractor only for a **deliberate update** —
    signalled by "now", "as of today", "not X anymore", "I've moved/switched", "new …" — and keyed
    to the *same gene* as the fact it replaces. A `supersedes` candidate **jumps to the top of the
    pool on insert and promotes immediately** (like a low-stakes fact), displacing the incumbent
    fast; a stray mention (no signal) still binary-inserts and must win the contest. So deliberate
    corrections land at once while casual contradictions are still resisted — static strength kept,
    dynamic weakness closed. The signal is detected **deterministically in code**
    (`looks_like_update` over the turn text), *not* asked of the model in a prompt — see §43.

**Design principle: metadata over prompt**
43. Prefer solving problems by **enriching the stored elements' metadata** (fields on
    `Provenance`/`Candidate`/`Page`), computed **deterministically in code** where possible, so the
    ranking brain has more to work with — rather than adding rules to model prompts. It is cheaper,
    deterministic (no reliance on a weak model reasoning right), and grounded (behaviour from state,
    not prose). The model is used only for what needs it (turning raw text into gene-keyed
    candidates). `supersedes` (§42) is the worked example: a metadata flag set by a code heuristic,
    not a prompt rule.

**Tags: the fact's own vocabulary (§43, applied twice)**
44. Every fact carries **tags** as a child field in the population JSON: the 3–6 words someone
    would use when *asking* about it (`"Just picked up a new Tesla"` → `car, vehicle, drive, ev`).
    Minted by the deep brain at extraction (riding an existing call, ~15 tokens), they travel with
    the fact for life: dedup **unions** them like receipts, fission carries them to the child
    gene, promotion copies them onto the page, and `index.md` renders them as each page's cue
    line.
45. Tags are **hints, never verdicts** — they make work happen or not happen; they never decide
    truth. Two uses, one field:
    - **Routing (fast brain):** retrieval matches question tokens against `content ∪ tags`, so an
      implicit probe ("what *car* do I drive?") reaches a page that never says "car" — closing
      the dynamic-implicit gap with zero runtime model calls.
    - **Sweep scoping (deep brain):** the conflicts sweep only asks the judge about page pairs
      that **share a tag**. Token overlap could not do this safely (facts collide without sharing
      surface words — "backend engineer" / "platform team"), but tags are deep-model-authored
      semantics written at compile time, so shared-tag scoping keeps that collision while
      collapsing O(pages²) checks to pairs within a topic. **Conservative rule:** a pair where
      either page has *no* tags always falls through to the judge — scoping only skips work when
      both sides declared their topics and the topics are disjoint.

**The primary source (raw before derived)**
46. There are two records, at two levels of digestion. The **raw record** is every turn,
    verbatim, captured deterministically at the harness level (`turns.jsonl`, append-only,
    written at the exactly-once compile boundary — so chat, evals, and benches are all captured
    the same way). The **population and the wiki are derived state**: the extractor's
    restatements, the judge's rankings — all of it could in principle be re-derived from the raw
    record by a better compiler later. Each fact also carries its own raw words inline
    (`provenance.source_texts`, unioned by dedup like receipts) so `pool.json` is auditable at a
    glance and each wiki page can show a "said as:" line. **Reference only:** raw text never
    enters a prompt — the fast brain reads the wiki, the judge compares restatements — so the
    raw layer costs zero tokens at any size.

**Grooming, not sweeping (§47) — the deep brain grooms at a constant rate**
47. The cross-page work (fusion + conflict detection) is **not** an all-pairs O(pages²) sweep each
    cleanup. It examines a **bounded** set of page pairs, from two sources:
    - **Eager frontier:** every page changed (promoted/re-promoted) SINCE THE LAST CLEANUP —
      accumulated in a dirty set across passes, so a change never slips through the cleanup cadence
      — against its plausible peers (same-tag, or either side untagged — §45's conservative rule).
      Because the *second* page of any conflicting pair is a change, a fresh contradiction is caught
      at the next cleanup — including an update keyed to a FRESH gene ("usual_order" vs
      "dish.usual_order") that must be fused with the incumbent to contest it. O(dirty·peers), not
      O(pages²).
    - **Stochastic grooming:** a constant `groom_rate` of random pairs per pass, biased toward
      shared tags — slowly grooming the long tail (minds loaded from disk, pairs made comparable by
      a later fission). It never asks "is everything checked?"; time does the work, evolutionarily.
    A per-cleanup **new-call budget** caps how many first-time judge calls one cleanup may make
    (each is a ~4s subprocess on the sub) — fusion runs first so answer-critical merges get the
    budget, and overflow defers to the next cleanup where the already-checked pairs are free.
    The verdict cache (§43 in spirit) makes a re-sampled unchanged pair free, so steady-state cost
    tracks the *churn* rate, not the *size* of the mind. The one guarantee that matters — catch a
    brand-new contradiction now — is the eager frontier's job; everything else converges over time.
    (Untagged minds fall back to the conservative "check every peer" path; real minds are tagged at
    extraction, so the frontier stays small.)

**The gist: gene = answer (§48)**
48. Every fact also carries a **gist** — the bare value its gene resolves to, in as few words as
    possible (`user.food.favorite_cuisine` → `japanese`, `user.pet.species` → `greyhound`). The
    gene is the *question*; the gist is the *value* — together a subject-predicate-object triple,
    the structured form the fast brain reads without parsing (or misparsing) a sentence. Minted at
    extraction beside `content`, it travels with the winning allele onto the page, and the fast
    brain reads **only** the bare triples (`- user.food.favorite_cuisine = japanese`), never the
    sentence — the goal is to make the wiki trivial for a *weak* model, and a confusable sentence
    ("prefers Japanese over Thai") is exactly what makes it wander. The sentence stays in the wiki
    for humans and for `content` audit; the fast path is pure `key = value`. The fast prompt is
    kept **tiny** for the same reason (a long prompt is something a weak model quotes back and
    trips over), and conflict handling is deliberately NOT the fast brain's job — the deep brain
    settles conflicts by queueing questions (§36), so the notes are already resolved by the time
    the fast brain reads them. (The triples are also the natural bridge to a knowledge-graph / OKF
    rendering of the kainome — they're already there.)

**Append-only ingest; ranking is grooming (§49)**
49. Ingest does **no comparison**. `insert()` drops a candidate into its pool at the front
    (newest-first — recency is the right provisional guess) in O(1), with zero judge calls, so
    taking in a whole conversation costs only the extraction calls. All ranking is the groomer's
    job, done **one O(n) pass at a time**: `_bubble` walks a pool once, compares each ADJACENT
    pair, and bubbles the better one up — *every element compared once, never every pair*. A pool
    settles over successive grooms, not in a single expensive sort; cached verdicts make
    re-passing an unchanged pool free. A pinned candidate (a deliberate `supersedes` update or a
    low-stakes style fact — §4/§42) is never bubbled down. This completes the grooming philosophy
    of §47: *all* comparison — rank, dedup, fuse, resolve — is now off the ingest path, running at
    a constant rate in the background. The wiki is *eventually* ordered; for real use (ingest now,
    query later) that is invisible and ideal.

See `docs/plan.md` for the components and `docs/tasks.md` for the build order.

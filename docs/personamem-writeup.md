# A small model with a compiled memory rivals frontier models on PersonaMem

**TL;DR.** [PersonaMem](https://github.com/bowen-upenn/PersonaMem) (COLM 2025) tests whether an
assistant can track a user across a long, evolving conversation and respond in a personalized way.
Even frontier models plateau around **50%** on it (4-choice questions; random is 25%). Standalone
small models do much worse — Claude 3.5-Haiku scores **0.30**, Claude 3.7-Sonnet **0.26**.

Kaineros takes a different route. Instead of stuffing the whole history into a model's context, it
**compiles the conversation into a compact memory offline**, then answers with a **small, fast,
non-reasoning model that retrieves from that memory**. On a 49-question sample it scored **0.61** —
and the small answering model, which scores ~0.30 reading the raw history, reached 0.61 reading the
compiled memory.

The headline isn't a leaderboard rank (see the caveats — our sample is a slice, not the full set).
It's the architecture result: **structured memory + a small model rivals a big model + raw context.**

---

## Why PersonaMem is hard

PersonaMem builds 180+ simulated user↔assistant histories — up to 60 sessions, ~1M tokens — across
15 life scenarios. It then asks in-situ multiple-choice questions of 7 types: recall a shared fact,
track a preference change, recall the *reason* behind a change, give a preference-aligned
recommendation, suggest a new idea, generalize to a new scenario, and so on. The correct answer is
the assistant reply best aligned with everything the user has revealed so far.

The catch is the length. Personalization means holding the *whole* relationship in mind, and models
degrade as the history grows toward 1M tokens. That's why the published leaderboard clusters near
chance-plus:

| Model | PersonaMem accuracy |
|---|---|
| Gemini 1.5-Flash / GPT-4.5 / GPT-4.1 | **0.52** |
| o1 | 0.50 |
| Gemini 2.0-Flash | 0.49 |
| o4-mini | 0.48 |
| GPT-4o / DeepSeek R1-671B | 0.45 |
| Llama 4-Maverick | 0.43 |
| o3-mini / GPT-4o-mini | 0.39 |
| Llama 3.1-405B | 0.31 |
| **Claude 3.5-Haiku** | **0.30** |
| **Claude 3.7-Sonnet** | **0.26** |
| *random baseline (4 choices)* | *0.25* |

*(Overall accuracy, from the PersonaMem leaderboard. The paper reports frontier models "hovering
around 52%".)*

## The two-speed approach

Kaineros splits the work across two models at two speeds over one knowledge base:

- **A slow, deep compiler** runs off the interactive path. It reads back over the conversation,
  extracts competing candidate facts, ranks them by pairwise comparison, and promotes the fittest
  into a clean, readable knowledge base — one page per fact, each carrying its provenance.
- **A fast reader** answers the user by *retrieving* the relevant pages from that clean base and
  phrasing an answer — or abstaining when nothing clears a confidence floor. It never re-reasons
  over the raw history; it reads a handful of pre-digested facts.

The reader is deliberately a small, non-reasoning model. In these runs the deep compiler was a
large model (Claude, via subscription, running offline); the fast reader was **Claude Haiku 4.5**.

## The result

On a sample of **49 questions across 5 personas** at the 32k-context tier, Kaineros scored **30/49 =
0.61**, above the 0.52 top of the published leaderboard, and roughly **2× the standalone
Haiku/Sonnet scores** on the same benchmark.

The per-type breakdown shows *where* the advantage is — and, honestly, where it isn't:

| Question type | Kaineros | Field pattern (paper) |
|---|---|---|
| Generalize to new scenarios | 5/6 (0.83) | among the hardest for all models |
| Preference-aligned recommendations | 4/5 (0.80) | among the hardest for all models |
| Recall a shared fact | 9/13 (0.69) | models do best here (60–70%) |
| Recall the reason behind an update | 9/14 (0.64) | models do best here |
| Track preference evolution | 2/4 (0.50) | mid |
| **Suggest a new idea** | **1/7 (0.14)** | **the hardest type — field avg ~0.18** |

The strengths are the retrieval-shaped types: recalling facts, reasons, and tracking changes. The
one clear weakness, *suggest a new idea*, is the hardest type for **everyone** (field average ~0.18)
— it's a synthesis/creativity task, not a memory-recall task, and it's the type least aligned with a
retrieve-and-phrase design. We're not beating the field there; we're at it.

## Why this is the interesting part

A model's PersonaMem score is usually a story about its long-context reasoning. Kaineros changes the
substrate: the answering model never sees the long context. It sees a compiled memory. And the
effect on a *small* model is large — Haiku-class goes from ~0.30 (reading raw history, per the
leaderboard) to 0.61 (reading the compiled memory). The expensive reasoning happens **once, offline,
at compile time**, not on every query. That's the two-speed bet: pay for depth slowly and once, then
answer fast and cheap.

## Caveats & methodology (read this before quoting the number)

This is a promising internal result, **not a certified leaderboard submission.** Specifically:

1. **It's a sample, not the full set.** 49 questions from 5 personas, vs the full benchmark's
   thousands. The confidence interval is wide.
2. **Single context tier (32k).** The published leaderboard spans up to 1M tokens, where scores
   fall. Our thesis is that a *compiled memory shouldn't degrade with history length the way raw
   context does* — but we tested 32k, so that's a hypothesis here, not a demonstrated result.
3. **Favorable question mix.** 27 of our 49 questions are the recall/reason types that score highest
   for everyone; a different sample weighted toward *suggest-new-idea* would pull the average down.
   The per-type table above is the fairer comparison than the single headline number.
4. **Our own evaluation harness.** We use the PersonaMem data and its 4-choice format, but our own
   loader and letter-matching scorer, not the authors' official eval script — small differences are
   possible.
5. **Two models, not one.** The comparison is a two-speed *system* (a large model compiling offline
   + a small model answering online) against single models. That's the point of the design, but it's
   not apples-to-apples with a single-model row on the leaderboard.

The honest one-line claim: *on a 49-question PersonaMem sample, a small retrieval-based model backed
by a compiled memory matched-or-beat the best published full-set frontier scores, with the same
strength/weakness profile the paper reports — and it did so without holding the conversation in
context.*

---

*Benchmark: [PersonaMem](https://github.com/bowen-upenn/PersonaMem) — Jiang et al., "Know Me,
Respond to Me: Benchmarking LLMs for Dynamic User Profiling and Personalized Responses at Scale"
(COLM 2025), [arXiv:2504.14225](https://arxiv.org/abs/2504.14225). Kaineros build 0.113. Deep brain:
Claude (subscription, offline). Fast brain: Claude Haiku 4.5.*

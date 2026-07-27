# A private, on-device memory that answers for 1/36th the cost of stuffing your history into a frontier model

**TL;DR.** Personalization needs an assistant to remember a long, evolving history of you. The usual
way to do that — pour the whole conversation into a big model's context window on every question —
is both **expensive** (it re-reads everything, every time) and **privacy-hostile** (your entire
history is shipped to a cloud provider on every query). Kaineros takes the other road: it **compiles
your history into a compact, human-readable memory that lives on your own machine**, and answers with
a **small, fast model** that retrieves from it.

On a slice of the [PersonaMem](https://github.com/bowen-upenn/PersonaMem) benchmark, that small model
+ compiled memory answers each question with **~36× fewer tokens** than a frontier model reading the
raw history — and keeps the data local. It gives up some raw accuracy at short context (0.61 vs 0.78,
below) to do it. The bet, backed by independent work, is that the compiled-memory approach *pulls
ahead* as histories grow long — exactly where context-stuffing gets slow, costly, and worse.

---

## The problem: remembering you is unsolved, and the brute-force fix is costly and public

[PersonaMem](https://arxiv.org/abs/2504.14225) (COLM 2025) tests whether an assistant can track a
user across a long, evolving conversation and respond in a personalized way. Frontier models plateau
around **50%** on it — GPT-4.5 / GPT-4.1 at 0.52, and on the harder
[PersonaMem-v2](https://arxiv.org/abs/2512.06688) (Dec 2025) even **GPT-5-Chat reaches only 45.6%**.
The task is genuinely hard because personalization means holding the *whole* relationship in mind,
and models degrade as the history stretches toward a million tokens.

The industry's answer is to shove more of the history into the context window. That has two costs
people rarely price in:

- **Compute.** Re-reading tens of thousands of tokens of history on *every* question is enormous,
  repeated work. It scales with how much you've ever said, not with the question.
- **Privacy.** To answer "what should I cook tonight?", a context-stuffing assistant sends your
  entire life's conversation to a cloud model. Every query re-exports everything.

## Kaineros: compile once, answer cheap, keep it local

Kaineros splits the work across two models at two speeds over one knowledge base:

- **A slow, deep compiler** runs off the interactive path (overnight, or between sessions). It reads
  back over your conversation, ranks competing candidate facts, and promotes the fittest into a
  clean, **human-readable knowledge base on your own disk** — one page per fact, each carrying its
  provenance.
- **A fast reader** answers you by *retrieving* the handful of relevant pages and phrasing an answer
  — or honestly abstaining. It never re-reasons over the raw history; it reads a few pre-digested
  facts. In these tests the reader was **Claude Haiku 4.5**; the offline compiler was a larger model.

The expensive reasoning happens **once, at compile time** — not on every query.

## The head-to-head (same benchmark, same 49 questions, same scorer)

We ran the two-speed system against a single frontier model reading the raw history, on 49 questions
across 5 personas at the 32k-context tier:

| | Answering model | What it reads | Accuracy | Tokens / answer |
|---|---|---|---|---|
| **Frontier baseline** | Opus 4.8 | full raw history | **0.78** | **~18,600** (every query) |
| **Kaineros** | Haiku 4.5 | compiled memory | 0.61 | **~518** |

Two honest readings of this table:

1. **At 32k context, the frontier model is more accurate** (0.78 vs 0.61). We do not claim to beat it
   on raw accuracy at short history — where the whole conversation still fits comfortably in context,
   brute force works well.
2. **Kaineros answers each question for ~1/36th the tokens, on a much smaller model, with the data
   kept local.** The deep reasoning was paid once at compile; every answer after that is cheap. That
   is the trade: near-ballpark accuracy at a fraction of the per-query cost, privately.

## Why the trade tilts toward Kaineros as history grows

The 32k tier is context-stuffing's *best* case — short enough that the full history fits and stays
cheap. The interesting regime is long histories, and there the evidence favors compiled memory.
PersonaMem-v2's own authors built a compiled-memory system and report it **distilling a long history
into a 2k-token memory, scoring 55% while using 16× fewer input tokens — beating GPT-5-Chat's
45.6%.** That's independent corroboration, from the benchmark's own team, that a compact memory
outperforms raw context once the history is long. Kaineros is a local, human-readable, privacy-first
take on the same principle.

## Privacy is the point, not a footnote

Because the memory lives on your machine as a readable wiki:

- **Fully local** (on capable hardware): nothing leaves the device — the real "your data stays
  yours."
- **Cloud-assisted** (on a small device): the cloud does the heavy compile, but it sees only the few
  compiled facts needed for one answer, statelessly — never your whole history. Contrast that with
  shipping your entire conversation into a context window on every query.

And every answer is auditable: it traces back to the specific pages retrieved, each with provenance
— so the reason for an answer is grounded in what's actually stored, not improvised.

## Caveats (read before quoting a number)

- **Small sample, single context tier.** 49 questions, 5 personas, 32k only — a slice, not the full
  benchmark. Treat the accuracy figures as indicative, not certified.
- **Our own harness.** We use PersonaMem's data and 4-choice format but our own loader and scorer,
  not the authors' official eval script.
- **A system, not a single model.** The comparison is a two-speed *system* (a large offline compiler
  + a small online reader) against one model reading raw context — which is the whole design, but not
  a like-for-like leaderboard row.
- **The long-context advantage is argued, not yet shown by us.** We measured 32k (context-stuffing's
  easy case) and cite PersonaMem-v2 for the crossover; we haven't run the 128k / 1M tiers ourselves.

The honest one-liner: *a small model reading a compiled, on-device memory answers PersonaMem
questions at ~1/36th the per-query token cost of a frontier model reading the raw history, trading
some accuracy at short context for cost and privacy — and independent work suggests the compiled
memory pulls ahead as histories grow.*

---

*Benchmark: [PersonaMem](https://github.com/bowen-upenn/PersonaMem) (COLM 2025,
[arXiv:2504.14225](https://arxiv.org/abs/2504.14225)) and
[PersonaMem-v2](https://arxiv.org/abs/2512.06688). Kaineros build 0.123. Offline compiler: Claude
(subscription). Fast reader: Claude Haiku 4.5.*

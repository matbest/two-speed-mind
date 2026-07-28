# Compiled memory vs. raw context: does a wiki help a small model remember you?

*Two-Speed Mind / Kaineros — internal study, PersonaMem v1 & v2, July 2026.*
*Rendered version: https://claude.ai/code/artifact/b58e6ab0-9282-45e7-8547-b44bdaa6a490*

**Kaineros** compiles your conversations into a readable knowledge wiki, then answers with a small,
fast model that *retrieves* from that wiki instead of re-reading your whole history. We benchmarked
whether the wiki makes a small model better — and whether it's fast enough to feel like an assistant
— on [PersonaMem](https://github.com/bowen-upenn/PersonaMem).

## The finding

On our 32k slices, a small model was **more accurate reading the raw history than the compiled wiki**
— on *both* benchmarks (v1: 0.73 vs 0.61; v2: 0.50 vs 0.41). The wiki isn't an accuracy win; it's a
**speed, cost, and privacy** win. Reading the raw history took **~23–28 seconds per answer** — far too slow to feel responsive —
while the wiki answers fast, ~20–40× cheaper, and never leaves the
machine. It holds up on plain fact recall but loses where the answer needs the raw *narrative* (why a
preference changed, how it evolved) or an implicit detail — because compiling flattens exactly those.
The fix: let the fast search reach raw snippets on demand — raw-context accuracy at wiki speed.

## What we compared

Three ways to answer the same multiple-choice questions, scored the same way:
- **Kaineros** — a small model (Claude Haiku) reading the **compiled wiki**; compilation happens once, offline.
- **Haiku, raw history** — the *same* small model handed the full raw conversation. Isolates what the wiki adds.
- **Opus, raw history** — a frontier model reading the full history. A ceiling reference.

## The numbers

**PersonaMem v2** — 5 personas, 129 questions, 4-choice (random = 0.25):

| Setup | Reads | Accuracy | Tokens/answer | Latency |
|---|---|---:|---:|---:|
| **Kaineros (Haiku)** | compiled wiki | **0.41** | ~490 | ~1.4s |
| Haiku | raw history | 0.50 | ~8,500 | ~28s |
| Opus | raw history | 0.67 | ~9,400 | ~28s |

**PersonaMem v1** — 5 personas, 49 questions:

| Setup | Reads | Accuracy | Tokens/answer | Latency |
|---|---|---:|---:|---:|
| **Kaineros (Haiku)** | compiled wiki | **0.61** | ~520 | ~1.6s |
| Haiku | raw history | 0.73 | ~14,300 | ~23s |
| Opus | raw history | 0.78 | ~18,600 | ~23s |

The same pattern both times: the same small model is more accurate on the raw history (0.73, 0.50)
than on the compiled wiki (0.61, 0.41). The wiki trades accuracy for speed.

## Reading the results

**The wiki was less accurate than raw history on both benchmarks — consistently, not by task.** The
explicit-vs-implicit split we expected did not appear: the small model was more accurate on raw
history on both slices. Where the wiki loses is *type*, not benchmark — it holds up on plain fact
recall but drops the questions that need the raw **narrative**: on v1, reasons-behind-a-change (9/14
vs 13/14) and evolution (2/4 vs 4/4); on v2, implicit details like health (0.47 vs 0.82) and
stereotype cues (0.27 vs 0.64). Compiling into clean facts flattens the sequence-and-context those
answers live in.

**But raw context isn't a usable assistant.** Every raw-history setup cost ~23–28s/answer — the model
must *read* tens of thousands of tokens before writing a word (the prefill). Kaineros reads a few
hundred tokens of wiki and answers fast. On time-to-first-word — the metric that decides whether
something feels like an assistant — raw context loses outright. The trade-off: the wiki gives up
roughly ten points of accuracy to be the only responsive, low-cost, and private option.

## What it points to

The wiki is fast but drops the narrative and implicit detail; raw history keeps them but is too slow.
The synthesis is a memory that holds both and searches whichever the question needs: the small model
browses the wiki **step by step**, and when the clean facts don't settle a question it opens the
**relevant raw snippet** on demand — a few hundred tokens, not tens of thousands. Plain recall stays
fast on the facts; narrative and implicit questions recover the raw signal without the ~25s tax.
That's the next build (spec §52), and this study is the evidence for it.

## Caveats

- Small samples (129 / 49 questions, 5 personas each) — a slice, not the full benchmark.
- Our own harness (PersonaMem's data + format, our loader/scorer) — so our Opus figures aren't
  directly comparable to published numbers like GPT-5's 45.6% on full v2.
- Kaineros is a two-part *system* (large model compiles offline, small one answers); the raw rows are
  single models.
- One context tier (32k); the current one-shot retrieval, not step-by-step search — the fix that
  aims to close the accuracy gap is unbuilt.

# Compiled memory vs. raw context: does a wiki help a small model remember you?

*A finding, not a boast. Two-Speed Mind / Kaineros — internal study, PersonaMem v1 & v2, July 2026.*
*Rendered version: https://claude.ai/code/artifact/b58e6ab0-9282-45e7-8547-b44bdaa6a490*

**Kaineros** is a private, on-device assistant that compiles your conversations into a readable
knowledge wiki, then answers with a small, fast model that *retrieves* from that wiki instead of
re-reading your whole history. We benchmarked whether the wiki actually makes a small model better —
and whether it's fast enough to feel like an assistant — on
[PersonaMem](https://github.com/bowen-upenn/PersonaMem).

## The finding

A compiled memory **helps a small model on explicit recall** and cuts per-answer cost ~20×. On
**implicit** preferences (things you reveal indirectly), the compilation throws away the signal, and
the same small model does *better* reading the raw history. But reading the raw history took **~28
seconds per answer** — a spinner, not an assistant. So the real target is a memory that can reach the
raw conversation *on demand*: raw-context accuracy at compiled-memory speed.

## What we compared

Three ways to answer the same multiple-choice questions, scored the same way:
- **Kaineros** — a small model (Claude Haiku) reading the **compiled wiki**; compilation happens once, offline.
- **Haiku, raw history** — the *same* small model handed the full raw conversation. Isolates what the wiki adds.
- **Opus, raw history** — a frontier model reading the full history. A ceiling reference.

## The numbers

**PersonaMem v2** — 5 personas, 129 questions, 4-choice (random = 0.25):

| Setup | Reads | Accuracy | Tokens/answer | Latency |
|---|---|---:|---:|---:|
| **Kaineros (Haiku)** | compiled wiki | **0.41** | ~490 | fast |
| Haiku | raw history | 0.50 | ~8,500 | ~28s |
| Opus | raw history | 0.67 | ~9,400 | ~28s |

**PersonaMem v1** — 5 personas, 49 questions (earlier, mostly explicit recall):

| Setup | Reads | Accuracy | Tokens/answer |
|---|---|---:|---:|
| **Kaineros (Haiku)** | compiled wiki | **0.61** | ~520 |
| Opus | raw history | 0.78 | ~18,600 |

On v1 the published leaderboard puts a Haiku-class model at ~0.30 on raw context — the wiki roughly
*doubled* the small model on explicit recall. On v2, the picture flips.

## Reading it honestly

**The wiki helps explicit recall and hurts implicit inference.** v1 leans on facts stated outright;
compiling helps. v2 is built on *implicit* preferences, and compilation distils exactly that signal
away. Per type on v2, the memory loses most where fine detail matters — health & medical (0.47 vs
0.82) and stereotype-relevant (0.27 vs 0.64) — while staying competitive or better on sensitive-info
and therapy-background.

**But raw context isn't a usable assistant.** Both raw-history setups cost ~28s/answer — the model
must *read* ~32k tokens of history before writing a word (the prefill). Kaineros reads a few hundred
tokens of wiki and answers fast. On time-to-first-word — the metric that decides whether something
feels like an assistant — raw context loses outright. The compiled memory is the only responsive one.

## What it points to

If compiled facts answer *explicit* questions fast, and raw history answers *implicit* ones
accurately but far too slowly, the synthesis is a memory that holds both and searches whichever the
question needs: the small model browses the wiki **step by step**, and when the facts don't settle an
implicit question it opens the **relevant raw snippet** on demand — a few hundred tokens, not thirty
thousand. Explicit stays fast; implicit recovers the raw signal without the 28-second tax. That's the
next build (spec §52), and this study is the evidence for it.

## Caveats

- Small samples (129 / 49 questions, 5 personas each) — a slice, not the full benchmark.
- Our own harness (PersonaMem's data + format, our loader/scorer) — so our Opus 0.67 isn't directly
  comparable to published numbers like GPT-5's 45.6% on full v2.
- Kaineros is a two-part *system* (large model compiles offline, small one answers); the raw rows are
  single models.
- One context tier (32k); the long-history advantage is argued, not shown here.

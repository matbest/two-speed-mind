# Spec — Two-Speed Mind (prototype)

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

- A real/fast/high-quality local LLM (added last, as an adapter).
- Multi-user, networking, persistence beyond a simple store, a GUI.
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
   `promote_after` passes. A promoted entry is a **page**.
5. A beaten candidate is **not destroyed** — it drops back in the pool and can win again later.

**Fast runtime (reading the base)**
6. On a question, the runtime **retrieves** relevant pages and, if any clear the **confidence
   floor**, asks the fast model to phrase an answer.
7. If nothing clears the floor, the runtime **abstains** ("I don't know") rather than answer from a
   weak match.
8. Every response separates **`answer`** (the model's words) from **`why`** (a readout of which
   pages/genes/provenance it used). The `why` is derived from state, never from the model's prose.

**The chat app**
9. `twospeed` starts a REPL; plain text is a conversation turn; `/help`, `/notebook`, `/why`,
   `/forget`, `/quit` work as slash commands.
10. After a turn, the slow compiler consolidates the buffer into the base (so the base grows as you
    talk). Consolidation may lag; the short-term buffer covers what isn't compiled yet.

See `docs/plan.md` for the components and `docs/tasks.md` for the build order.

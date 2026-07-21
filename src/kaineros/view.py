"""Pure renderers for the cockpit (spec §18–19).

State in, text out — no I/O, no model, no terminal. Each brain's panel renders from its receipt:
the deep brain from a CompileReport + the store, the fast brain from a Lookup trace + the response.
"""
from __future__ import annotations

from .schema import CompileReport, Lookup, Response, Turn
from .store import Store


def deep_panel(report: CompileReport, store: Store, promote_after: int = 3) -> str:
    """The deep brain's report: last pass's tallies, the backlog, and the population."""
    lines = [
        f"last pass: {report.inserted} inserted, {report.merged} merged, "
        f"{report.split} split, {report.fused} fused, {report.promoted} promoted",
        f"backlog: {report.backlog} turns awaiting compilation",
    ]
    genes = store.genes()
    n_candidates = sum(len(store.candidates(g)) for g in genes)
    lines.append(f"population: {len(genes)} genes, {n_candidates} candidates")
    for gene in genes:
        top = store.top(gene)
        if top is None:
            continue
        page = store.page(gene)
        if page is not None and page.content == top.content:
            status = "page"
        else:
            status = f"{top.wins}/{promote_after}"
        lines.append(f"  {gene:<14} {status:<6} top: {top.content}")
    return "\n".join(lines)


def fast_panel(trace: Lookup | None, response: Response | None, buffer: list[Turn]) -> str:
    """The fast brain's report: the lookup it issued and what was handed to the phraser."""
    if trace is None:
        return "no lookup yet - say something"
    lines = [f"probed: {', '.join(trace.query_terms) or '(nothing)'}"]
    if not trace.hits:
        lines.append("  (no pages to examine)")
    for h in trace.hits:
        if h.decision == "admitted":
            detail = f"admitted ({h.confidence} >= {trace.floor})"
        elif h.decision == "blocked":
            detail = f"blocked ({h.confidence} < {trace.floor})"
        else:
            detail = "no match"
        lines.append(f"  {h.gene:<14} {h.strength} hit(s)  {detail}")
    lines.append(f"floor: {trace.floor}")
    if trace.abstained:
        lines.append("abstained - nothing cleared the floor")
    else:
        used = len(response.used) if response else 0
        lines.append(f"sent to phrase: {used} page(s) + {len(buffer)}-turn buffer")
    return "\n".join(lines)

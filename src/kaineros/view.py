"""Pure renderers for the cockpit (spec §18–19).

State in, text out — no I/O, no model, no terminal. Each brain's panel renders from its receipt:
the deep brain from a CompileReport + the store, the fast brain from a Lookup trace + the response.
"""
from __future__ import annotations

from .schema import CompileReport, Lookup, Response, Turn
from .store import Store


def status_bar(label: str, detail: str, offdevice: bool) -> str:
    """The posture strip above the panels: is the mind running on-device, and is it private?

    offdevice is the sum of model locality (and, later, every enabled tool's locality) — true if
    anything the mind does ships the user's words off the machine.
    """
    if offdevice:
        return f"! {label} - {detail} - your words leave this device: NOT local, NOT private"
    return f"* {label} - {detail} - on-device: private"


def deep_panel(report: CompileReport, store: Store) -> str:
    """The deep brain's report: last pass's tallies, the backlog, and the population counts.

    Counts only — the wiki itself is browsable on disk; the panel is a gauge, not a listing.
    """
    genes = store.genes()
    n_candidates = sum(len(store.candidates(g)) for g in genes)
    lines = [
        f"last pass: {report.inserted} inserted, {report.merged} merged, "
        f"{report.split} split, {report.fused} fused, {report.promoted} promoted",
        f"backlog: {report.backlog} turns awaiting compilation",
        f"wiki: {len(store.clean)} pages · pool: {n_candidates} candidates in {len(genes)} genes",
    ]
    if report.error:
        lines.append(f"last pass FAILED: {report.error[:70]} (will retry)")
    return "\n".join(lines)


def fast_panel(trace: Lookup | None, response: Response | None, buffer: list[Turn]) -> str:
    """The fast brain's report: which wiki files the lookup pulled, and what the floor hid."""
    from .persist import _safe  # gene -> the filename you'd browse in kainome/

    if trace is None:
        return "no lookup yet - say something"
    lines = [f"probed: {', '.join(trace.query_terms) or '(nothing)'}"]
    admitted = [h for h in trace.hits if h.decision == "admitted"]
    blocked = [h for h in trace.hits if h.decision == "blocked"]
    if admitted:
        lines.append("pulled from the wiki:")
        for h in admitted:
            lines.append(f"  {_safe(h.gene)}.md  ({h.strength} hit(s), {h.confidence})")
    else:
        lines.append("pulled nothing from the wiki")
    for h in blocked:
        lines.append(f"  blocked: {_safe(h.gene)}.md ({h.confidence} < {trace.floor} floor)")
    if trace.abstained:
        lines.append("abstained")
    else:
        lines.append(f"phrased from {len(response.used) if response else 0} page(s) + {len(buffer)}-turn buffer")
    return "\n".join(lines)

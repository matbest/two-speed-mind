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


def _tokens_line(total: int | None, hour: int | None) -> str | None:
    if total is None:
        return None
    return f"tokens: {total:,} total · {hour:,} last hour"


def deep_panel(
    report: CompileReport,
    store: Store,
    tokens_total: int | None = None,
    tokens_hour: int | None = None,
    pending_questions: int = 0,
) -> str:
    """The deep brain's report: last pass's tallies, the backlog, the population, its token cost.

    Counts only — the wiki itself is browsable on disk; the panel is a gauge, not a listing.
    Token counts (spec §33) show how much this brain would ask of a local model.
    """
    genes = store.genes()
    n_candidates = sum(len(store.candidates(g)) for g in genes)
    lines = [
        f"last pass: {report.inserted} inserted, {report.merged} merged, "
        f"{report.split} split, {report.fused} fused, {report.promoted} promoted",
        f"backlog: {report.backlog} turns awaiting compilation",
        f"wiki: {len(store.clean)} pages · pool: {n_candidates} candidates in {len(genes)} genes",
    ]
    tl = _tokens_line(tokens_total, tokens_hour)
    if tl:
        lines.append(tl)
    if pending_questions:
        lines.append(f"questions to ask: {pending_questions} pending")
    if report.error:
        lines.append(f"last pass FAILED: {report.error[:70]} (will retry)")
    return "\n".join(lines)


def calls_panel(records: list[dict], height: int = 5) -> str:
    """The live wire: the actual text most recently sent to a model. State in, text out.

    Shows the newest call's input verbatim (what we ASK), headed by which brain and why. The
    rendering Panel crops to its height, so a long prompt shows its opening lines — enough to
    watch, in real time, exactly what the two minds are being asked.
    """
    if not records:
        return "no model calls yet - nothing has been asked (the fakes ask nothing)"
    r = records[-1]
    tok = f" · {r['tokens']:,} tok" if r.get("tokens") else ""
    head = f"> {r.get('brain', '?')} · {r.get('purpose', '?')}{tok}"
    body = " ".join((r.get("input") or "").split())  # collapse whitespace so 5 lines show more
    reply = " ".join((r.get("output") or "").split())
    lines = [head, body]
    if reply:
        lines.append(f"=> {reply}")
    return "\n".join(lines)


def fast_panel(
    trace: Lookup | None,
    response: Response | None,
    buffer: list[Turn],
    tokens_total: int | None = None,
    tokens_hour: int | None = None,
) -> str:
    """The fast brain's report: which wiki files the lookup pulled, and what the floor hid."""
    from .persist import _safe  # gene -> the filename you'd browse in kainome/

    if trace is None:
        head = "no lookup yet - say something"
        tl = _tokens_line(tokens_total, tokens_hour)
        return head + ("\n" + tl if tl else "")
    lines = [f"probed: {', '.join(trace.query_terms) or '(nothing)'}"]
    routed = [h for h in trace.hits if h.decision == "routed"]
    matched = [h for h in trace.hits if h.decision == "matched"]  # matched but not read (two-step)
    blocked = [h for h in trace.hits if h.decision == "blocked"]
    if routed:
        lines.append("routed to (read by the fast brain):")
        for h in routed:
            lines.append(f"  {_safe(h.gene)}.md  ({h.strength} hit(s), {h.confidence})")
    else:
        lines.append("routed to nothing in the wiki")
    for h in matched:  # the index routed past these — saved reading them
        lines.append(f"  matched, not read: {_safe(h.gene)}.md ({h.strength} hit(s))")
    for h in blocked:
        lines.append(f"  blocked: {_safe(h.gene)}.md ({h.confidence} < {trace.floor} floor)")
    if trace.abstained:
        lines.append("abstained")
    else:
        lines.append(f"phrased from {len(response.used) if response else 0} page(s) + {len(buffer)}-turn buffer")
    tl = _tokens_line(tokens_total, tokens_hour)
    if tl:
        lines.append(tl)
    return "\n".join(lines)

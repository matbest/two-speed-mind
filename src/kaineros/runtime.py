"""The fast runtime.

Answers a turn by *reading* the clean layer — retrieve, abstain if weak, phrase, and explain.
The reason is grounded in state; the model only phrases the words.

Implement per docs/tasks.md T5-T6. Tests: tests/test_runtime.py.
"""
from __future__ import annotations

import re
import time

from .interfaces import FastModel
from .schema import Lookup, LookupHit, Page, Response, Turn
from .store import Store


_SUFFIXES = ("ies", "ing", "es", "ed", "s", "y", "ic")


def _variants(token: str) -> set[str]:
    """The token plus every plausible suffix-stripped form.

    Matching on ALL variants (not one committed stem) is what lets 'lives'→{lives,live,liv}
    meet 'live'→{live}, while 'mangoes'→{...,mango} still meets 'mango' — single-stem
    algorithms fail one of those two whichever suffix order they pick.
    """
    out = {token}
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            out.add(token[: -len(suffix)])
    return out


# common function words carry no routing signal — dropping them stops "my"/"is"/"the" from making
# every page match, so routing keys on the CONTENT words (spec §41: help the fast brain choose)
_STOPWORDS = frozenset(
    "i me my mine we our you your he she it its they them their a an the of to in on at for and or "
    "is are am was were be been being do does did have has had this that these those about with "
    "from as so just still got what who where when why how which whose whom no not".split()
)


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in _STOPWORDS:
            continue
        tokens |= _variants(t)
    return tokens

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
HEDGE_PREFIX = "If I remember rightly: "


class Runtime:
    def __init__(
        self,
        store: Store,
        model: FastModel,
        confidence_floor: str = "low",
        stale_after: float = 30 * 24 * 3600.0,
        route_k: int = 3,
    ) -> None:
        self.store = store
        self.model = model
        self.confidence_floor = confidence_floor
        self.stale_after = stale_after
        # two-step retrieval (spec §41, T16): route to the top-K matching pages by the index, then
        # read only those — the cheap fast brain sees a few pages, not every keyword match.
        self.route_k = route_k

    def _hedge_reason(self, page: Page, now: float) -> str | None:
        """Why this page can't be asserted plainly — from provenance, never from prose (spec §6)."""
        prov = page.provenance
        if not prov.stated:
            return "inferred"
        if prov.confidence == "low":
            return "low confidence"
        if now - prov.created_at > self.stale_after:
            return "stale"
        return None

    def respond(self, question: str, buffer: list[Turn] | None = None) -> Response:
        """Answer `question` from the clean layer.

        1. Retrieve pages relevant to `question` (naive keyword overlap on gene/content is fine for
           v1) whose provenance confidence clears ``self.confidence_floor`` (see CONFIDENCE_ORDER).
        2. If none clear the floor, **abstain**: return a Response with ``abstained=True`` and an
           answer that says it does not know — a blank is better than a confident wrong page.
        3. Otherwise ask ``self.model.answer(...)`` to phrase the ``answer`` (the words), and build
           ``why`` yourself from the retrieved pages' genes + provenance — grounded in state, never
           taken from the model's prose. Put the used pages in ``used``.

        See docs/tasks.md T5, T6.
        """
        floor = CONFIDENCE_ORDER[self.confidence_floor]
        terms = _tokens(question)
        trace = Lookup(query_terms=tuple(sorted(terms)), floor=self.confidence_floor)
        now = time.time()

        # step 1 — score every page against the index (gene + content cues); classify each
        matched: list[tuple[Page, int]] = []  # (page, strength) that cleared the floor
        blocked: list[LookupHit] = []
        nomatch: list[LookupHit] = []
        for page in self.store.pages():
            # cues = gene + content + tags: tags are the deep brain's answer to "what words would
            # the QUESTION use?" (spec §45) — they let "what car do I drive?" reach a page that
            # only ever says "Tesla", with no model call
            cues = page.gene + " " + page.content + " " + " ".join(page.tags)
            strength = len(terms & _tokens(cues))
            confidence = page.provenance.confidence
            if strength == 0:
                nomatch.append(LookupHit(page.gene, 0, confidence, "no match"))
            elif CONFIDENCE_ORDER[confidence] < floor:
                blocked.append(LookupHit(page.gene, strength, confidence, "blocked"))
            else:
                matched.append((page, strength))

        # step 2 — ROUTE: keep only the top-K matches (strongest, then most confident, then
        # freshest). The cheap fast brain reads these, not every match — the token win.
        matched.sort(
            key=lambda ps: (ps[1], CONFIDENCE_ORDER[ps[0].provenance.confidence], ps[0].provenance.created_at),
            reverse=True,
        )
        used = [p for p, _ in matched[: self.route_k]]
        routed_genes = {p.gene for p in used}
        for page, strength in matched:
            decision = "routed" if page.gene in routed_genes else "matched"
            trace.hits.append(LookupHit(page.gene, strength, page.provenance.confidence, decision))
        trace.hits.extend(blocked)
        trace.hits.extend(nomatch)

        if not used:
            trace.abstained = True
            return Response(
                answer="I don't know.",
                why=f"abstained: no page matched above the '{self.confidence_floor}' confidence floor",
                used=[],
                abstained=True,
                trace=trace,
            )
        answer = self.model.answer(question, used, buffer or [])
        reasons = {p.gene: self._hedge_reason(p, now) for p in used}
        if any(reasons.values()):
            # hedge decided by state; the model's words are only prefixed, never consulted
            answer = HEDGE_PREFIX + answer
        why = "; ".join(
            f"page '{p.gene}' ({'stated' if p.provenance.stated else 'inferred'}, "
            f"confidence {p.provenance.confidence}, {reasons[p.gene] or 'fresh'})"
            for p in used
        )
        return Response(answer=answer, why=why, used=used, abstained=False, trace=trace)

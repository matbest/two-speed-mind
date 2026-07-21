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


_SUFFIXES = ("ing", "ed", "ies", "es", "s", "y", "ic")


def _stem(token: str) -> str:
    """Crude suffix-stripping so 'loves'/'love', 'called'/'call', 'allergy'/'allergic' meet."""
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())}

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
HEDGE_PREFIX = "If I remember rightly: "


class Runtime:
    def __init__(
        self,
        store: Store,
        model: FastModel,
        confidence_floor: str = "low",
        stale_after: float = 30 * 24 * 3600.0,
    ) -> None:
        self.store = store
        self.model = model
        self.confidence_floor = confidence_floor
        self.stale_after = stale_after

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
        used: list[Page] = []
        for page in self.store.pages():
            strength = len(terms & _tokens(page.gene + " " + page.content))
            confidence = page.provenance.confidence
            if strength == 0:
                decision = "no match"
            elif CONFIDENCE_ORDER[confidence] < floor:
                decision = "blocked"
            else:
                decision = "admitted"
                used.append(page)
            trace.hits.append(
                LookupHit(gene=page.gene, strength=strength, confidence=confidence, decision=decision)
            )
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
        now = time.time()
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

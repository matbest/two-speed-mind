"""The fast runtime.

Answers a turn by *reading* the clean layer — retrieve, abstain if weak, phrase, and explain.
The reason is grounded in state; the model only phrases the words.

Implement per docs/tasks.md T5-T6. Tests: tests/test_runtime.py.
"""
from __future__ import annotations

import re

from .interfaces import FastModel
from .schema import Page, Response, Turn
from .store import Store


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


class Runtime:
    def __init__(self, store: Store, model: FastModel, confidence_floor: str = "low") -> None:
        self.store = store
        self.model = model
        self.confidence_floor = confidence_floor

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
        used: list[Page] = []
        for page in self.store.pages():
            if not terms & _tokens(page.gene + " " + page.content):
                continue
            if CONFIDENCE_ORDER[page.provenance.confidence] < floor:
                continue
            used.append(page)
        if not used:
            return Response(
                answer="I don't know.",
                why=f"abstained: no page matched above the '{self.confidence_floor}' confidence floor",
                used=[],
                abstained=True,
            )
        answer = self.model.answer(question, used, buffer or [])
        why = "; ".join(
            f"page '{p.gene}' ({'stated' if p.provenance.stated else 'inferred'}, "
            f"confidence {p.provenance.confidence})"
            for p in used
        )
        return Response(answer=answer, why=why, used=used, abstained=False)

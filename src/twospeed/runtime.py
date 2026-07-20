"""The fast runtime.

Answers a turn by *reading* the clean layer — retrieve, abstain if weak, phrase, and explain.
The reason is grounded in state; the model only phrases the words.

Implement per docs/tasks.md T5-T6. Tests: tests/test_runtime.py.
"""
from __future__ import annotations

from .interfaces import FastModel
from .schema import Page, Response, Turn
from .store import Store

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
        raise NotImplementedError("T5/T6: implement retrieval, abstention, and the reason/words split")

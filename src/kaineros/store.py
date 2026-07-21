"""The store: the gene pool (competing candidates) and the clean layer (promoted pages).

A dumb data holder — the selection logic lives in `compiler.py`, retrieval in `runtime.py`.
"""
from __future__ import annotations

from .schema import Candidate, Page, Question


class Store:
    def __init__(self) -> None:
        # gene -> candidates, ranked best-first (the pool)
        self.pool: dict[str, list[Candidate]] = {}
        # gene -> promoted page (the clean layer — the kainome)
        self.clean: dict[str, Page] = {}
        # disambiguation questions the deep brain queued (spec §36-39)
        self.questions: list[Question] = []

    def pending_questions(self) -> list[Question]:
        return [q for q in self.questions if q.status == "pending"]

    def has_question_for(self, genes: tuple[str, ...]) -> bool:
        """Is a question (any status but answered) already covering this exact conflict?"""
        key = frozenset(genes)
        return any(
            frozenset(q.genes) == key and q.status != "answered" for q in self.questions
        )

    def genes(self) -> list[str]:
        return list(self.pool.keys())

    def candidates(self, gene: str) -> list[Candidate]:
        return self.pool.get(gene, [])

    def top(self, gene: str) -> Candidate | None:
        cs = self.pool.get(gene)
        return cs[0] if cs else None

    def page(self, gene: str) -> Page | None:
        return self.clean.get(gene)

    def pages(self) -> list[Page]:
        return list(self.clean.values())

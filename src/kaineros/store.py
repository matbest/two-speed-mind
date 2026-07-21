"""The store: the gene pool (competing candidates) and the clean layer (promoted pages).

A dumb data holder — the selection logic lives in `compiler.py`, retrieval in `runtime.py`.
"""
from __future__ import annotations

from .schema import Candidate, Page


class Store:
    def __init__(self) -> None:
        # gene -> candidates, ranked best-first (the pool)
        self.pool: dict[str, list[Candidate]] = {}
        # gene -> promoted page (the clean layer — the kainome)
        self.clean: dict[str, Page] = {}

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

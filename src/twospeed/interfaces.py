"""The model boundary.

The core depends on these Protocols, never on a concrete model. Tests and the CLI run against the
deterministic fakes in `twospeed.fakes`; a real local model (Slice 5) implements the same Protocols.
"""
from __future__ import annotations

from typing import Protocol

from .schema import Candidate, Page, Turn


class Judge(Protocol):
    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        """Pairwise comparison — the one judgement models make reliably.

        Return True if `a` is a better account of `gene` than `b`.
        """
        ...


class SlowModel(Protocol):
    def extract(self, turns: list[Turn]) -> list[Candidate]:
        """Compile raw conversation turns into candidate facts."""
        ...


class FastModel(Protocol):
    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        """Phrase an answer from the retrieved pages (and the recent buffer).

        The model supplies WORDS. It must never be the source of truth for behaviour — the caller
        decides what is retrieved and builds the grounded reason itself.
        """
        ...

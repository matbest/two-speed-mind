"""Deterministic fakes implementing the model Protocols.

The whole architecture is built, tested, and demoed with these — no real model needed. A real local
model (Slice 5) implements the same Protocols and drops in unchanged.
"""
from __future__ import annotations

from typing import Callable

from .schema import Candidate, Page, Provenance, Turn


class FakeJudge:
    """Ranks candidates by a key function. Default: longer content wins. Deterministic."""

    def __init__(self, key: Callable[[Candidate], float] | None = None) -> None:
        self.key = key or (lambda c: float(len(c.content)))

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self.key(a) > self.key(b)


class FakeSlowModel:
    """One candidate per user turn: gene = first word (lowercased), content = the turn text."""

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        out: list[Candidate] = []
        for t in turns:
            if t.speaker != "user":
                continue
            words = t.text.split()
            gene = words[0].lower() if words else "misc"
            out.append(
                Candidate(
                    gene=gene,
                    content=t.text,
                    provenance=Provenance(source_turn_ids=(t.id,), created_at=t.created_at, stated=True),
                )
            )
        return out


class FakeFastModel:
    """Phrases a fixed template from the pages, so tests can prove that `why` is grounded
    independently of the words the model returns."""

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        if not pages:
            return "I don't know."
        return "From what I know: " + " ; ".join(p.content for p in pages)

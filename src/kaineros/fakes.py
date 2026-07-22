"""Deterministic fakes implementing the model Protocols.

The whole architecture is built, tested, and demoed with these — no real model needed. A real local
model (Slice 5) implements the same Protocols and drops in unchanged.
"""
from __future__ import annotations

from typing import Callable

from .schema import Candidate, Page, Provenance, Turn


class FakeJudge:
    """Deterministic judge driven by key functions.

    - `key`: ranking (default: longer content wins).
    - `claim_of`: claim identity for fission/fusion (default: the gene — one gene, one claim, so
      nothing splits or fuses unless a test says otherwise).
    - `account_of`: account identity for dedup (default: the exact content — identical restatements
      merge in cleanup).
    """

    def __init__(
        self,
        key: Callable[[Candidate], float] | None = None,
        claim_of: Callable[[Candidate], object] | None = None,
        account_of: Callable[[Candidate], object] | None = None,
        conflict_pred: Callable[[Candidate, Candidate], bool] | None = None,
    ) -> None:
        self.key = key or (lambda c: float(len(c.content)))
        self.claim_of = claim_of or (lambda c: c.gene)
        self.account_of = account_of or (lambda c: c.content)
        # default: nothing conflicts, so existing tests never queue questions
        self.conflict_pred = conflict_pred or (lambda a, b: False)

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self.key(a) > self.key(b)

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self.claim_of(a) == self.claim_of(b)

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self.account_of(a) == self.account_of(b)

    def conflicts(self, a: Candidate, b: Candidate) -> bool:
        return self.conflict_pred(a, b)


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
                    provenance=Provenance(
                        source_turn_ids=(t.id,), source_texts=(t.text,),
                        created_at=t.created_at, stated=True,
                    ),
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

"""The model boundary.

The core depends on these Protocols, never on a concrete model. Tests and the CLI run against the
deterministic fakes in `kaineros.fakes`; a real local model (Slice 5) implements the same Protocols.
"""
from __future__ import annotations

from typing import Protocol

from .schema import ArcDraft, Candidate, Page, Turn


class Judge(Protocol):
    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        """Pairwise comparison — the one judgement models make reliably.

        Return True if `a` is a better account of `gene` than `b`.
        """
        ...

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        """The fission/fusion primitive (spec §9–10): are `a` and `b` rival accounts of ONE claim?

        "Lives in London" vs "lives in Berlin" → True (rivals). "Likes bananas" vs "allergic to
        bananas" → False (same topic, different claims). Pairwise and boolean, like all judging.
        """
        ...

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        """The dedup primitive (spec §11), one grain finer: do `a` and `b` assert the SAME thing?

        Restatements merge in housekeeping's cleanup rather than competing as rivals.
        """
        ...

    def conflicts(self, a: Candidate, b: Candidate) -> bool:
        """The disambiguation primitive (spec §36): do `a` and `b` CONTRADICT — can they not both
        be true? ("backend engineer" vs "on the platform team, not backend"). Different genes, so
        not fusion's job; the deep brain queues a question rather than pick a winner blindly.
        """
        ...


class SlowModel(Protocol):
    def extract(self, turns: list[Turn]) -> list[Candidate]:
        """Compile raw conversation turns into candidate facts."""
        ...

    def summarise(self, topic: str, facts: list[str]) -> str:
        """Distil several related facts about `topic` into ONE rich, self-contained sentence
        (spec §50). Used by grooming to consolidate a fragmented cluster (many narrow genes about
        one aspect of the user) into a single dense page, so retrieval isn't diluted. Optional:
        the compiler only summarises when a model providing this is wired in.
        """
        ...

    def arc(self, facts: list[str]) -> "ArcDraft":
        """Narrate an evolving thread — `facts` in time order (oldest first) — into an ArcDraft
        (gist + ordered beats), for the narrative questions flat facts can't answer (spec §51).
        Uses ONLY what the facts state; never invents a reason (and the compiler drops any beat it
        can't ground anyway). Optional: the compiler only arcs when a model providing this is wired
        in and `arc_enabled` is set.
        """
        ...


class FastModel(Protocol):
    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        """Phrase an answer from the retrieved pages (and the recent buffer).

        The model supplies WORDS. It must never be the source of truth for behaviour — the caller
        decides what is retrieved and builds the grounded reason itself.
        """
        ...

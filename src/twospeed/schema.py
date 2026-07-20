"""The data model for the two-speed mind.

White Paper 1 calls a concrete page-and-candidate schema "the natural next artifact" — this is it.
Every fact carries provenance (where it came from, when, stated vs inferred, confidence, stakes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count

_ids = count(1)


def _new_id(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


Confidence = str  # "high" | "medium" | "low"
Stakes = str      # "high" | "low"


@dataclass
class Turn:
    """One verbatim line of conversation (a short-term buffer entry)."""
    text: str
    speaker: str = "user"  # "user" | "assistant"
    id: str = field(default_factory=lambda: _new_id("t"))
    created_at: float = 0.0


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from — the basis for hedging, abstention, and audit."""
    source_turn_ids: tuple[str, ...] = ()
    created_at: float = 0.0
    stated: bool = True                 # True: the user said it; False: the system inferred it
    confidence: Confidence = "medium"
    stakes: Stakes = "high"             # "high": facts about the user/world; "low": persona/style


@dataclass
class Candidate:
    """An allele: one competing version of a fact about `gene`, living in the pool."""
    gene: str                           # the concept/topic key this is about
    content: str
    provenance: Provenance = field(default_factory=Provenance)
    id: str = field(default_factory=lambda: _new_id("c"))
    wins: int = 0                       # consecutive housekeeping passes held at #1 (promotion threshold)


@dataclass
class Page:
    """A promoted clean entry — the fittest allele, expressed. The fast model reads these."""
    gene: str
    content: str
    provenance: Provenance
    rank_history: list = field(default_factory=list)  # timestamped rank estimates (auditability)


@dataclass
class Response:
    """A runtime answer, keeping the two things apart on purpose."""
    answer: str                         # the words — phrased by the fast model
    why: str                            # the grounded reason — read from state, NOT from the model
    used: list = field(default_factory=list)  # list[Page] the answer drew on
    abstained: bool = False

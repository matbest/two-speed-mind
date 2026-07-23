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
    source_texts: tuple[str, ...] = ()  # the raw words, verbatim (spec §46) — reference only:
                                        # never enters a prompt; dedup unions like receipts
    created_at: float = 0.0
    stated: bool = True                 # True: the user said it; False: the system inferred it
    confidence: Confidence = "medium"
    stakes: Stakes = "high"             # "high": facts about the user/world; "low": persona/style
    supersedes: bool = False            # a DELIBERATE update ("now", "as of today", "not X") —
                                        # wins fast over an entrenched incumbent (spec §42); a
                                        # stray contradiction (supersedes=False) still has to fight


@dataclass
class Candidate:
    """An allele: one competing version of a fact about `gene`, living in the pool."""
    gene: str                           # the concept/topic key this is about
    content: str
    provenance: Provenance = field(default_factory=Provenance)
    id: str = field(default_factory=lambda: _new_id("c"))
    wins: int = 0                       # consecutive housekeeping passes held at #1 (promotion threshold)
    tags: tuple[str, ...] = ()          # the fact's own vocabulary (spec §44) — the words someone
                                        # would use when ASKING about it; hints, never verdicts
    gist: str = ""                      # the bare ANSWER the gene resolves to (spec §48): gene =
                                        # the question, gist = the value ("japanese") — a terse
                                        # form the fast brain reads without parsing a sentence


@dataclass
class Page:
    """A promoted clean entry — the fittest allele, expressed. The fast model reads these."""
    gene: str
    content: str
    provenance: Provenance
    rank_history: list = field(default_factory=list)  # timestamped rank estimates (auditability)
    tags: tuple[str, ...] = ()          # inherited from the winning allele (spec §44)
    gist: str = ""                      # the bare answer, inherited from the winner (spec §48)


@dataclass
class LookupHit:
    """One page examined during retrieval — real state, recorded as it happened."""
    gene: str
    strength: int                       # matched query terms (0 = no match)
    confidence: Confidence              # the page's provenance confidence
    decision: str                       # "admitted" | "blocked" | "no match"


@dataclass
class Lookup:
    """One retrieval's machine-readable record (spec §14) — the fast brain's receipt.

    The `why` prose and the cockpit's fast-brain panel both render from this; nothing about
    retrieval is reconstructed after the fact or phrased by the model.
    """
    query_terms: tuple[str, ...] = ()
    hits: list = field(default_factory=list)  # list[LookupHit], every page examined
    floor: str = "low"
    abstained: bool = False


@dataclass
class Question:
    """A disambiguation the deep brain queued and the fast brain will ask (spec §36-39).

    Grounded: `text` is built from the conflicting pages' words, `genes` records where it came
    from. Its answer flows back through the normal pipeline as an ordinary turn.
    """
    text: str
    genes: tuple[str, ...] = ()
    status: str = "pending"             # "pending" | "asked" | "answered"
    id: str = field(default_factory=lambda: _new_id("q"))
    created_at: float = 0.0
    asked_at: float = 0.0
    answered_at: float = 0.0


@dataclass
class CompileReport:
    """One housekeeping pass's tally (spec §19) — the deep brain's receipt."""
    inserted: int = 0                   # candidates inserted since the previous pass
    merged: int = 0                     # dedup merges (Slice 4.5)
    split: int = 0                      # fissions (Slice 4.5)
    fused: int = 0                      # fusions (Slice 4.5)
    promoted: int = 0                   # pages promoted this pass
    queued: int = 0                     # disambiguation questions queued this pass (Slice 9)
    backlog: int = 0                    # turns awaiting compilation (set by the session)
    error: str | None = None            # a failed background pass reports here (spec §26)


@dataclass
class Response:
    """A runtime answer, keeping the two things apart on purpose."""
    answer: str                         # the words — phrased by the fast model
    why: str                            # the grounded reason — read from state, NOT from the model
    used: list = field(default_factory=list)  # list[Page] the answer drew on
    abstained: bool = False
    trace: Lookup = field(default_factory=Lookup)  # the retrieval record the why renders from

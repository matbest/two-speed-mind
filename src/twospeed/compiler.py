"""The slow-deep compiler.

Ranks competing candidates by pairwise comparison and promotes settled winners into the clean layer.
Runs off the interactive path (in this prototype, called synchronously after a turn).

Implement per docs/tasks.md T2-T4. Tests: tests/test_store.py.
"""
from __future__ import annotations

from .interfaces import Judge
from .schema import Candidate, Page
from .store import Store


class Compiler:
    def __init__(self, store: Store, judge: Judge, promote_after: int = 3) -> None:
        self.store = store
        self.judge = judge
        self.promote_after = promote_after

    def insert(self, candidate: Candidate) -> None:
        """Place `candidate` into ``store.pool[candidate.gene]``, keeping the list best-first
        according to ``judge.better``.

        Use binary insertion (~log n comparisons). The current top must be *beaten* to be
        displaced — the incumbent defends its position. A candidate that loses is not thrown away;
        it stays in the pool at its ranked place (it can win again later).

        See docs/tasks.md T2, T3.
        """
        raise NotImplementedError("T2/T3: implement ranked insertion (incumbent defends; losers survive)")

    def housekeep(self) -> list[Page]:
        """One maintenance pass. For each gene, the top candidate accrues a 'win'; once it has held
        the top rank for ``promote_after`` passes, promote it into ``store.clean`` as a Page (append
        a timestamped entry to the page's ``rank_history``). Return the pages promoted this pass.

        See docs/tasks.md T4.
        """
        raise NotImplementedError("T4: implement promotion after the stability threshold")

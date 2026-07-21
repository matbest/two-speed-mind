"""The slow-deep compiler.

Ranks competing candidates by pairwise comparison and promotes settled winners into the clean layer.
Runs off the interactive path (in this prototype, called synchronously after a turn).

Implement per docs/tasks.md T2-T4. Tests: tests/test_store.py.
"""
from __future__ import annotations

import time

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
        pool = self.store.pool.setdefault(candidate.gene, [])
        lo, hi = 0, len(pool)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.judge.better(candidate.gene, candidate, pool[mid]):
                hi = mid
            else:
                lo = mid + 1
        pool.insert(lo, candidate)

    def housekeep(self) -> list[Page]:
        """One maintenance pass. For each gene, the top candidate accrues a 'win'; once it has held
        the top rank for ``promote_after`` passes, promote it into ``store.clean`` as a Page (append
        a timestamped entry to the page's ``rank_history``). Return the pages promoted this pass.

        See docs/tasks.md T4.
        """
        promoted: list[Page] = []
        for gene, pool in self.store.pool.items():
            if not pool:
                continue
            top = pool[0]
            top.wins += 1
            for challenger in pool[1:]:
                challenger.wins = 0  # the streak is consecutive passes at #1
            page = self.store.clean.get(gene)
            if top.wins >= self.promote_after and (page is None or page.content != top.content):
                history = page.rank_history if page else []
                new_page = Page(
                    gene=gene,
                    content=top.content,
                    provenance=top.provenance,
                    rank_history=history + [{"at": time.time(), "event": "promoted"}],
                )
                self.store.clean[gene] = new_page
                promoted.append(new_page)
        return promoted

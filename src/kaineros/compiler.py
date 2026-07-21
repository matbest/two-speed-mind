"""The slow-deep compiler.

Ranks competing candidates by pairwise comparison and promotes settled winners into the clean layer.
Runs off the interactive path (in this prototype, called synchronously after a turn).

`housekeep` is the slow brain's maintenance pass, in order — each step keeps the next step's signal
honest (docs/plan.md): dedup (restatements merge, so pool size means distinct accounts) → fission
(crowded mixed pools split, so a gene means one claim) → fusion (genes whose promoted pages state
one claim merge) → promotion (stability earns a page).

Implement per docs/tasks.md T2-T4 and Slice 4.5. Tests: tests/test_store.py, tests/test_identity.py.
"""
from __future__ import annotations

import time
from dataclasses import replace

from .interfaces import Judge
from .schema import Candidate, CompileReport, Page
from .store import Store


class Compiler:
    def __init__(
        self, store: Store, judge: Judge, promote_after: int = 3, split_after: int = 8
    ) -> None:
        self.store = store
        self.judge = judge
        self.promote_after = promote_after
        self.split_after = split_after
        self.last_report = CompileReport()  # the deep brain's receipt (spec §19)
        self._inserted_since_pass = 0

    def insert(self, candidate: Candidate) -> None:
        """Place `candidate` into ``store.pool[candidate.gene]``, keeping the list best-first
        according to ``judge.better``.

        Use binary insertion (~log n comparisons). The current top must be *beaten* to be
        displaced — the incumbent defends its position. A candidate that loses is not thrown away;
        it stays in the pool at its ranked place (it can win again later).

        Deliberately cheap: no identity checks here — repair work belongs to housekeep (spec §11).

        Low-stakes candidates (persona/style — spec §4) skip judging entirely: the newest account
        goes straight to the top, because for style recency *is* the right answer, and trivia is
        not worth judge calls.

        See docs/tasks.md T2, T3, T8.
        """
        pool = self.store.pool.setdefault(candidate.gene, [])
        if candidate.provenance.stakes == "low":
            pool.insert(0, candidate)
        else:
            self._place(candidate.gene, pool, candidate)
        self._inserted_since_pass += 1

    def housekeep(self) -> list[Page]:
        """One maintenance pass: dedup → fission → fusion → promotion. Returns newly promoted
        pages; the full tally lands in ``self.last_report``.

        See docs/tasks.md T4, T8.4-T8.6.
        """
        merged = split = 0
        for gene, pool in self.store.pool.items():
            merged += self._dedup(gene, pool)
        for gene in list(self.store.pool):
            split += self._fission(gene)
        fused = self._fusion()

        promoted: list[Page] = []
        for gene, pool in self.store.pool.items():
            if not pool:
                continue
            top = pool[0]
            top.wins += 1
            for challenger in pool[1:]:
                challenger.wins = 0  # the streak is consecutive passes at #1
            page = self.store.clean.get(gene)
            # low stakes skip competition (spec §4); so does an uncontested claim — stability
            # is meaningless without rivals, and dedup already ran, so a pool of one means
            # "one account, possibly restated", never "a contest in progress"
            uncontested = len(pool) == 1
            threshold = (
                0 if (top.provenance.stakes == "low" or uncontested) else self.promote_after
            )
            if top.wins >= threshold and (page is None or page.content != top.content):
                history = page.rank_history if page else []
                new_page = Page(
                    gene=gene,
                    content=top.content,
                    provenance=top.provenance,
                    rank_history=history + [{"at": time.time(), "event": "promoted"}],
                )
                self.store.clean[gene] = new_page
                promoted.append(new_page)

        self.last_report = CompileReport(
            inserted=self._inserted_since_pass,
            merged=merged,
            split=split,
            fused=fused,
            promoted=len(promoted),
        )
        self._inserted_since_pass = 0
        return promoted

    # -- the maintenance steps ---------------------------------------------------------------

    def _place(self, gene: str, pool: list[Candidate], candidate: Candidate) -> None:
        """Binary-insert by the judge; strict wins only, so the incumbent defends."""
        lo, hi = 0, len(pool)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.judge.better(gene, candidate, pool[mid]):
                hi = mid
            else:
                lo = mid + 1
        pool.insert(lo, candidate)

    def _dedup(self, gene: str, pool: list[Candidate]) -> int:
        """Merge same-account restatements into the best-ranked of them (spec §11).

        Receipts accumulate, recency refreshes, the survivor keeps its rank and wins.
        """
        merged = 0
        kept: list[Candidate] = []
        for cand in pool:
            survivor = next(
                (k for k in kept if self.judge.same_account(gene, k, cand)), None
            )
            if survivor is None:
                kept.append(cand)
                continue
            ids = survivor.provenance.source_turn_ids + tuple(
                i
                for i in cand.provenance.source_turn_ids
                if i not in survivor.provenance.source_turn_ids
            )
            survivor.provenance = replace(
                survivor.provenance,
                source_turn_ids=ids,
                created_at=max(survivor.provenance.created_at, cand.provenance.created_at),
            )
            merged += 1
        pool[:] = kept
        return merged

    def _fission(self, gene: str) -> int:
        """Split a crowded, mixed pool (spec §8-9): the top candidate keeps the gene; candidates
        not sharing its claim are re-keyed to a fresh gene and re-ranked from scratch.

        History does not leak across a split: `wins` reset on both sides and the gene's page is
        retired — each child pool re-earns promotion. No recursion; a still-mixed child splits on
        a later pass.
        """
        pool = self.store.pool.get(gene, [])
        if len(pool) <= self.split_after:
            return 0
        seed = pool[0]
        keep = [c for c in pool if self.judge.same_claim(gene, seed, c)]
        movers = [c for c in pool if c not in keep]
        if not movers:
            return 0  # large but pure — crowded is only a symptom when claims are mixed
        new_gene = self._fresh_gene(gene)
        pool[:] = keep
        for c in keep:
            c.wins = 0
        self.store.clean.pop(gene, None)
        target = self.store.pool.setdefault(new_gene, [])
        for c in movers:
            c.gene = new_gene
            c.wins = 0
            self._place(new_gene, target, c)
        return 1

    def _fusion(self) -> int:
        """Merge genes whose *promoted pages* state one claim (spec §10) — split-brain repair.

        The earlier-promoted gene key survives; both pools re-rank into it (`wins` reset) and the
        merged pool re-earns promotion. Destructive, so cautious: the verdict is asked both ways
        round, and the survivor's page keeps serving through the contest — only the absorbed page
        retires. A false fusion narrows the wiki by one page, never empties it.
        """
        fused = 0
        while True:
            pair = self._find_duplicate_pages()
            if pair is None:
                return fused
            survivor, absorbed = pair
            pool = self.store.pool.setdefault(survivor, [])
            incoming = self.store.pool.pop(absorbed, [])
            self.store.clean.pop(absorbed, None)  # survivor's page keeps serving (§6's rule)
            for c in pool:
                c.wins = 0
            for c in incoming:
                c.gene = survivor
                c.wins = 0
                self._place(survivor, pool, c)
            fused += 1

    def _find_duplicate_pages(self) -> tuple[str, str] | None:
        genes = list(self.store.clean)  # dict order = promotion order; the older key survives
        for i in range(len(genes)):
            for j in range(i + 1, len(genes)):
                p1, p2 = self.store.clean[genes[i]], self.store.clean[genes[j]]
                a = Candidate(gene=p1.gene, content=p1.content, provenance=p1.provenance)
                b = Candidate(gene=p2.gene, content=p2.content, provenance=p2.provenance)
                # asked both ways round: fusion is destructive, one noisy verdict must not fire it
                if self.judge.same_claim(genes[i], a, b) and self.judge.same_claim(
                    genes[j], b, a
                ):
                    return genes[i], genes[j]
        return None

    def _fresh_gene(self, gene: str) -> str:
        n = 2
        while f"{gene}.{n}" in self.store.pool:
            n += 1
        return f"{gene}.{n}"

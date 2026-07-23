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

import random
import re
import time
from dataclasses import replace

from .interfaces import Judge
from .schema import Candidate, CompileReport, Page, Question
from .store import Store


def _norm(text: str) -> str:
    """Normalised form for exact-restatement detection: case, punctuation, spacing removed."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


class Compiler:
    def __init__(
        self, store: Store, judge: Judge, promote_after: int = 3, split_after: int = 8,
        groom_rate: int = 8, seed: int | None = None
    ) -> None:
        self.store = store
        self.judge = judge
        self.promote_after = promote_after
        self.split_after = split_after
        # cross-page grooming (spec §47): instead of an O(pages²) all-pairs sweep every cleanup,
        # examine only a BOUNDED set of pairs — the changed pages against their peers (eager, so a
        # fresh conflict is caught the moment its second page appears), plus `groom_rate` random
        # pairs biased to shared tags (stochastic grooming of the long tail). Constant-ish per
        # pass; the verdict cache makes re-sampled unchanged pairs free, so cost tracks churn, not
        # size. `seed` makes the sampling reproducible for tests.
        self.groom_rate = groom_rate
        self._rng = random.Random(seed)
        self.last_report = CompileReport()  # the deep brain's receipt (spec §19)
        self._inserted_since_pass = 0
        # verdict cache (T18): a verdict about an unchanged pair never expires. Housekeeping
        # re-walks the same pools every pass, so without this the judge is re-asked the same
        # same_claim/conflicts questions endlessly (measured: 24.6 rankings/element on the
        # bench sample; better cost 1). Keyed on content, not identity — dedup merges receipts
        # but never edits content, and fission re-keys the gene so re-asks are correct there.
        # If the judge is swapped mid-run (/model deep), old verdicts persist by design: a
        # verdict is about the FACTS, and re-litigating settled pairs on a model change would
        # reintroduce exactly the churn this exists to kill.
        self._verdicts: dict[tuple, bool] = {}

    def _verdict(self, kind: str, gene: str, a: Candidate, b: Candidate) -> bool:
        key = (kind, gene, a.content, b.content)
        if key not in self._verdicts:
            shortcut = self._shortcut(kind, a, b)
            if shortcut is not None:
                self._verdicts[key] = shortcut
            elif kind == "conflicts":
                self._verdicts[key] = self.judge.conflicts(a, b)
            else:
                self._verdicts[key] = getattr(self.judge, kind)(gene, a, b)
        return self._verdicts[key]

    @staticmethod
    def _shortcut(kind: str, a: Candidate, b: Candidate) -> bool | None:
        """T19, the deterministic pre-comparator: settle a comparison from the candidates' own
        text/metadata when the answer is near-certain; ``None`` sends the pair to the model.

        Every rule is CONSERVATIVE — a wrong deterministic verdict is never re-examined, so a
        shortcut only fires where it can't plausibly be wrong, and each falls on the
        non-destructive side (don't fuse, don't queue a question) when it declines a pair:

        - identical normalised text: the same account, the same claim, no conflict, and never
          strictly "better" than itself.
        - same_claim only: zero content-token overlap (same tokeniser the fast brain routes
          with) → not one claim. A false "not same" here merely skips a fusion — the wiki stays
          one page less tidy; nothing is lost and no answer changes.
        - conflicts gets NO overlap shortcut: facts can collide without sharing a single word
          ("i work as a backend engineer" / "i'm on the platform team now" — the spec test in
          tests/test_modes.py pins exactly this), and the cost of a missed conflict is a missed
          question to the user. Irreducibly semantic → always the model's call.
        - everything else — genuinely semantic questions ("gone off Thai" vs "Thai is my
          favourite") — is exactly what the deep model is FOR.
        """
        same_text = _norm(a.content) == _norm(b.content)
        if kind == "same_account":
            return True if same_text else None
        if kind == "better":
            return False if same_text else None  # strict wins only; otherwise the judge ranks
        if kind == "conflicts":
            return False if same_text else None  # identical text can't disagree with itself
        if kind == "same_claim":
            if same_text:
                return True
            from .runtime import _tokens  # one definition of "content words" for both brains

            if not (_tokens(a.content) & _tokens(b.content)):
                return False
            return None
        return None

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
        prov = candidate.provenance
        if prov.stakes == "low" or prov.supersedes:
            # low-stakes style, or a DELIBERATE update ("as of today… not X") — believe the newest
            # immediately, straight to the top, past the incumbent's defence (spec §42)
            pool.insert(0, candidate)
        else:
            self._place(candidate.gene, pool, candidate)
        self._inserted_since_pass += 1

    def housekeep(self, cleanup: bool = True) -> list[Page]:
        """One maintenance pass, in two modes (spec §41):

        - **rank** (always, cheap, per-pool): dedup restatements, split mixed pools, promote
          settled winners. No cross-page comparison.
        - **cleanup** (``cleanup=True``, cross-page): fusion + conflict detection over a BOUNDED
          set of page pairs (spec §47) — the pass's changed pages against their peers, plus a
          few random pairs. Constant-ish, not O(pages²). Gated to a cadence by the caller.

        Returns newly promoted pages; the full tally lands in ``self.last_report``.
        """
        merged = split = fused = queued = 0
        for gene, pool in self.store.pool.items():
            merged += self._dedup(gene, pool)  # rank: per-pool, cheap
        for gene in list(self.store.pool):
            split += self._fission(gene)        # rank: per-pool

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
            instant = top.provenance.stakes == "low" or top.provenance.supersedes or uncontested
            threshold = 0 if instant else self.promote_after
            if top.wins >= threshold and (page is None or page.content != top.content):
                history = page.rank_history if page else []
                new_page = Page(
                    gene=gene,
                    content=top.content,
                    provenance=top.provenance,
                    rank_history=history + [{"at": time.time(), "event": "promoted"}],
                    tags=top.tags,  # the winning allele's vocabulary serves the page (spec §44)
                )
                self.store.clean[gene] = new_page
                promoted.append(new_page)

        if cleanup:
            # cross-page work over a BOUNDED pair set (spec §47): the pages that CHANGED this pass
            # (eager — catches a fresh conflict the moment its second page lands) + random pairs
            # (grooms the long tail). Fusion first (it may retire a page), then conflict curation.
            pairs = self._pairs_to_check([p.gene for p in promoted])
            fused = self._fusion(pairs)
            queued = self._curate(pairs)
        self.last_report = CompileReport(
            inserted=self._inserted_since_pass,
            merged=merged,
            split=split,
            fused=fused,
            promoted=len(promoted),
            queued=queued,
        )
        self._inserted_since_pass = 0
        return promoted

    def _pairs_to_check(self, changed_genes: list[str]) -> list[tuple[str, str]]:
        """The bounded set of promoted-page pairs this cleanup examines (spec §47).

        Two sources, deduped, each pair ordered by promotion order (older gene first, so fusion
        keeps the older key):

        - **eager:** every CHANGED page (newly promoted or re-promoted this pass) against each
          peer it could plausibly clash with — same-tag peers, or either side untagged
          (conservative, §45). This is why a fresh conflict is caught at once: the *second* page
          of any conflicting pair changes, and gets paired with the first here. O(changed·peers),
          not O(pages²).
        - **stochastic:** `groom_rate` random pairs, biased toward shared tags — grooms the long
          tail (loaded minds, pairs that became comparable without a promotion event). Constant
          per pass; the verdict cache makes unchanged re-samples free.
        """
        genes = list(self.store.clean)  # dict order = promotion order
        if len(genes) < 2:
            return []
        idx = {g: i for i, g in enumerate(genes)}

        def _ordered(x: str, y: str) -> tuple[str, str]:
            return (x, y) if idx[x] <= idx[y] else (y, x)

        def _could_clash(ta: tuple, tb: tuple) -> bool:
            return not ta or not tb or bool(set(ta) & set(tb))  # untagged either side → maybe

        pairs: set[tuple[str, str]] = set()
        for g in changed_genes:
            if g not in self.store.clean:
                continue
            tg = self.store.clean[g].tags
            for other in genes:
                if other != g and _could_clash(tg, self.store.clean[other].tags):
                    pairs.add(_ordered(g, other))

        for _ in range(self.groom_rate):
            g = self._rng.choice(genes)
            peers = [o for o in genes if o != g]
            tg = set(self.store.clean[g].tags)
            same = [o for o in peers if tg & set(self.store.clean[o].tags)] if tg else []
            pool = same if (same and self._rng.random() < 0.8) else peers  # bias to shared tags
            pairs.add(_ordered(g, self._rng.choice(pool)))
        return list(pairs)

    def _curate(self, pairs: list[tuple[str, str]]) -> int:
        """Queue a disambiguation question for each conflicting pair (spec §36), over the bounded
        pair set. Grounded and non-destructive: the question is built from the pages' own words,
        both pages keep serving, and the user's answer resolves it through competition. Deduped.
        """
        queued = 0
        for ga, gb in pairs:
            if ga not in self.store.clean or gb not in self.store.clean:
                continue  # a fusion earlier this pass retired one of them
            pa, pb = self.store.clean[ga], self.store.clean[gb]
            if pa.tags and pb.tags and not set(pa.tags) & set(pb.tags):
                continue  # tag scoping (§45): declared, disjoint topics can't collide
            a = Candidate(gene=ga, content=pa.content, provenance=pa.provenance)
            b = Candidate(gene=gb, content=pb.content, provenance=pb.provenance)
            if self._verdict("same_claim", ga, a, b):
                continue  # fusion's job, not a question
            if not self._verdict("conflicts", "", a, b):
                continue
            if self.store.has_question_for((ga, gb)):
                continue  # already pending — don't nag twice
            self.store.questions.append(
                Question(
                    text=(
                        f"You've told me both: \"{pa.content}\" and \"{pb.content}\". "
                        "Which is right — or are both true?"
                    ),
                    genes=(ga, gb),
                    created_at=time.time(),
                )
            )
            queued += 1
        return queued

    # -- the maintenance steps ---------------------------------------------------------------

    def _place(self, gene: str, pool: list[Candidate], candidate: Candidate) -> None:
        """Binary-insert by the judge; strict wins only, so the incumbent defends."""
        lo, hi = 0, len(pool)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._verdict("better", gene, candidate, pool[mid]):
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
                (k for k in kept if self._verdict("same_account", gene, k, cand)), None
            )
            if survivor is None:
                kept.append(cand)
                continue
            ids = survivor.provenance.source_turn_ids + tuple(
                i
                for i in cand.provenance.source_turn_ids
                if i not in survivor.provenance.source_turn_ids
            )
            texts = survivor.provenance.source_texts + tuple(
                t for t in cand.provenance.source_texts
                if t not in survivor.provenance.source_texts
            )
            survivor.provenance = replace(
                survivor.provenance,
                source_turn_ids=ids,
                source_texts=texts,
                created_at=max(survivor.provenance.created_at, cand.provenance.created_at),
            )
            # tags union like receipts do (spec §44): every restatement's vocabulary is kept
            survivor.tags = survivor.tags + tuple(t for t in cand.tags if t not in survivor.tags)
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
        keep = [c for c in pool if self._verdict("same_claim", gene, seed, c)]
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

    def _fusion(self, pairs: list[tuple[str, str]]) -> int:
        """Merge genes whose *promoted pages* state one claim (spec §10) — split-brain repair,
        over the bounded pair set. The earlier-promoted gene key survives; both pools re-rank into
        it (`wins` reset) and the merged pool re-earns promotion. Destructive, so cautious: the
        verdict is asked both ways round, and the survivor's page keeps serving through the
        contest — only the absorbed page retires. A false fusion narrows the wiki by one page,
        never empties it. (A merge that opens up a fresh duplicate is caught a later pass.)
        """
        fused = 0
        for survivor, absorbed in pairs:  # ordered older-first: the older key survives
            if survivor not in self.store.clean or absorbed not in self.store.clean:
                continue  # a prior merge this pass already consumed one of them
            if not self._is_duplicate(survivor, absorbed):
                continue
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
        return fused

    def _is_duplicate(self, ga: str, gb: str) -> bool:
        p1, p2 = self.store.clean[ga], self.store.clean[gb]
        # tag scoping (spec §45): two pages stating ONE claim must share a topic — both tagged +
        # disjoint topics → not a duplicate, no model call. Either side untagged falls through.
        if p1.tags and p2.tags and not set(p1.tags) & set(p2.tags):
            return False
        a = Candidate(gene=p1.gene, content=p1.content, provenance=p1.provenance)
        b = Candidate(gene=p2.gene, content=p2.content, provenance=p2.provenance)
        # asked both ways round: fusion is destructive, one noisy verdict must not fire it
        return self._verdict("same_claim", ga, a, b) and self._verdict("same_claim", gb, b, a)

    def _fresh_gene(self, gene: str) -> str:
        n = 2
        while f"{gene}.{n}" in self.store.pool:
            n += 1
        return f"{gene}.{n}"

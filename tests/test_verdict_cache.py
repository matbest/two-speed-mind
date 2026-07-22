"""T18: a verdict about an unchanged pair never expires.

Housekeeping re-walks the same pools every pass; the compiler must answer repeat comparisons
from its verdict cache and only consult the judge about pairs it has never seen. Counted with
a tallying judge — the same instrument the bench uses.
"""
from __future__ import annotations

from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


class TallyJudge(FakeJudge):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.calls = 0
        self.by_kind: dict[str, int] = {}

    def _count(self, kind: str) -> None:
        self.calls += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1

    def better(self, gene, a, b):
        self._count("better")
        return super().better(gene, a, b)

    def same_claim(self, gene, a, b):
        self._count("same_claim")
        return super().same_claim(gene, a, b)

    def same_account(self, gene, a, b):
        self._count("same_account")
        return super().same_account(gene, a, b)

    def conflicts(self, a, b):
        self._count("conflicts")
        return super().conflicts(a, b)


def _cand(gene: str, text: str) -> Candidate:
    return Candidate(gene=gene, content=text, provenance=Provenance(stated=True))


def _build() -> tuple[Compiler, TallyJudge]:
    judge = TallyJudge()
    return Compiler(Store(), judge), judge


def test_repeat_passes_cost_zero_new_calls():
    comp, judge = _build()
    for text in ("alpha fact one", "beta fact two", "gamma fact three"):
        comp.insert(_cand(text.split()[0], text))
    comp.housekeep()
    first_pass = judge.calls
    assert first_pass > 0  # the first pass genuinely consulted the judge
    for _ in range(5):
        comp.housekeep()
    assert judge.calls == first_pass  # nothing changed -> nothing re-asked


def test_new_candidate_pays_only_its_own_comparisons():
    comp, judge = _build()
    comp.insert(_cand("drink", "green tea every morning"))
    comp.insert(_cand("hobby", "chess at the weekend"))
    comp.housekeep()
    settled = judge.calls
    comp.insert(_cand("drink", "matcha lattes lately, quite long account"))
    comp.housekeep()
    added = judge.calls - settled
    assert 0 < added <= 6  # the newcomer's own placement + pool checks, not a full re-litigation
    before = judge.calls
    comp.housekeep()
    assert judge.calls == before  # and the enlarged-but-unchanged world settles again


def test_cached_verdicts_still_rank_correctly():
    comp, judge = _build()  # FakeJudge default: longer content wins
    comp.insert(_cand("g", "short"))
    comp.insert(_cand("g", "a much longer and therefore better account"))
    comp.insert(_cand("g", "short"))  # identical content: verdicts come from the cache
    pool = comp.store.pool["g"]
    assert pool[0].content.startswith("a much longer")
    comp.housekeep()
    assert comp.store.pool["g"][0].content.startswith("a much longer")

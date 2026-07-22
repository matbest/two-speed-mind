"""T19: the deterministic pre-comparator — settle near-certain comparisons in code.

The contract: exact restatements and zero-overlap pairs never reach the judge; anything
genuinely semantic (any shared content word) still does. Counted with the same tallying judge
as the cache tests — and behaviour must be identical either way.
"""
from __future__ import annotations

from kaineros.compiler import Compiler
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store
from tests.test_verdict_cache import TallyJudge, _cand


def _build() -> tuple[Compiler, TallyJudge]:
    judge = TallyJudge()
    return Compiler(Store(), judge), judge


def test_exact_restatement_dedups_without_the_judge():
    comp, judge = _build()
    comp.insert(_cand("diet", "I am a strict vegetarian."))
    comp.insert(_cand("diet", "i am a strict VEGETARIAN"))  # same words, different dressing
    comp.housekeep()
    assert judge.calls == 0  # normalised equality settled account, claim, rank — all of it
    assert len(comp.store.pool["diet"]) == 1  # and the restatements really did merge


def test_disjoint_pages_fusion_sweep_is_free_conflicts_still_asked():
    comp, judge = _build()
    comp.insert(_cand("drink", "Green tea is my favourite drink."))
    comp.insert(_cand("hobby", "Chess tournaments fill the weekends."))
    comp.insert(_cand("pet", "A rescue greyhound called Pixel."))
    comp.housekeep()  # cleanup sweeps every page pair for fusion + conflicts
    # no shared content words -> same_claim settled in code (fusion sweep costs nothing)...
    assert judge.by_kind.get("same_claim", 0) == 0
    assert judge.by_kind.get("same_account", 0) == 0
    # ...but conflict detection is irreducibly semantic (facts can collide without sharing a
    # word - see tests/test_modes.py) so those checks still reach the model
    assert judge.by_kind.get("conflicts", 0) == 3  # one per page pair


def test_overlapping_pair_still_reaches_the_judge():
    comp, judge = _build()
    comp.insert(_cand("sister", "My sister Anna lives in Portland, Oregon."))
    comp.insert(_cand("brother", "My brother Ben lives in Portland, Maine."))
    comp.housekeep()
    assert judge.calls > 0  # shared content ('Portland', 'lives') -> genuinely semantic -> model


def test_identical_content_is_never_better():
    comp, judge = _build()
    comp.insert(_cand("g", "the same account"))
    comp.insert(_cand("g", "the same account"))  # placed without asking: can't beat itself
    assert judge.calls == 0
    assert [c.content for c in comp.store.pool["g"]] == ["the same account"] * 2


def test_behaviour_unchanged_where_the_judge_does_decide():
    comp, judge = _build()  # FakeJudge: longer content wins
    comp.insert(_cand("g", "short claim words"))
    comp.insert(_cand("g", "short claim words plus rather more detail"))
    assert comp.store.pool["g"][0].content.endswith("more detail")  # judge ranked, as before
    assert judge.calls > 0

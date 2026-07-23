"""Slice 1 — the selective store (docs/tasks.md T2-T4).

RED until Compiler.insert / Compiler.housekeep are implemented.
"""
from kaineros.compiler import Compiler
from kaineros.fakes import FakeJudge
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store


def cand(gene: str, content: str, score: float = 0.0) -> Candidate:
    # `score` rides on provenance.created_at so the judge below is fully deterministic.
    return Candidate(gene=gene, content=content, provenance=Provenance(created_at=score))


def make_compiler(promote_after: int = 3):
    store = Store()
    judge = FakeJudge(key=lambda c: c.provenance.created_at)  # higher score = better
    return store, Compiler(store, judge, promote_after=promote_after)


def test_grooming_orders_by_judge():
    # ingest is append-only (spec §49): the judge never runs at insert; ranking emerges over
    # groom passes. The pool is newest-first until then.
    store, comp = make_compiler()
    comp.insert(cand("food", "b", score=2))
    comp.insert(cand("food", "a", score=5))
    comp.insert(cand("food", "c", score=1))
    assert store.candidates("food")[0].content == "c"  # newest, provisional
    for _ in range(3):
        comp.housekeep()  # bubble passes sort it
    assert [c.content for c in store.candidates("food")] == ["a", "b", "c"]  # best first, groomed


def test_incumbent_defends():
    store, comp = make_compiler()
    comp.insert(cand("food", "top", score=5))
    comp.insert(cand("food", "weak", score=1))  # newer but weaker
    comp.housekeep()  # a groom pass re-ranks: the stronger fact wins, recency doesn't override
    assert store.top("food").content == "top"


def test_loser_not_destroyed():
    store, comp = make_compiler()
    comp.insert(cand("food", "top", score=5))
    comp.insert(cand("food", "weak", score=1))
    assert {c.content for c in store.candidates("food")} == {"top", "weak"}


def test_contested_claim_not_promoted_before_threshold():
    store, comp = make_compiler(promote_after=3)
    comp.insert(cand("food", "apple", score=5))
    comp.insert(cand("food", "apricot", score=1))  # a rival: the claim is contested
    comp.housekeep()  # only 1 pass at #1
    assert store.page("food") is None  # stability must be earned once there is competition


def test_uncontested_claim_promotes_immediately():
    # spec §4: competition needs competitors — a pool of one has already won
    store, comp = make_compiler(promote_after=3)
    comp.insert(cand("food", "apple", score=5))
    promoted = comp.housekeep()
    assert store.page("food") is not None
    assert any(p.gene == "food" for p in promoted)


def test_promote_after_threshold():
    store, comp = make_compiler(promote_after=3)
    comp.insert(cand("food", "apple", score=5))
    promoted = []
    for _ in range(3):
        promoted += comp.housekeep()
    page = store.page("food")
    assert page is not None and page.content == "apple"
    assert any(p.gene == "food" for p in promoted)


def test_compile_report_matches_the_pass():
    store, comp = make_compiler(promote_after=1)
    comp.insert(cand("food", "apple", score=5))
    comp.insert(cand("moon", "orbits", score=3))
    comp.housekeep()
    # the deep brain's receipt (spec §19): numbers match what the pass actually did
    assert comp.last_report.inserted == 2
    assert comp.last_report.promoted == 2   # promote_after=1 → both promoted this pass
    comp.housekeep()
    assert comp.last_report.inserted == 0   # nothing new since the previous pass
    assert comp.last_report.promoted == 0   # already promoted; content unchanged

"""Slice 4 — provenance & honesty (docs/tasks.md T8).

Stakes gate the competition (low-stakes skip it), hedging is decided by provenance (never by the
model), and time is evidence: a newer stated account displaces an older incumbent through the
normal win path. Spec §4, §6.
"""
import time

from twospeed.compiler import Compiler
from twospeed.fakes import FakeFastModel, FakeJudge
from twospeed.runtime import HEDGE_PREFIX, Runtime
from twospeed.schema import Candidate, Page, Provenance
from twospeed.store import Store


class CountingJudge(FakeJudge):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.better_calls = 0

    def better(self, gene, a, b):
        self.better_calls += 1
        return super().better(gene, a, b)


def cand(gene: str, content: str, stakes: str = "high", score: float = 0.0) -> Candidate:
    return Candidate(
        gene=gene, content=content, provenance=Provenance(created_at=score, stakes=stakes)
    )


def put_page(store, gene, content, confidence="high", stated=True, created_at=None):
    store.clean[gene] = Page(
        gene=gene,
        content=content,
        provenance=Provenance(
            confidence=confidence,
            stated=stated,
            created_at=time.time() if created_at is None else created_at,
        ),
    )


# -- stakes: only high-stakes facts get the full competition (spec §4) --------------------------


def test_low_stakes_skip_judging_and_promote_immediately():
    store = Store()
    judge = CountingJudge()
    comp = Compiler(store, judge, promote_after=3)
    comp.insert(cand("persona", "call me Mat", stakes="low"))
    comp.insert(cand("persona", "call me Matty", stakes="low"))
    assert judge.better_calls == 0            # style is not worth judge calls
    comp.housekeep()
    page = store.page("persona")
    assert page is not None                   # promoted on the first pass, no streak needed
    assert page.content == "call me Matty"    # newest wins — for style, recency IS the answer


def test_high_stakes_still_earn_promotion():
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=3)
    comp.insert(cand("home", "i live in berlin"))
    comp.housekeep()
    assert store.page("home") is None         # one pass at #1 is not stability


# -- hedging: decided by provenance, never by the model (spec §6) -------------------------------


def test_fresh_stated_fact_is_asserted_plainly():
    store = Store()
    put_page(store, "food", "you like apples")
    resp = Runtime(store, FakeFastModel()).respond("what food do I like?")
    assert not resp.answer.startswith(HEDGE_PREFIX)
    assert "fresh" in resp.why


def test_inferred_memory_is_hedged():
    store = Store()
    put_page(store, "food", "you like apples", stated=False)
    resp = Runtime(store, FakeFastModel()).respond("what food do I like?")
    assert resp.answer.startswith(HEDGE_PREFIX)
    assert "inferred" in resp.why             # the why says which page forced the hedge, and why


def test_stale_memory_is_hedged():
    store = Store()
    put_page(store, "food", "you like apples", created_at=0.0)  # long, long ago
    resp = Runtime(store, FakeFastModel()).respond("what food do I like?")
    assert resp.answer.startswith(HEDGE_PREFIX)
    assert "stale" in resp.why


def test_low_confidence_memory_is_hedged():
    store = Store()
    put_page(store, "food", "you maybe like apples", confidence="low")
    resp = Runtime(store, FakeFastModel()).respond("what food do I like?")
    assert resp.answer.startswith(HEDGE_PREFIX)
    assert "low confidence" in resp.why


# -- time is evidence: recency displaces through the normal win path (spec §6) ------------------


def test_newer_stated_account_displaces_older_incumbent():
    store = Store()
    judge = FakeJudge(key=lambda c: c.provenance.created_at)  # recency-aware judge
    comp = Compiler(store, judge, promote_after=2)
    comp.insert(cand("home", "i live in london", score=1))
    comp.housekeep()
    comp.housekeep()
    assert store.page("home").content == "i live in london"

    comp.insert(cand("home", "i live in berlin", score=5))  # newer stated account beats incumbent
    comp.housekeep()
    # a vote, not a veto: the old page is still served while the newcomer re-earns stability
    assert store.page("home").content == "i live in london"

    comp.housekeep()
    assert store.page("home").content == "i live in berlin"  # the pool converged on the newer fact
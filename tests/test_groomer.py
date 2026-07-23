"""T21 / spec §47: bounded stochastic grooming replaces the O(pages²) cleanup sweep.

The contract: cross-page work per cleanup pass is BOUNDED (constant-ish) regardless of how many
pages the mind holds — yet a fresh conflict is still caught at once (eager frontier), and an old
conflict in a quiet mind is caught eventually (random grooming).
"""
from __future__ import annotations

from kaineros.compiler import Compiler
from kaineros.schema import Candidate, Provenance
from kaineros.store import Store
from tests.test_verdict_cache import TallyJudge


def _cand(gene: str, text: str, tags: tuple[str, ...] = ()) -> Candidate:
    return Candidate(gene=gene, content=text, provenance=Provenance(stated=True), tags=tags)


def _distinct_mind(judge, n: int) -> Compiler:
    """n promoted pages, all different topics, no conflicts — a big quiet wiki."""
    comp = Compiler(Store(), judge, promote_after=1, groom_rate=8, seed=1)
    for i in range(n):
        comp.insert(_cand(f"g{i}", f"fact number {i} about topic {i}", (f"topic{i}",)))
        comp.housekeep()
    return comp


def test_cross_page_cost_is_bounded_as_the_mind_grows():
    # a settled mind (nothing changes this pass) must not re-sweep all pairs — only ~groom_rate
    small = _distinct_mind(TallyJudge(), 10)
    small.judge.calls = 0
    small.housekeep()  # no new pages -> eager frontier empty -> only stochastic sampling
    small_calls = small.judge.calls

    big = _distinct_mind(TallyJudge(), 60)
    big.judge.calls = 0
    big.housekeep()
    big_calls = big.judge.calls

    # O(pages²) would be ~45 vs ~1770; grooming keeps both near groom_rate, flat with size
    assert big_calls <= 4 * small.groom_rate
    assert big_calls <= small_calls + small.groom_rate  # not growing with the page count


def test_fresh_conflict_is_caught_eagerly():
    # the SECOND page of a conflicting pair, promoted into a big mind, is checked at once
    judge = TallyJudge(conflict_pred=lambda a, b: "berlin" in a.content and "munich" in b.content
                       or "munich" in a.content and "berlin" in b.content)
    comp = _distinct_mind(judge, 40)
    comp.insert(_cand("home.a", "the user lives in berlin", ("city",)))
    comp.housekeep()  # promotes home.a
    comp.insert(_cand("home.b", "the user lives in munich", ("city",)))
    comp.housekeep()  # promotes home.b -> eager frontier pairs it with home.a -> conflict queued
    assert any(set(q.genes) == {"home.a", "home.b"} for q in comp.store.questions)


def test_old_conflict_in_a_quiet_mind_is_caught_eventually():
    # two conflicting pages already promoted; nothing changes. Random grooming must find them
    # within a bounded number of passes (not never, not only via an all-pairs sweep).
    judge = TallyJudge(conflict_pred=lambda a, b: {"x", "y"} == {a.content[-1], b.content[-1]})
    comp = Compiler(Store(), judge, promote_after=1, groom_rate=8, seed=7)
    for i in range(12):
        comp.insert(_cand(f"g{i}", f"a distinct fact {i}", (f"t{i}",)))
        comp.housekeep()
    # two pages that conflict, sharing a tag so grooming can pair them, promoted quietly
    comp.insert(_cand("p", "shared topic reading x", ("shared",)))
    comp.housekeep()
    comp.insert(_cand("q", "shared topic reading y", ("shared",)))
    comp.housekeep()  # p and q both promoted; eager may catch immediately, but if not...
    caught = any(set(qq.genes) == {"p", "q"} for qq in comp.store.questions)
    for _ in range(50):  # ...random grooming must get there within a bounded number of passes
        if caught:
            break
        comp.housekeep()
        caught = any(set(qq.genes) == {"p", "q"} for qq in comp.store.questions)
    assert caught


def test_change_off_the_cleanup_cadence_is_still_checked():
    """The bug the ramen/green-curry miss exposed: a page promoted on a rank-only pass must still
    get its eager cross-check at the next cleanup — not slip through the cadence."""
    # same claim, two gene names (fragmentation); they must be recognised as one claim and fused
    judge = TallyJudge(claim_of=lambda c: "usual_order" in c.gene and "order" or c.gene)
    comp = Compiler(Store(), judge, promote_after=1, groom_rate=0, seed=1)  # no random grooming:
    #                                                    only the eager frontier can catch this
    comp.insert(_cand("food.usual_order", "usual order is green curry", ("food", "order")))
    comp.housekeep(cleanup=True)          # green curry promoted + cleaned (alone, no pair yet)
    comp.insert(_cand("food.dish.usual_order", "usual order these days is ramen", ("food", "order")))
    comp.housekeep(cleanup=False)         # ramen promoted on a RANK-ONLY pass — no cleanup here
    assert {"food.usual_order", "food.dish.usual_order"} <= set(comp.store.clean)  # both exist
    comp.housekeep(cleanup=True)          # next cleanup: the dirty ramen page gets its eager check
    assert comp.last_report.fused == 1    # ...and fuses with green curry (one claim, two genes)


def test_disjoint_topics_are_never_sampled_into_a_call():
    # grooming is tag-biased, but even a random cross-topic pair is skipped by tag scoping
    judge = TallyJudge()
    comp = Compiler(Store(), judge, promote_after=1, groom_rate=8, seed=3)
    for i in range(20):
        comp.insert(_cand(f"g{i}", f"fact {i}", (f"topic{i}",)))  # every page a unique topic
        comp.housekeep()
    judge.calls = 0
    for _ in range(10):
        comp.housekeep()
    # all topics disjoint -> tag scoping rejects every sampled pair before the model
    assert judge.calls == 0

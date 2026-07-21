"""Slice 3 — cockpit renderers (docs/tasks.md T7.5). Pure functions, no terminal needed."""
from twospeed.compiler import Compiler
from twospeed.fakes import FakeFastModel, FakeJudge
from twospeed.runtime import Runtime
from twospeed.schema import Candidate, Page, Provenance
from twospeed.store import Store
from twospeed.view import deep_panel, fast_panel


def cand(gene: str, content: str, score: float = 0.0) -> Candidate:
    return Candidate(gene=gene, content=content, provenance=Provenance(created_at=score))


def test_deep_panel_shows_population_and_wins_progress():
    store = Store()
    comp = Compiler(store, FakeJudge(key=lambda c: c.provenance.created_at), promote_after=3)
    comp.insert(cand("food", "apple", score=5))
    comp.insert(cand("food", "kiwi", score=1))
    comp.housekeep()
    comp.housekeep()
    text = deep_panel(comp.last_report, store, promote_after=comp.promote_after)
    assert "1 genes, 2 candidates" in text
    assert "food" in text
    assert "2/3" in text  # the top's wins toward promote_after
    assert "top: apple" in text  # ranked: the winner is shown as top


def test_deep_panel_shows_tallies_and_marks_pages():
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=1)
    comp.insert(cand("food", "apple"))
    comp.housekeep()
    text = deep_panel(comp.last_report, store, promote_after=1)
    assert "1 inserted" in text
    assert "1 promoted" in text
    assert "page" in text  # promoted gene marked as settled


def test_fast_panel_marks_blocked_with_the_tier_that_failed():
    store = Store()
    store.clean["food"] = Page(
        gene="food", content="you maybe like apples", provenance=Provenance(confidence="low")
    )
    rt = Runtime(store, FakeFastModel(), confidence_floor="high")
    resp = rt.respond("what food do I like?")
    text = fast_panel(resp.trace, resp, [])
    assert "food" in text
    assert "blocked (low < high)" in text
    assert "abstained" in text


def test_fast_panel_shows_probe_admission_and_handoff():
    store = Store()
    store.clean["food"] = Page(
        gene="food", content="you like apples", provenance=Provenance(confidence="high")
    )
    rt = Runtime(store, FakeFastModel())
    resp = rt.respond("what food do I like?")
    text = fast_panel(resp.trace, resp, [])
    assert "probed:" in text and "food" in text
    assert "admitted (high >= low)" in text
    assert "1 page(s)" in text


def test_fast_panel_before_first_lookup():
    assert "no lookup yet" in fast_panel(None, None, [])

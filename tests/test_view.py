"""Slice 3 — cockpit renderers (docs/tasks.md T7.5). Pure functions, no terminal needed."""
from kaineros.compiler import Compiler
from kaineros.fakes import FakeFastModel, FakeJudge
from kaineros.runtime import Runtime
from kaineros.schema import Candidate, Page, Provenance
from kaineros.store import Store
from kaineros.view import deep_panel, fast_panel


def cand(gene: str, content: str, score: float = 0.0) -> Candidate:
    return Candidate(gene=gene, content=content, provenance=Provenance(created_at=score))


def test_deep_panel_shows_counts_not_listings():
    store = Store()
    comp = Compiler(store, FakeJudge(key=lambda c: c.provenance.created_at), promote_after=3)
    comp.insert(cand("food", "apple", score=5))
    comp.insert(cand("food", "kiwi", score=1))
    comp.housekeep()
    text = deep_panel(comp.last_report, store)
    assert "2 candidates in 1 genes" in text
    assert "0 pages" in text          # contested pool, nothing promoted yet
    assert "top:" not in text         # counts only — the wiki itself lives on disk


def test_deep_panel_shows_tallies_and_page_count():
    store = Store()
    comp = Compiler(store, FakeJudge(), promote_after=1)
    comp.insert(cand("food", "apple"))
    comp.housekeep()
    text = deep_panel(comp.last_report, store)
    assert "1 inserted" in text
    assert "1 promoted" in text
    assert "wiki: 1 pages" in text


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

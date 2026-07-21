"""Slice 2 — the fast runtime (docs/tasks.md T5-T6).

RED until Runtime.respond is implemented.
"""
from twospeed.fakes import FakeFastModel
from twospeed.runtime import Runtime
from twospeed.schema import Page, Provenance
from twospeed.store import Store


def make_runtime(floor: str = "low"):
    store = Store()
    return store, Runtime(store, FakeFastModel(), confidence_floor=floor)


def put_page(store: Store, gene: str, content: str, confidence: str = "high") -> None:
    store.clean[gene] = Page(
        gene=gene,
        content=content,
        provenance=Provenance(confidence=confidence, stated=True),
    )


def test_abstains_when_empty():
    store, rt = make_runtime()
    resp = rt.respond("what food do I like?")
    assert resp.abstained is True
    assert not resp.used


def test_answers_from_pages():
    store, rt = make_runtime()
    put_page(store, "food", "you like apples", confidence="high")
    resp = rt.respond("what food do I like?")
    assert resp.abstained is False
    assert "apples" in resp.answer                       # words came via the (fake) model + page
    assert any(p.gene == "food" for p in resp.used)


def test_reason_is_grounded():
    store, rt = make_runtime()
    put_page(store, "food", "you like apples", confidence="high")
    resp = rt.respond("what food do I like?")
    # `why` is built from state (the retrieved page's gene), not the model's prose:
    assert "food" in resp.why


def test_confidence_floor_hides_weak_pages():
    store, rt = make_runtime(floor="high")
    put_page(store, "food", "you maybe like apples", confidence="low")
    resp = rt.respond("what food do I like?")
    assert resp.abstained is True                        # low page is below the 'high' floor


def test_trace_records_blocked_page_and_why():
    store, rt = make_runtime(floor="high")
    put_page(store, "food", "you maybe like apples", confidence="low")
    resp = rt.respond("what food do I like?")
    # the trace is the machine-readable record of what the lookup actually did (spec §14)
    assert resp.trace.floor == "high"
    assert "food" in resp.trace.query_terms
    hit = next(h for h in resp.trace.hits if h.gene == "food")
    assert hit.decision == "blocked"
    assert hit.confidence == "low"                       # why it was blocked
    assert hit.strength > 0                              # it *did* match; the floor hid it


def test_trace_records_admitted_page():
    store, rt = make_runtime()
    put_page(store, "food", "you like apples", confidence="high")
    resp = rt.respond("what food do I like?")
    hit = next(h for h in resp.trace.hits if h.gene == "food")
    assert hit.decision == "admitted"
    assert resp.trace.abstained is False


def test_abstention_leaves_a_trace_too():
    store, rt = make_runtime()
    resp = rt.respond("what food do I like?")
    assert resp.abstained is True
    assert resp.trace.abstained is True
    assert "food" in resp.trace.query_terms              # what was probed, even with no pages

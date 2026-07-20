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

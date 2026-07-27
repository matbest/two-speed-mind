"""Slice 2 — the fast runtime (docs/tasks.md T5-T6).

RED until Runtime.respond is implemented.
"""
from kaineros.fakes import FakeFastModel
from kaineros.runtime import Runtime
from kaineros.schema import Page, Provenance
from kaineros.store import Store


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


def test_trace_records_routed_page():
    store, rt = make_runtime()
    put_page(store, "food", "you like apples", confidence="high")
    resp = rt.respond("what food do I like?")
    hit = next(h for h in resp.trace.hits if h.gene == "food")
    assert hit.decision == "routed"  # the page the fast brain was routed to (two-step, T16)
    assert resp.trace.abstained is False


def test_routing_reads_only_the_top_k_pages():
    store, rt = make_runtime()
    rt.route_k = 1
    put_page(store, "food.love", "you love mangoes", confidence="high")
    put_page(store, "food.hate", "you hate mangoes actually", confidence="high")
    resp = rt.respond("mangoes?")
    assert len(resp.used) == 1  # both matched, but only the top-1 was read
    decisions = {h.gene: h.decision for h in resp.trace.hits}
    assert "routed" in decisions.values()
    assert "matched" in decisions.values()  # the other matched but was NOT read (token saved)


def test_abstention_leaves_a_trace_too():
    store, rt = make_runtime()
    resp = rt.respond("what food do I like?")
    assert resp.abstained is True
    assert resp.trace.abstained is True
    assert "food" in resp.trace.query_terms              # what was probed, even with no pages


def test_multiple_choice_gets_a_selection_prompt_not_the_phraser():
    """PersonaMem's task is response-SELECTION: a lettered question wants a letter, not a phrased
    value. The fast brain must switch to the selection prompt so it names a choice grounded in the
    retrieved facts, instead of leaking chain-of-thought and getting truncated before it decides."""
    from kaineros.cloud import (
        PHRASE_SYSTEM,
        SELECT_SYSTEM,
        answer_prompt,
        is_multiple_choice,
    )
    from kaineros.schema import Page, Provenance

    mc = (
        "Which drink is my current go-to?\n"
        "(a) green tea\n(b) matcha lattes\n(c) cola\n"
        "Answer with the letter of the best option."
    )
    assert is_multiple_choice(mc)
    assert not is_multiple_choice("Where do I live?")
    assert not is_multiple_choice("What's my favourite (special) drink?")  # one paren, not options

    page = Page(gene="user.drink.go_to", content="matcha", gist="matcha lattes",
                provenance=Provenance(stated=True), tags=("drink",))
    sys_mc, user_mc = answer_prompt(mc, [page], [])
    sys_lookup, _ = answer_prompt("Where do I live?", [page], [])
    assert sys_mc is SELECT_SYSTEM and sys_lookup is PHRASE_SYSTEM
    assert "matcha lattes" in user_mc            # the grounded fact is put in front of the model
    assert user_mc.rstrip().endswith("letter.")  # and it's told to answer with only the letter

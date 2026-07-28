"""T24 / spec §52-53: step-by-step wiki search, streamed as it happens.

The fast brain browses the wiki hop by hop instead of grabbing route_k pages in one shot, and emits
a stream of SearchEvents — a `reading` event as each page/snippet is opened (the app's gaze-flick +
debug trace), then `token` events for the answer, then a `done` with the grounded Response. Bounded
by a hop cap (the latency knob), stops early when confident, stays grounded, and can dive into a raw
conversation snippet (§53) when the compiled facts don't answer. Built and tested on the fakes.
"""
from __future__ import annotations

from kaineros.fakes import FakeFastModel
from kaineros.runtime import Runtime
from kaineros.schema import Page, Provenance
from kaineros.store import Store


def make_rt(floor: str = "low", **kw):
    store = Store()
    return store, Runtime(store, FakeFastModel(), confidence_floor=floor, **kw)


def put_page(store, gene, content, confidence="high", tags=(), source_texts=()):
    store.clean[gene] = Page(
        gene=gene, content=content, tags=tuple(tags),
        provenance=Provenance(confidence=confidence, stated=True, source_texts=tuple(source_texts)),
    )


def run(rt, question, **kw):
    """Drain the search stream into (reading_events, tokens, done_response)."""
    reads, tokens, done = [], [], None
    for ev in rt.search(question, **kw):
        if ev.kind == "reading":
            reads.append(ev)
        elif ev.kind == "token":
            tokens.append(ev.text)
        elif ev.kind == "done":
            done = ev.response
    return reads, tokens, done


def test_search_reads_the_index_then_opens_a_page():
    store, rt = make_rt()
    put_page(store, "food", "you like apples")
    reads, tokens, done = run(rt, "what food do I like?")
    assert reads and reads[0].gene == "food"            # it opened a page from the index
    assert not done.abstained
    assert "apples" in done.answer                       # phrased from the page it read
    assert [p.gene for p in done.used] == ["food"]       # grounded in the page actually opened


def test_search_emits_a_reading_event_per_hop():
    store, rt = make_rt(confident_at=2)                   # weak (strength-1) matches never satisfy
    put_page(store, "hobby.chess", "chess on weekends")
    put_page(store, "hobby.music", "music every day")
    put_page(store, "hobby.art", "art in the evenings")
    reads, tokens, done = run(rt, "chess music art", max_hops=3)
    assert len(reads) == 3                                # one reading event per page opened
    assert [e.hop for e in reads] == [1, 2, 3]            # numbered in open order
    assert {e.gene for e in reads} == {"hobby.chess", "hobby.music", "hobby.art"}
    assert all(not e.raw for e in reads)                 # all fact pages, no raw dive here


def test_search_respects_the_hop_cap():
    store, rt = make_rt(confident_at=9)                   # never "confident" -> capped by hops alone
    for i in range(4):
        put_page(store, f"hobby.{i}", f"hobby number {i} thing")
    reads, _, _ = run(rt, "hobby thing", max_hops=2)
    assert len(reads) == 2                                # the latency guarantee: at most max_hops


def test_search_stops_early_when_confident():
    store, rt = make_rt(confident_at=2)
    put_page(store, "home.city", "your home city is berlin")   # strong: overlaps home + city
    put_page(store, "food", "you like apples")                 # a decoy, should never be opened
    reads, _, done = run(rt, "what is my home city?", max_hops=3)
    assert len(reads) == 1 and reads[0].gene == "home.city"    # stopped after the confident page
    assert [p.gene for p in done.used] == ["home.city"]


def test_search_streams_answer_tokens():
    store, rt = make_rt()
    put_page(store, "food", "you like apples")
    reads, tokens, done = run(rt, "what food do I like?")
    assert tokens                                         # the answer arrived as a token stream...
    assert "".join(tokens) == done.answer                # ...that reconstructs the final answer


def test_search_stays_grounded_and_abstains():
    store, rt = make_rt(floor="low")
    reads, tokens, done = run(rt, "what car do I drive?")
    assert done.abstained is True                         # nothing to read -> abstain, not invent
    assert not reads and not done.used and not tokens

    put_page(store, "car", "you drive a tesla")
    reads, _, done = run(rt, "what car do I drive?")
    assert not done.abstained
    assert "car" in done.why or "car" in " ".join(p.gene for p in done.used)  # why is the pages read


def test_search_can_open_a_raw_snippet_when_facts_dont_answer():
    store, rt = make_rt(confident_at=2)
    # the fact page doesn't mention seats at all — but a raw quote (the primary source, §46) does
    put_page(store, "user.pref", "the user has some preferences",
             source_texts=["I always take the window seat when I fly"])
    reads, tokens, done = run(rt, "which seat do I prefer?")
    raw_reads = [e for e in reads if e.raw]
    assert raw_reads                                      # it dove into the raw conversation...
    assert "window" in done.answer                        # ...and answered from the snippet

"""The PersonaMem bench harness on the fakes — offline, deterministic.

Tests the HARNESS (loading, replay, scoring mechanics), not model accuracy: rows come back for
every probe, the happy paths land, and the unanswerable probe abstains rather than guesses.
"""
from __future__ import annotations

from kaineros.bench import personamem
from kaineros.cli import Session


def test_sample_fixture_loads():
    sl = personamem.load_sample()
    assert len(sl.sessions) == 3
    assert len(sl.probes) == 4
    p = sl.probes[0]
    assert p.letter == "b"  # green tea is option 2
    assert "(a) coffee" in p.text and "(b) green tea" in p.text


def test_score_answer_variants():
    p = personamem.Probe(
        qid="q", qtype="t", question="?", options=["coffee", "green tea", "cola"],
        answer="green tea", after_session=0,
    )
    assert personamem.score_answer("b", p)
    assert personamem.score_answer("(b)", p)
    assert personamem.score_answer("B) green tea", p)
    assert personamem.score_answer("I'd say green tea.", p)
    assert not personamem.score_answer("a", p)
    assert not personamem.score_answer("coffee", p)
    # naming the right option only counts when no wrong option is named too
    assert not personamem.score_answer("maybe green tea or maybe cola", p)
    assert not personamem.score_answer("", p)


def test_run_slice_on_fakes():
    session = Session()  # synchronous, deterministic fakes
    sl = personamem.load_sample()
    rows = personamem.run_slice(session, sl)
    assert len(rows) == len(sl.probes)
    by_type = {r["type"]: r for r in rows}
    # recall probes: the fake fast model echoes the routed page, which names the right option
    assert by_type["recall_preference"]["correct"]
    assert by_type["recall_fact"]["correct"]
    # never-mentioned fact: the honest move is abstention, and it's recorded as such
    assert by_type["unanswerable"]["abstained"]
    assert not by_type["unanswerable"]["correct"]
    # every row carries the cost/latency fields the scorecard reports
    for r in rows:
        assert {"qid", "type", "expected", "answer", "deep_tok", "fast_tok", "seconds"} <= set(r)


def test_summarise_reports_accuracy_and_abstention():
    rows = [
        {"type": "recall", "correct": True, "abstained": False},
        {"type": "recall", "correct": False, "abstained": True},
    ]
    lines = personamem.summarise(rows)
    assert any("accuracy 1/2" in line for line in lines)
    assert any("abstained 1" in line for line in lines)

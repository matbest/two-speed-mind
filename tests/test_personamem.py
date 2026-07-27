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
    assert p.letter == "b"  # window is option 2
    assert "(a) aisle" in p.text and "(b) window" in p.text


def test_big_fixture_loads_and_is_well_formed():
    sl = personamem.load_sample(personamem.SAMPLE_BIG)
    assert len(sl.sessions) == 8
    assert len(sl.probes) == 10
    for p in sl.probes:
        assert p.answer in p.options  # every probe's answer is one of its own options
        assert 0 <= p.after_session < len(sl.sessions)
    # the query shapes the real benchmark cares about are all represented
    types = {p.qtype for p in sl.probes}
    assert {"update", "conditional", "preference_evolution", "new_scenario", "unanswerable"} <= types


def test_score_answer_variants():
    p = personamem.Probe(
        qid="q", qtype="t", question="?", options=["coffee", "green tea", "cola"],
        answer="green tea", after_session=0,
    )
    assert personamem.score_answer("b", p)
    assert personamem.score_answer("(b)", p)
    assert personamem.score_answer("B) green tea", p)
    assert personamem.score_answer("I'd say green tea.", p)
    # the system's own honesty must not read as wrongness (seen live on sample-big):
    assert personamem.score_answer('"b"', p)
    assert personamem.score_answer("If I remember rightly: b", p)
    assert personamem.score_answer('If I remember rightly: "b"', p)
    assert not personamem.score_answer("If I remember rightly: a", p)  # hedged AND wrong is wrong
    assert not personamem.score_answer("a", p)
    assert not personamem.score_answer("coffee", p)
    # 'a' as an article is not a letter pick
    pa = personamem.Probe(qid="q", qtype="t", question="?",
                          options=["warm milk", "cola", "tea"], answer="warm milk",
                          after_session=0)
    assert not personamem.score_answer("a glass of cola at night", pa)
    # naming the right option only counts when no wrong option is named too
    assert not personamem.score_answer("maybe green tea or maybe cola", p)
    assert not personamem.score_answer("", p)


def test_run_slice_on_fakes():
    session = Session()  # synchronous, deterministic fakes
    sl = personamem.load_sample()
    rows, stats = personamem.run_slice(session, sl)
    assert len(rows) == len(sl.probes)
    # the population block: extraction happened, pools exist, the judge was consulted
    assert stats["inserted"] == 5 and stats["genes"] >= 4
    assert stats["judge_calls"]["total"] > 0
    # the counting wrapper is removed afterwards - the session leaves as it arrived
    assert not isinstance(session.compiler.judge, personamem._CountingJudge)
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


def test_dataset_loader_and_smoke_cap(tmp_path):
    """The real-data loader against a minimal fake dataset mirroring PersonaMem's actual schema
    (lettered options, letter answers, context keyed by shared_context_id, 'User:' prefixes),
    incl. the max_sessions cap the smoke run uses."""
    import json

    (tmp_path / "questions_32k.csv").write_text(
        "persona_id,question_id,question_type,user_question_or_message,correct_answer,"
        "all_options,shared_context_id,end_index_in_shared_context\n"
        '0,q1,recall,"What pet?",(b),"[""(a) a cat"", ""(b) a dog""]",CTX,999\n'
        '0,q2,recall,"What city?",(a),"[""(a) london"", ""(b) paris""]",CTX,999\n',
        encoding="utf-8",
    )
    msgs = [{"role": "system", "content": "persona"}]
    msgs += [{"role": "user", "content": f"User: turn {i}"} for i in range(35)]  # 4 sessions
    (tmp_path / "shared_contexts_32k.jsonl").write_text(
        json.dumps({"CTX": msgs}) + "\n", encoding="utf-8"
    )

    full = personamem.load_dataset_slice(data_dir=tmp_path)
    assert len(full.sessions) == 4 and len(full.probes) == 2
    assert full.probes[0].answer == "a dog"          # (b) -> the 2nd option, letter stripped
    assert full.sessions[0][0] == "turn 0"           # 'User:' prefix stripped

    smoke = personamem.load_dataset_slice(data_dir=tmp_path, limit=1, max_sessions=2)
    assert len(smoke.sessions) == 2 and len(smoke.probes) == 1  # capped context + question count


def test_run_baseline_reads_raw_history_and_scores_like_run_slice():
    """The single-model baseline feeds each probe the full history to one `ask` callable and scores
    with the same score_answer — so a baseline row is directly comparable to a two-speed row."""
    sl = personamem.load_sample()
    seen_histories = []

    def fake_ask(system: str, user: str) -> str:
        seen_histories.append(user)
        # a perfect oracle: the fixture's first probe is 'window' = option (b); answer that one right,
        # everything else wrong — just enough to prove scoring + row shape, deterministically
        return "b" if "seat should you book" in user else "z"

    rows = personamem.run_baseline([sl], fake_ask)
    assert len(rows) == len(sl.probes)
    assert all(r["persona"] == sl.name for r in rows)
    # the model was handed the actual conversation, not just the question
    assert any("Green tea is my favourite drink." in u for u in seen_histories)
    by_type = {r["type"]: r for r in rows}
    assert by_type["recall_preference"]["correct"]      # answered 'b' -> window, the right option
    assert not by_type["recall_fact"]["correct"]        # answered 'z' -> wrong
    # rows carry the same fields the scorecard reports, so summarise() works on them unchanged
    assert personamem.summarise(rows)[0].startswith("accuracy")


def test_persona_ids_lists_distinct_ids_in_file_order(tmp_path):
    """The multi-persona bench enumerates personas from the question set — distinct ids, file order,
    optionally capped to the first N (one persona is too few questions to aggregate anything from)."""
    (tmp_path / "questions_32k.csv").write_text(
        "persona_id,question_id,question_type,user_question_or_message,correct_answer,"
        "all_options,shared_context_id,end_index_in_shared_context\n"
        '7,q1,recall,"Q?",(a),"[""(a) x"", ""(b) y""]",CTX,1\n'
        '7,q2,recall,"Q?",(a),"[""(a) x"", ""(b) y""]",CTX,1\n'   # 7 again — deduped
        '3,q3,recall,"Q?",(a),"[""(a) x"", ""(b) y""]",CTX,1\n'
        '9,q4,recall,"Q?",(a),"[""(a) x"", ""(b) y""]",CTX,1\n',
        encoding="utf-8",
    )
    assert personamem.persona_ids(data_dir=tmp_path) == ["7", "3", "9"]  # order preserved, deduped
    assert personamem.persona_ids(n=2, data_dir=tmp_path) == ["7", "3"]  # first N


def test_dataset_loader_missing_data_gives_the_download_hint(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="hf download bowen-upenn/PersonaMem"):
        personamem.load_dataset_slice(data_dir=tmp_path / "nope")


def test_summarise_reports_accuracy_and_abstention():
    rows = [
        {"type": "recall", "correct": True, "abstained": False},
        {"type": "recall", "correct": False, "abstained": True},
    ]
    lines = personamem.summarise(rows)
    assert any("accuracy 1/2" in line for line in lines)
    assert any("abstained 1" in line for line in lines)

"""The eval corpus stays well-formed (free — no models, no tokens; docs/tasks.md T9a).

The corpus itself is graded by `python -m kaineros.evals`, which spends tokens and is
deliberately not part of pytest.
"""
from kaineros.evals import load_corpus


def test_conflicts_corpus_is_well_formed():
    from kaineros.evals import corpus_path

    scenarios = load_corpus(corpus_path("conflicts"))
    names = " ".join(s["name"] for s in scenarios)
    assert "static" in names and "dynamic" in names and "conditional" in names
    for s in scenarios:
        assert len(s["turns"]) >= 3
        for exp in s["expect"]:
            assert exp["keywords"] and exp["question"].strip()


def test_corpus_parses_and_is_well_formed():
    scenarios = load_corpus()
    assert len(scenarios) >= 5
    for s in scenarios:
        assert s["name"].strip()
        assert len(s["turns"]) >= 1 and all(t.strip() for t in s["turns"])
        assert len(s["expect"]) >= 1
        for exp in s["expect"]:
            assert exp["keywords"], s["name"]
            assert all(k == k.lower() for k in exp["keywords"]), "keywords are lowercase"
            assert exp["question"].strip(), s["name"]


def test_corpus_runs_on_the_fakes_without_error():
    # not graded — the fake extractor is deliberately crude; this only pins that the harness
    # itself is sound (feeding, settling, probing) with zero cost
    from kaineros.evals import run_scenario

    results = run_scenario(load_corpus()[0], fakes=True)
    assert len(results) >= 2  # a stored-check and an answered-check per expectation

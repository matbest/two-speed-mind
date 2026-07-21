"""Metrics scoring computes sane rows on the fakes (spec: comparable-to-benchmark metrics)."""
from kaineros.evals import corpus_path, load_corpus, score_scenario, run_metrics


def test_score_scenario_shape_on_fakes():
    static = next(
        s for s in load_corpus(corpus_path("conflicts")) if s.get("conflict_type") == "static"
    )
    row = score_scenario(static, fakes=True)
    assert row["type"] == "static"
    assert 0.0 <= row["aa"] <= 1.0
    assert 0.0 <= row["seh"] <= 1.0
    assert row["crs"] in (0.0, 1.0)          # a recognition verdict, either way
    assert row["deep_tok"] == 0              # fakes never spend
    assert "lag" in row                      # static/dynamic report displacement lag


def test_conditional_reports_false_question_count():
    cond = next(
        s for s in load_corpus(corpus_path("conflicts")) if s.get("conflict_type") == "conditional"
    )
    row = score_scenario(cond, fakes=True)
    assert "false_q" in row and row["false_q"] >= 0  # a question here would be a false positive
    assert "crs" not in row                          # CRS doesn't apply to conditional


def test_run_metrics_prints_a_scorecard():
    lines: list[str] = []
    run_metrics(load_corpus(corpus_path("conflicts")), fakes=True, openrouter=False, free=False, out=lines.append)
    text = "\n".join(lines)
    assert "METRICS" in text
    assert "overall: AA" in text
    assert "CRS" in text  # the conflict-recognition line, Kaineros's distinctive metric

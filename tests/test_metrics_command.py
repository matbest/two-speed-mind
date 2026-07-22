"""The /metrics slash command runs a conflict scenario live and scores it (fakes, no credits)."""
from kaineros.cli import Session, main
from kaineros import profiles


def test_conflict_metric_scores_into_a_throwaway_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    # a real fact in default, then run the conflict>static metric (family + submenu), then quit
    lines = iter(["i live in st leonards", "/metrics conflict static", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    assert main(["--plain"]) == 0

    out = capsys.readouterr().out
    assert "metric: static" in out          # it ran the static scenario
    assert "SCORE [static]" in out
    assert "CRS" in out
    # the user's own default mind is untouched; metrics ran in its own throwaway profile
    assert Session(store_dir=profiles.mind_dir("default")).store.pages()
    assert "metrics-conflict-static" in profiles.list_profiles()


def test_retrieval_family_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    lines = iter(["/metrics retrieval", "/quit"])  # retrieval family has no submenu
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    assert main(["--plain"]) == 0
    out = capsys.readouterr().out
    assert "SEH" in out and "SCORE" in out
    assert "metrics-retrieval" in profiles.list_profiles()


def test_metrics_family_picker_cancels_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    lines = iter(["/metrics", "nonsense", "/quit"])  # invalid family pick
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    assert main(["--plain"]) == 0
    assert "(cancelled)" in capsys.readouterr().out

"""The /metrics slash command runs a conflict scenario live and scores it (fakes, no credits)."""
from kaineros.cli import Session, main
from kaineros import profiles


def test_metrics_command_scores_into_a_throwaway_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    # a real fact in the default profile, then run /metrics on the static scenario, then quit
    lines = iter(["i live in st leonards", "/metrics static", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    assert main(["--plain"]) == 0

    out = capsys.readouterr().out
    assert "metric: static" in out          # it ran the static scenario
    assert "SCORE [static]" in out          # and printed a scorecard line
    assert "CRS" in out                     # with the recognition metric
    # the user's own default mind is untouched; metrics ran in its own throwaway profile
    assert Session(store_dir=profiles.mind_dir("default")).store.pages()
    assert "metrics-static" in profiles.list_profiles()


def test_metrics_picker_cancels_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAINEROS_HOME", str(tmp_path))
    lines = iter(["/metrics", "nonsense", "/quit"])  # /metrics then an invalid pick
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    assert main(["--plain"]) == 0
    assert "(cancelled)" in capsys.readouterr().out

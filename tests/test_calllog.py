"""The raw model-call transcript: adapters record exactly what crossed the wire."""
from __future__ import annotations

import json

from kaineros import calllog, openrouter


def test_disabled_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr(calllog, "_path", None)
    calllog.log("deep", "x", "m", "p", "s", "i", "o")  # must not raise, must write nothing
    assert list(tmp_path.iterdir()) == []


def test_openrouter_chat_is_transcribed(monkeypatch, tmp_path):
    monkeypatch.setattr(calllog, "_path", None)
    log_file = calllog.enable(tmp_path)
    monkeypatch.setattr(openrouter, "_key", lambda: "test")
    monkeypatch.setattr(
        openrouter,
        "_request",
        lambda path, body=None: {
            "choices": [{"message": {"content": "the reply"}}],
            "usage": {"total_tokens": 42},
        },
    )
    out = openrouter._chat("some/model", "sys prompt", "user prompt",
                           brain="fast", purpose="phrase")
    assert out == "the reply"
    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["brain"] == "fast" and entry["purpose"] == "phrase"
    assert entry["system"] == "sys prompt" and entry["input"] == "user prompt"
    assert entry["output"] == "the reply" and entry["tokens"] == 42
    monkeypatch.setattr(calllog, "_path", None)  # leave the module as we found it


def test_pending_then_reply(monkeypatch):
    """Two-phase: the request shows as awaiting, then the raw reply lands in the same entry."""
    from kaineros.view import calls_panel

    monkeypatch.setattr(calllog, "_path", None)
    calllog._recent.clear()
    entry = calllog.begin("deep", "claude-cli", "sonnet", "extract", "sys", "compile this")
    panel = calls_panel(calllog.recent())
    assert "compile this" in panel                       # the request shows immediately
    assert '{"candidates": []}' not in panel             # no reply yet, no clutter
    calllog.finish(entry, '{"candidates": []}', tokens=910)
    panel = calls_panel(calllog.recent())
    assert '{"candidates": []}' in panel                 # the raw reply landed
    assert "s" in panel and entry["elapsed"] is not None  # with its latency stamp
    calllog._recent.clear()


def test_recent_feeds_the_live_panel_even_with_file_off(monkeypatch):
    from kaineros.view import calls_panel

    monkeypatch.setattr(calllog, "_path", None)
    calllog._recent.clear()
    assert "no model calls yet" in calls_panel(calllog.recent())  # empty state
    calllog.log("deep", "claude-cli", "sonnet", "judge.conflicts",
                "sys", "Do A and B contradict?", '{"conflicts": false}', tokens=412)
    panel = calls_panel(calllog.recent())
    assert "deep · judge.conflicts" in panel      # the header: which brain, why
    assert "Do A and B contradict?" in panel      # the actual text we asked
    assert "412 tok" in panel
    calllog._recent.clear()


def test_calls_panel_is_newest_first(monkeypatch):
    from kaineros.view import calls_panel

    monkeypatch.setattr(calllog, "_path", None)
    calllog._recent.clear()
    calllog.log("deep", "b", "m", "extract", "s", "the FIRST call", "o")
    calllog.log("fast", "b", "m", "phrase", "s", "the SECOND call", "o")
    panel = calls_panel(calllog.recent())
    # newest first, so the latest survives the Panel's bottom-crop
    assert panel.index("SECOND") < panel.index("FIRST")
    calllog._recent.clear()


def test_debug_command_sets_panel_depth():
    from kaineros.cli import Session, _cmd_debug

    s = Session()
    s.cloud = True
    assert _cmd_debug(s, ["10"]) is True and s.calls_lines == 10   # direct arg
    assert _cmd_debug(s, ["off"]) is True and s.calls_lines == 0   # hide
    assert _cmd_debug(s, ["off"]) is False                         # no change -> no repaint
    # menu positions: 1=off 2=5 3=10 4=15 5=20
    assert _cmd_debug(s, ["4"]) is True and s.calls_lines == 15
    assert _cmd_debug(s, ["2"]) is True and s.calls_lines == 5


def test_debug_panel_height_tracks_setting():
    from kaineros.cli import Session, _calls_panel_height, _header_height

    s = Session()
    s.cloud = True
    s.calls_lines = 0
    assert _calls_panel_height(s) == 0
    base = _header_height(s)
    s.calls_lines = 20
    assert _calls_panel_height(s) == 22  # 20 + border
    assert _header_height(s) == base + 22
    s.cloud = False  # offline: no panel regardless
    s.calls_lines = 20
    assert _calls_panel_height(s) == 0


def test_recent_keeps_only_the_tail(monkeypatch):
    monkeypatch.setattr(calllog, "_path", None)
    calllog._recent.clear()
    for i in range(20):
        calllog.log("deep", "b", "m", "extract", "s", f"turn {i}", "out")
    rec = calllog.recent()
    assert len(rec) == 8 and rec[-1]["input"] == "turn 19"  # bounded ring, newest last
    calllog._recent.clear()


def test_entries_append_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(calllog, "_path", None)
    log_file = calllog.enable(tmp_path)
    calllog.log("deep", "b", "m", "extract", "s", "turns", "candidates", tokens=10)
    calllog.log("fast", "b", "m", "phrase", "s", "question", "answer", tokens=5)
    lines = [json.loads(x) for x in log_file.read_text(encoding="utf-8").splitlines()]
    assert [e["purpose"] for e in lines] == ["extract", "phrase"]
    assert lines[0]["at"] <= lines[1]["at"]
    monkeypatch.setattr(calllog, "_path", None)

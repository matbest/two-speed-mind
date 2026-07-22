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


def test_entries_append_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(calllog, "_path", None)
    log_file = calllog.enable(tmp_path)
    calllog.log("deep", "b", "m", "extract", "s", "turns", "candidates", tokens=10)
    calllog.log("fast", "b", "m", "phrase", "s", "question", "answer", tokens=5)
    lines = [json.loads(x) for x in log_file.read_text(encoding="utf-8").splitlines()]
    assert [e["purpose"] for e in lines] == ["extract", "phrase"]
    assert lines[0]["at"] <= lines[1]["at"]
    monkeypatch.setattr(calllog, "_path", None)

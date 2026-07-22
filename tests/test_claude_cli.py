"""The claude -p adapter, with subprocess faked — no CLI, no network, no subscription.

The contract under test: env scrubbing (a nested session must not inherit this session's
proxy/auth), verdict parsing, token metering, and the clear re-login error on expired OAuth.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from kaineros import claude_cli
from kaineros.metering import TokenMeter
from kaineros.schema import Candidate, Provenance


@dataclass
class FakeProc:
    stdout: str
    stderr: str = ""
    returncode: int = 0


def _cli_json(result: str, is_error: bool = False, tokens: int = 10) -> str:
    return json.dumps(
        {
            "result": result,
            "is_error": is_error,
            "usage": {"input_tokens": tokens, "output_tokens": 5},
        }
    )


def _cand(text: str) -> Candidate:
    return Candidate(gene="g", content=text, provenance=Provenance(stated=True))


def test_scrubbed_env_drops_session_auth(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("HOME_ISH_THING", "keep-me")
    env = claude_cli._env()
    assert "ANTHROPIC_BASE_URL" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDECODE" not in env
    assert env["HOME_ISH_THING"] == "keep-me"


def test_judge_verdict_parses_and_meters(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc(stdout=_cli_json('{"a_is_better": true}', tokens=40))

    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    meter = TokenMeter()
    judge = claude_cli.ClaudeCLIJudge(meter=meter)
    assert judge.better("g", _cand("a"), _cand("b")) is True
    assert meter.total == 45  # 40 in + 5 out
    assert "-p" in calls[0] and "--no-session-persistence" in calls[0]


def test_degenerate_output_falls_back_conservative(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: FakeProc(stdout=_cli_json("no json here at all"))
    )
    judge = claude_cli.ClaudeCLIJudge()
    # unparseable twice -> the conservative verdict, never a crash mid-housekeeping
    assert judge.better("g", _cand("a"), _cand("b")) is False


def test_expired_login_gets_actionable_message(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: FakeProc(
            stdout=_cli_json("API Error: 401 OAuth access token has expired.", is_error=True)
        ),
    )
    with pytest.raises(claude_cli.ClaudeCLIError, match="/login"):
        claude_cli.preflight()


def test_missing_binary_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    with pytest.raises(claude_cli.ClaudeCLIError, match="not on PATH"):
        claude_cli.preflight()

"""Deep brain on your Claude subscription: adapters that shell out to `claude -p`.

The Claude Code CLI is already installed and logged in on this machine; print mode (`-p`) turns
it into a one-shot model call billed to the subscription, not to an API key. That suits the deep
role exactly: it runs off the interactive path, so the seconds of process startup per call cost
nothing the user can feel. But they DO make each judge comparison expensive in wall-clock — one
more reason the compiler keeps ranking O(log n) per insert and should settle whatever it can
from candidate metadata before asking a model at all.

The fast role stays elsewhere (OpenRouter free, or the fakes): phrasing sits on the interactive
path, where a subprocess per turn would be felt.

Models: `haiku`/`sonnet`/`opus` as the CLI understands them; KAINEROS_CLI_DEEP_MODEL overrides.
Auth: whatever `claude` is logged in as. If the OAuth token has expired the calls fail with a
clear "run `claude` and /login" message rather than a bare 401.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .cloud import (
    EXTRACT_SCHEMA,
    EXTRACT_SYSTEM,
    JUDGE_SYSTEM,
    _bool_schema,
    better_prompt,
    candidates_from_items,
    conflicts_prompt,
    same_account_prompt,
    same_claim_prompt,
)
from .openrouter import _json  # the tolerant parser — CLI output has no schema forcing either
from .schema import Candidate, Turn

DEEP_MODEL = os.environ.get("KAINEROS_CLI_DEEP_MODEL", "sonnet")
# generous: cold process start + model generation; the deep brain is off the interactive path
TIMEOUT = float(os.environ.get("KAINEROS_CLI_TIMEOUT", "180"))

# Running nested inside a Claude Code session, the child would inherit this session's proxy and
# auth environment and 401. Scrub anything Anthropic/Claude-Code-shaped so the child uses the
# machine's own stored login.
_SCRUB_EXACT = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")
_SCRUB_PREFIX = ("CLAUDE_CODE_", "CLAUDE_AGENT_")


class ClaudeCLIError(RuntimeError):
    """A `claude -p` call failed in a way retries won't fix (missing binary, expired login)."""


def _env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in _SCRUB_EXACT and not k.startswith(_SCRUB_PREFIX)
    }


def _exe() -> str:
    path = shutil.which("claude")
    if not path:
        raise ClaudeCLIError(
            "the `claude` CLI is not on PATH - install Claude Code, or use --openrouter instead"
        )
    return path


def _ask(system: str, user: str, model: str = DEEP_MODEL, meter=None, purpose: str = "?") -> str:
    """One subscription-billed model call. Returns the reply text; meters total tokens."""
    from . import calllog  # the raw transcript: exactly what was sent and what came back

    entry = calllog.begin("deep", "claude-cli", model, purpose, system, user)  # show the ask now
    result = ""
    total = None
    try:
        proc = subprocess.run(
            [
                _exe(), "-p",
                "--output-format", "json",
                "--model", model,
                "--system-prompt", system,
                "--tools", "",  # a comparator needs no tools - and mustn't wander off to use any
                "--no-session-persistence",
                user,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
            timeout=TIMEOUT,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = (proc.stderr or proc.stdout)[:300]
            raise ClaudeCLIError(
                f"claude -p returned no JSON (exit {proc.returncode}): {result}"
            ) from None
        result = data.get("result") or ""
        if data.get("is_error"):
            if "authenticat" in result.lower() or "oauth" in result.lower():
                raise ClaudeCLIError(
                    "your Claude login has expired - run `claude` in a terminal (then /login if "
                    f"asked) and retry. [{result[:200]}]"
                )
            raise ClaudeCLIError(f"claude -p error: {result[:300]}")
        u = data.get("usage") or {}
        total = (
            u.get("input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("output_tokens", 0)
        )
        if meter is not None:
            meter.add(total)
        return result
    finally:
        calllog.finish(entry, result, tokens=total or None)  # the reply lands in the panel


def _with_schema(user: str, schema: dict) -> str:
    # no response_format forcing over the CLI — ask for bare JSON and parse tolerantly
    return user + "\n\nRespond with ONLY a JSON object matching this schema, no other text:\n" + json.dumps(schema)


def preflight(model: str = DEEP_MODEL) -> None:
    """Fail fast with a clear message — one tiny call proves the binary and the login."""
    _ask("You reply with the single word: ok", "ping", model=model, purpose="preflight")


class ClaudeCLIJudge:
    """Deep-role pairwise verdicts on the subscription — same prompts as every other backend."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter

    def _verdict(self, field: str, question: str) -> bool:
        # a boolean verdict needs no schema — appending the full JSON schema made the model echo
        # it back (~19% of calls, wasted). Ask for the concrete answer shape instead.
        ask = f'{question}\n\nAnswer with ONLY this JSON: {{"{field}": true}} or {{"{field}": false}}'
        for _ in range(2):  # retry degenerate output once, then the conservative verdict
            content = _ask(
                JUDGE_SYSTEM, ask, model=self.model, meter=self.meter, purpose=f"judge.{field}",
            )
            try:
                return bool(_json(content).get(field, False))
            except ValueError:
                continue
        return False

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("a_is_better", better_prompt(gene, a, b))

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_claim", same_claim_prompt(gene, a, b))

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_account", same_account_prompt(gene, a, b))

    def conflicts(self, a: Candidate, b: Candidate) -> bool:
        return self._verdict("conflicts", conflicts_prompt(a, b))


class ClaudeCLISlowModel:
    """Deep-role extraction on the subscription — same schema and rules as the other backends."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter

    def list_models(self) -> list[str]:
        return ["haiku", "sonnet", "opus"]  # the aliases the CLI accepts

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        users = [t for t in turns if t.speaker == "user"]
        if not users:
            return []
        numbered = "\n".join(f"[{i}] {t.text}" for i, t in enumerate(users))
        items: list[dict] = []
        for _ in range(2):  # retry once on degenerate output, then extract nothing
            content = _ask(
                EXTRACT_SYSTEM,
                _with_schema(f"Conversation turns:\n{numbered}", EXTRACT_SCHEMA),
                model=self.model,
                meter=self.meter,
                purpose="extract",
            )
            try:
                items = _json(content).get("candidates", [])
                break
            except ValueError:
                continue
        return candidates_from_items(items, users)

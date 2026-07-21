"""OpenRouter adapters (Slice 5): the same three roles, any provider's models.

OpenRouter speaks the OpenAI chat-completions dialect, so this adapter is plain HTTP via the
stdlib — no new dependency. The two-speed split still maps onto model tiers, routed per role:
deep calls (extract + the three judge questions) go to the deep model, phrasing goes to the fast
model. The prompts are shared with cloud.py — every backend asks the same questions.

Key: OPENROUTER_API_KEY env var, or a git-ignored `.openrouter_key` file at the repo root.
Models: OPENROUTER_DEEP_MODEL / OPENROUTER_FAST_MODEL env vars override the defaults, and
`/model deep|fast <id>` switches at runtime.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .cloud import (
    EXTRACT_SCHEMA,
    EXTRACT_SYSTEM,
    JUDGE_SYSTEM,
    PHRASE_SYSTEM,
    _bool_schema,
    better_prompt,
    candidates_from_items,
    phrase_user,
    same_account_prompt,
    same_claim_prompt,
)
from .schema import Candidate, Page, Turn

API = "https://openrouter.ai/api/v1"
DEEP_MODEL = os.environ.get("OPENROUTER_DEEP_MODEL", "anthropic/claude-opus-4.8")
FAST_MODEL = os.environ.get("OPENROUTER_FAST_MODEL", "anthropic/claude-haiku-4.5")
# --free tier for development: zero token spend, at some quality/rate-limit cost.
# Free providers saturate; override without code changes when one is having a bad day.
FREE_DEEP_MODEL = os.environ.get("KAINEROS_FREE_DEEP", "nvidia/nemotron-3-super-120b-a12b:free")
FREE_FAST_MODEL = os.environ.get("KAINEROS_FREE_FAST", "nvidia/nemotron-3-nano-30b-a3b:free")
_KEY_FILE = Path(__file__).resolve().parents[2] / ".openrouter_key"


def _key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and _KEY_FILE.exists():
        key = _KEY_FILE.read_text().strip()
    if not key:
        raise RuntimeError(
            "OpenRouter needs a key: set OPENROUTER_API_KEY, or put the key in a "
            ".openrouter_key file at the repo root (git-ignored)"
        )
    return key


def ensure_key() -> None:
    """First-run UX: no key configured and we're at a terminal → ask once, save, move on.

    The key is read with hidden input and written to the git-ignored key file, so the next run
    doesn't ask. Non-interactive contexts (pipes, scripts) keep the clear hard error instead.
    """
    try:
        _key()
        return
    except RuntimeError:
        if not sys.stdin.isatty():
            raise
    print("First run with OpenRouter - paste your API key (from openrouter.ai/keys).")
    key = getpass.getpass("OpenRouter API key (input hidden): ").strip()
    if not key:
        raise RuntimeError("no key entered")
    _KEY_FILE.write_text(key + "\n")
    print(f"  saved to {_KEY_FILE} (git-ignored); delete that file to forget it")


def _request(path: str, body: dict | None = None) -> dict:
    for attempt, backoff in enumerate((5, 15, 30, None)):
        req = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and backoff is not None:
                time.sleep(backoff)  # free-tier rate limits are the common case here
                continue
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"openrouter {exc.code}: {detail}") from exc
        if isinstance(data, dict) and data.get("error"):
            code = data["error"].get("code") if isinstance(data["error"], dict) else None
            # OpenRouter reports upstream throttling/saturation in the body, not the HTTP status
            if code in (429, 500, 502, 503) and backoff is not None:
                time.sleep(backoff)
                continue
            raise RuntimeError(f"openrouter error: {data['error']}")
        return data
    raise RuntimeError("openrouter: retries exhausted")


def _chat(model: str, system: str, user: str, schema: dict | None = None, max_tokens: int = 1024) -> str:
    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "strict": True, "schema": schema},
        }
    try:
        data = _request("/chat/completions", body)
    except RuntimeError:
        if schema is None:
            raise
        # some (mostly free) models reject response_format — fall back to asking for the JSON
        # in the prompt; the tolerant parser handles fences/prose around it
        body.pop("response_format", None)
        body["messages"][1]["content"] += (
            "\n\nRespond with ONLY a JSON object matching this schema, no other text:\n"
            + json.dumps(schema)
        )
        data = _request("/chat/completions", body)
    return data["choices"][0]["message"].get("content") or ""


def _json(content: str) -> dict:
    """Tolerant parse — free-tier models wrap the JSON in prose, fences, or reasoning text.

    Strategy: plain parse; then any fenced block; then scan every '{' with raw_decode (which
    tolerates trailing text) and take the first valid non-empty object.
    """
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    fallback: dict | None = None
    for m in re.finditer(r"\{", content):
        try:
            obj, _ = decoder.raw_decode(content, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if obj:
                return obj
            fallback = obj  # an empty {} — keep looking for a real one
    if fallback is not None:
        return fallback
    raise ValueError(f"no JSON object in model output: {content[:200]!r}")


def preflight(model: str = FAST_MODEL) -> None:
    """Fail fast with a clear message — one tiny fast-model call proves key + route."""
    try:
        _chat(model, "You reply with the single word: ok", "ping", max_tokens=8)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"openrouter preflight failed: {exc}") from exc


def list_models() -> list[str]:
    return sorted(m["id"] for m in _request("/models")["data"])


class OpenRouterJudge:
    """Deep-role pairwise verdicts, forced-choice — same prompts as the cloud judge."""

    def __init__(self, model: str = DEEP_MODEL) -> None:
        self.model = model

    def _verdict(self, field: str, question: str) -> bool:
        content = _chat(self.model, JUDGE_SYSTEM, question, schema=_bool_schema(field))
        # missing field -> conservative False (incumbent defends / not-same); never crash the pass
        return bool(_json(content).get(field, False))

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("a_is_better", better_prompt(gene, a, b))

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_claim", same_claim_prompt(gene, a, b))

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_account", same_account_prompt(gene, a, b))


class OpenRouterSlowModel:
    """Deep-role extraction — the same schema and rules as the cloud extractor."""

    def __init__(self, model: str = DEEP_MODEL) -> None:
        self.model = model

    def list_models(self) -> list[str]:
        return list_models()

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        users = [t for t in turns if t.speaker == "user"]
        if not users:
            return []
        numbered = "\n".join(f"[{i}] {t.text}" for i, t in enumerate(users))
        content = _chat(
            self.model,
            EXTRACT_SYSTEM,
            f"Conversation turns:\n{numbered}",
            schema=EXTRACT_SCHEMA,
            max_tokens=4096,
        )
        return candidates_from_items(_json(content).get("candidates", []), users)


class OpenRouterFastModel:
    """The fast-role phraser — words only, on the cheap quick model."""

    def __init__(self, model: str = FAST_MODEL) -> None:
        self.model = model

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        return _chat(
            self.model, PHRASE_SYSTEM, phrase_user(question, pages, buffer), max_tokens=300
        ).strip()

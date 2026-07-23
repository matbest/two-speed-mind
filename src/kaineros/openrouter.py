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
    conflicts_prompt,
    phrase_user,
    same_account_prompt,
    same_claim_prompt,
)
from .schema import Candidate, Page, Turn

class RateLimitedError(RuntimeError):
    """The free-tier daily limit is spent. Carries the reset time so the app can wait it out."""

    def __init__(self, message: str, reset_at: float) -> None:
        super().__init__(message)
        self.reset_at = reset_at  # epoch seconds when the limit resets


API = "https://openrouter.ai/api/v1"
DEEP_MODEL = os.environ.get("OPENROUTER_DEEP_MODEL", "anthropic/claude-opus-4.8")
FAST_MODEL = os.environ.get("OPENROUTER_FAST_MODEL", "anthropic/claude-haiku-4.5")
# --free tier for development: zero token spend, at some quality/rate-limit cost.
# Free providers saturate; override without code changes when one is having a bad day.
FREE_DEEP_MODEL = os.environ.get("KAINEROS_FREE_DEEP", "nvidia/nemotron-3-super-120b-a12b:free")
FREE_FAST_MODEL = os.environ.get("KAINEROS_FREE_FAST", "nvidia/nemotron-3-nano-30b-a3b:free")
_KEY_FILE = Path(__file__).resolve().parents[2] / ".openrouter_key"
# per-request ceiling (seconds); retries add up to ~50s of backoff on top of this
TIMEOUT = float(os.environ.get("KAINEROS_TIMEOUT", "120"))


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


def _next_utc_midnight() -> float:
    now = time.time()
    return (int(now // 86400) + 1) * 86400  # next 00:00 UTC in epoch seconds


def _request(path: str, body: dict | None = None) -> dict:
    for attempt, backoff in enumerate((5, 15, 30, None)):
        req = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            if exc.code == 429 and (
                exc.headers.get("X-RateLimit-Remaining") == "0" or "per-day" in body
            ):
                # a daily/hard limit won't recover in seconds — carry the reset time so the app
                # can show a countdown and wait it out, rather than dying
                reset_ms = exc.headers.get("X-RateLimit-Reset")
                reset_at = float(reset_ms) / 1000.0 if reset_ms else _next_utc_midnight()
                raise RateLimitedError(
                    "OpenRouter free-tier daily limit reached (shared across all free models). "
                    "Options: run `kaineros` (offline fakes), `kaineros --openrouter` (paid — a "
                    "few cents), or wait for the reset.",
                    reset_at,
                ) from exc
            if exc.code in (429, 500, 502, 503) and backoff is not None:
                time.sleep(backoff)  # a transient per-minute limit — worth a short wait
                continue
            raise RuntimeError(f"openrouter {exc.code}: {body[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:  # network drop / request timeout
            if backoff is not None:
                time.sleep(backoff)
                continue
            raise RuntimeError(f"openrouter unreachable: {exc}") from exc
        if isinstance(data, dict) and data.get("error"):
            code = data["error"].get("code") if isinstance(data["error"], dict) else None
            # OpenRouter reports upstream throttling/saturation in the body, not the HTTP status
            if code in (429, 500, 502, 503) and backoff is not None:
                time.sleep(backoff)
                continue
            raise RuntimeError(f"openrouter error: {data['error']}")
        return data
    raise RuntimeError("openrouter: retries exhausted")


def _chat(model, system, user, schema=None, max_tokens=1024, meter=None,
          brain="deep", purpose="?") -> str:
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
    from . import calllog  # record exactly what crossed the wire (incl. any fallback rewrite)

    entry = calllog.begin(brain, "openrouter", model, purpose, system, user)  # show the ask now
    content = ""
    total = None
    try:
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
        u = data.get("usage") or {}
        total = u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
        if meter is not None:
            meter.add(total)
        content = data["choices"][0]["message"].get("content") or ""
        return content
    finally:
        calllog.finish(entry, content, tokens=total or None)  # the reply lands in the panel


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
        _chat(model, "You reply with the single word: ok", "ping", max_tokens=8,
              brain="fast", purpose="preflight")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"openrouter preflight failed: {exc}") from exc


def list_models() -> list[str]:
    return sorted(m["id"] for m in _request("/models")["data"])


class OpenRouterJudge:
    """Deep-role pairwise verdicts, forced-choice — same prompts as the cloud judge."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter

    def _verdict(self, field: str, question: str) -> bool:
        # free models occasionally emit degenerate output — retry once, then default to the
        # conservative verdict (incumbent defends / not-same); never crash a housekeeping pass
        for _ in range(2):
            content = _chat(self.model, JUDGE_SYSTEM, question, schema=_bool_schema(field),
                            meter=self.meter, brain="deep", purpose=f"judge.{field}")
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


class OpenRouterSlowModel:
    """Deep-role extraction — the same schema and rules as the cloud extractor."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter

    def list_models(self) -> list[str]:
        return list_models()

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        users = [t for t in turns if t.speaker == "user"]
        if not users:
            return []
        numbered = "\n".join(f"[{i}] {t.text}" for i, t in enumerate(users))
        items: list[dict] = []
        for _ in range(2):  # retry once on degenerate output, then extract nothing
            content = _chat(
                self.model,
                EXTRACT_SYSTEM,
                f"Conversation turns:\n{numbered}",
                schema=EXTRACT_SCHEMA,
                max_tokens=4096,
                meter=self.meter,
                brain="deep",
                purpose="extract",
            )
            try:
                items = _json(content).get("candidates", [])
                break
            except ValueError:
                continue
        return candidates_from_items(items, users)


class OpenRouterFastModel:
    """The fast-role phraser — words only, on the cheap quick model."""

    def __init__(self, model: str = FAST_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        return _chat(
            self.model, PHRASE_SYSTEM, phrase_user(question, pages, buffer),
            max_tokens=300, meter=self.meter, brain="fast", purpose="phrase",
        ).strip()

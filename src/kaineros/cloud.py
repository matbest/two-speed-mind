"""Cloud model adapters (Slice 5, T9a).

Real models behind the same Protocols, on the Claude API. The two-speed split maps onto model
tiers: deep roles (extractor + judge) default to Opus, the fast phraser to Haiku — heavyweight
cognition stays off the interactive path, featherweight rendering on it.

The grounding rule extends to the model boundary: judge calls are forced-choice (a structured
boolean, never parsed prose) and extraction fills a Candidate JSON schema. The fast model returns
words only; it decides nothing.

Requires: pip install -e ".[cloud]" and ANTHROPIC_API_KEY (or an `ant auth login` profile).
The core and all tests never import this module.
"""
from __future__ import annotations

import json
import time

from .schema import Candidate, Page, Provenance, Turn

DEEP_MODEL = "claude-opus-4-8"
FAST_MODEL = "claude-haiku-4-5"


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'cloud models need the anthropic package: pip install -e ".[cloud]"'
        ) from exc
    try:
        return anthropic.Anthropic()
    except Exception as exc:
        raise RuntimeError(
            "cloud models need credentials: set ANTHROPIC_API_KEY (or `ant auth login`)"
        ) from exc


def preflight() -> None:
    """Fail fast, with a clear message, if the API isn't reachable — a free call, no tokens.

    The SDK only validates credentials at request time, so constructing adapters succeeds even
    with no key; this surfaces the problem before the first real turn.
    """
    client = _client()
    try:
        client.models.list()
    except TypeError as exc:  # the SDK's no-auth-resolved error
        raise RuntimeError(
            "cloud models need credentials: set ANTHROPIC_API_KEY (or `ant auth login`)"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"cloud credential check failed: {exc}") from exc


def _first_text(response) -> str:
    return next(b.text for b in response.content if b.type == "text")


def _bool_schema(field: str) -> dict:
    return {
        "type": "object",
        "properties": {field: {"type": "boolean"}},
        "required": [field],
        "additionalProperties": False,
    }


def _describe(label: str, c: Candidate) -> str:
    prov = c.provenance
    said = "stated directly by the user" if prov.stated else "inferred"
    age_days = max(0.0, (time.time() - prov.created_at) / 86400.0)
    return f"{label} ({said}, ~{age_days:.1f} days old, confidence {prov.confidence}): {c.content}"


# The prompts are the adapter-independent part — every backend (Anthropic API, OpenRouter, local)
# asks the same questions; only the transport differs.

JUDGE_SYSTEM = (
    "You maintain a personal knowledge base about one user. Judge the comparison "
    "and answer with only the JSON verdict."
)


def better_prompt(gene: str, a: Candidate, b: Candidate) -> str:
    return (
        f"Topic key: {gene}\n"
        "Two competing accounts of the same claim about the user. Which is the better one to "
        "believe? Prefer more accurate, more specific, and more current accounts; a newer "
        "direct statement usually beats an older or inferred one.\n"
        f"{_describe('A', a)}\n{_describe('B', b)}\n"
        "Is A the better account?"
    )


def same_claim_prompt(gene: str, a: Candidate, b: Candidate) -> str:
    return (
        f"Topic key: {gene}\n"
        "Are A and B rival accounts of the SAME claim — i.e. they answer the same question "
        "about the user and would compete to be the truth? Same topic is NOT enough: 'loves "
        "mangoes' and 'allergic to peanuts' are different claims.\n"
        f"{_describe('A', a)}\n{_describe('B', b)}"
    )


def same_account_prompt(gene: str, a: Candidate, b: Candidate) -> str:
    return (
        f"Topic key: {gene}\n"
        "Do A and B assert the same thing — is one just a restatement or paraphrase of the "
        "other, adding no new information?\n"
        f"{_describe('A', a)}\n{_describe('B', b)}"
    )


PHRASE_SYSTEM = (
    "You are the voice of a personal assistant. Answer the user's question using "
    "ONLY the retrieved notes. Never invent facts; if the notes conflict, say both "
    "versions. One or two sentences, conversational."
)


def phrase_user(question: str, pages: list[Page], buffer: list[Turn]) -> str:
    notes = "\n".join(f"- [{p.gene}] {p.content}" for p in pages)
    recent = "\n".join(f"{t.speaker}: {t.text}" for t in buffer[-6:])
    return (
        f"Retrieved notes:\n{notes or '- (none)'}\n\n"
        f"Recent conversation:\n{recent}\n\nQuestion: {question}"
    )


def candidates_from_items(items: list[dict], users: list[Turn]) -> list[Candidate]:
    """Map the extractor's schema-validated items onto Candidates with real provenance."""
    out: list[Candidate] = []
    for item in items:
        idx = min(max(0, item["source_turn"]), len(users) - 1)
        src = users[idx]
        out.append(
            Candidate(
                gene=item["gene"],
                content=item["content"],
                provenance=Provenance(
                    source_turn_ids=(src.id,),
                    created_at=src.created_at or time.time(),
                    stated=item["stated"],
                    confidence=item["confidence"],
                    stakes=item["stakes"],
                ),
            )
        )
    return out


class CloudJudge:
    """Pairwise verdicts from a strong model — forced-choice, low effort, tiny prompts."""

    def __init__(self, model: str = DEEP_MODEL) -> None:
        self.model = model
        self.client = _client()

    def _verdict(self, field: str, question: str) -> bool:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _bool_schema(field)},
            },
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": question}],
        )
        return bool(json.loads(_first_text(resp))[field])

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("a_is_better", better_prompt(gene, a, b))

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_claim", same_claim_prompt(gene, a, b))

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_account", same_account_prompt(gene, a, b))


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gene": {"type": "string"},
                    "content": {"type": "string"},
                    "stated": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "stakes": {"type": "string", "enum": ["low", "high"]},
                    "source_turn": {"type": "integer"},
                },
                "required": ["gene", "content", "stated", "confidence", "stakes", "source_turn"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """You are the slow-deep compiler of a personal assistant: you turn raw \
conversation into durable candidate facts about the user.

Rules:
- One candidate per distinct fact. Skip questions, chit-chat, and anything with no lasting value.
- `gene` is a short lowercase dotted key naming the SINGLE CLAIM the fact answers, stable across \
paraphrases: user.home_city, user.job.employer, user.daughter.name, user.pref.address_as. \
Different claims about one topic get different keys (user.food.loves vs user.food.allergy).
- The key names the QUESTION, never the answer: user.home_city, not user.home.berlin; \
user.residence.part_time, not user.residence.milton_keynes. The answer changes; the key must not.
- `content` is one self-contained sentence, understandable years later without the conversation.
- `stated`: true if the user said it outright; false if you inferred it.
- `confidence`: how sure you are the fact is real and correctly read.
- `stakes`: "low" only for persona/style preferences (name to use, tone, format); "high" for \
facts about the user's life and world.
- `source_turn`: the [index] of the turn the fact came from."""


class CloudSlowModel:
    """Extraction via structured outputs — gene keys and provenance arrive as data, not prose."""

    def __init__(self, model: str = DEEP_MODEL) -> None:
        self.model = model
        self.client = _client()

    def list_models(self) -> list[str]:
        return [m.id for m in self.client.models.list()]

    def extract(self, turns: list[Turn]) -> list[Candidate]:
        users = [t for t in turns if t.speaker == "user"]
        if not users:
            return []
        numbered = "\n".join(f"[{i}] {t.text}" for i, t in enumerate(users))
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": f"Conversation turns:\n{numbered}"}],
        )
        items = json.loads(_first_text(resp))["candidates"]
        return candidates_from_items(items, users)


class CloudFastModel:
    """The phraser: a small, fast model that renders retrieved pages into words — nothing more."""

    def __init__(self, model: str = FAST_MODEL) -> None:
        self.model = model
        self.client = _client()

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=PHRASE_SYSTEM,
            messages=[{"role": "user", "content": phrase_user(question, pages, buffer)}],
        )
        return _first_text(resp).strip()

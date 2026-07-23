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


def conflicts_prompt(a: Candidate, b: Candidate) -> str:
    return (
        "Two facts recorded about the user. Do they CONTRADICT — can they not both be true at "
        "once? ('works as a backend engineer' vs 'moved to the platform team, not backend' "
        "conflict; 'loves mangoes' and 'allergic to peanuts' do not). Answer only the JSON.\n"
        f"{_describe('A', a)}\n{_describe('B', b)}"
    )


PHRASE_SYSTEM = (
    "You are the voice of a personal assistant. Answer the user's question using ONLY the "
    "retrieved notes. Never invent facts; if the notes genuinely conflict, say both versions.\n"
    "Reply with ONLY the answer itself — one short sentence. Do NOT restate the question, do NOT "
    "write preamble or meta-commentary ('We need to answer', 'Using the notes', 'So the answer "
    "is'), do NOT show your reasoning. Start directly with the fact."
)


def _note(p: Page) -> str:
    # lead with the bare answer (spec §48) so the fast brain reads the value, not a sentence it
    # can misparse; keep the sentence in parens for nuance
    if p.gist:
        return f"- {p.gene} = {p.gist}  ({p.content})"
    return f"- [{p.gene}] {p.content}"


def phrase_user(question: str, pages: list[Page], buffer: list[Turn]) -> str:
    notes = "\n".join(_note(p) for p in pages)
    recent = "\n".join(f"{t.speaker}: {t.text}" for t in buffer[-6:])
    return (
        f"Retrieved notes:\n{notes or '- (none)'}\n\n"
        f"Recent conversation:\n{recent}\n\nQuestion: {question}"
    )


# A deliberate update is detected DETERMINISTICALLY from the turn text (not asked of the model in
# the prompt) — code enriches the element's metadata; the ranking brain uses it (spec §42). Expand
# the signal list rather than growing the extractor prompt.
_UPDATE_SIGNALS = (
    "as of today", "as of now", "from now on", "no longer", "not anymore", "these days",
    "nowadays", "i've moved", "i moved", "i've switched", "i switched", "i've relocated",
    "relocated to", "moved to", "switched to", "changed to", "changed jobs", "update:",
    "now i ", "actually i ", "instead of", ", not ", " not anymore",
    # leaving/joining language is deliberate-update phrasing too ("I've left the studio and
    # gone freelance"). Kept narrow: "i left my ..." would catch "left my keys on the bus"
    "i've left the", "i've left my job", "i've quit", "i quit", "i've resigned", "i resigned",
    "i've joined", "i joined", "i've started at", "i started at", "i've stopped", "i stopped",
)


def looks_like_update(text: str) -> bool:
    """True if a turn deliberately updates/corrects an earlier fact — a code heuristic, no prompt."""
    t = " " + text.lower().strip() + " "
    return any(sig in t for sig in _UPDATE_SIGNALS)


def candidates_from_items(items: list[dict], users: list[Turn]) -> list[Candidate]:
    """Map the extractor's schema-validated items onto Candidates with real provenance.

    `supersedes` is computed here, deterministically from the turn text — not returned by the model.
    """
    out: list[Candidate] = []
    for item in items:
        idx = min(max(0, item["source_turn"]), len(users) - 1)
        src = users[idx]
        out.append(
            Candidate(
                gene=item["gene"],
                content=item["content"],
                gist=item.get("gist", "").strip(),
                tags=tuple(
                    dict.fromkeys(t.strip().lower() for t in item.get("tags", []) if t.strip())
                ),
                provenance=Provenance(
                    source_turn_ids=(src.id,),
                    source_texts=(src.text,),  # the raw words travel with the fact (spec §46)
                    created_at=src.created_at or time.time(),
                    stated=item["stated"],
                    confidence=item["confidence"],
                    stakes=item["stakes"],
                    supersedes=looks_like_update(src.text),  # deterministic, from the turn text
                ),
            )
        )
    return out


def _meter(meter, resp) -> None:
    if meter is not None:
        u = getattr(resp, "usage", None)
        if u is not None:
            meter.add((u.input_tokens or 0) + (u.output_tokens or 0))


class CloudJudge:
    """Pairwise verdicts from a strong model — forced-choice, low effort, tiny prompts."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter
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
        _meter(self.meter, resp)
        return bool(json.loads(_first_text(resp))[field])

    def better(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("a_is_better", better_prompt(gene, a, b))

    def same_claim(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_claim", same_claim_prompt(gene, a, b))

    def same_account(self, gene: str, a: Candidate, b: Candidate) -> bool:
        return self._verdict("same_account", same_account_prompt(gene, a, b))

    def conflicts(self, a: Candidate, b: Candidate) -> bool:
        return self._verdict("conflicts", conflicts_prompt(a, b))


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
                    "gist": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "stated": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "stakes": {"type": "string", "enum": ["low", "high"]},
                    "source_turn": {"type": "integer"},
                },
                "required": ["gene", "content", "gist", "tags", "stated", "confidence", "stakes", "source_turn"],
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
- A single message often holds SEVERAL distinct durable facts — extract every one as its own \
candidate, don't stop at the first or most obvious. ("I drive a Tesla and I'm allergic to nuts" \
is two facts.) But still skip the transient: a passing state ("my car needs oil") carries no \
lasting fact unless it implies one (that the user owns a car).
- `gene` is a short lowercase dotted key naming the SINGLE CLAIM the fact answers, stable across \
paraphrases: user.home_city, user.job.employer, user.daughter.name, user.pref.address_as. \
Different claims about one topic get different keys (user.food.loves vs user.food.allergy).
- The key names the QUESTION, never the answer: user.home_city, not user.home.berlin; \
user.residence.part_time, not user.residence.milton_keynes. The answer changes; the key must not.
- `content` is one self-contained sentence, understandable years later without the conversation.
- `gist`: the BARE ANSWER the gene's question resolves to, in as few words as possible — no \
sentence, no punctuation, lowercase (user.food.favorite_cuisine -> "japanese"; user.home_city -> \
"st leonards"; user.pet.species -> "greyhound"). The gene is the question; the gist is the value. \
A fast reader keys on this, so it must be unambiguous and must NOT restate the alternative it \
replaced ("japanese", never "japanese over thai").
- `tags`: 3-6 lowercase words someone would use when ASKING about this fact — the question's \
vocabulary, not the answer's ("Just picked up a new Tesla" -> ["car", "vehicle", "drive", "ev"]).
- `stated`: true if the user said it outright; false if you inferred it.
- `confidence`: how sure you are the fact is real and correctly read.
- `stakes`: "low" only for persona/style preferences (name to use, tone, format); "high" for \
facts about the user's life and world.
- `source_turn`: the [index] of the turn the fact came from."""


class CloudSlowModel:
    """Extraction via structured outputs — gene keys and provenance arrive as data, not prose."""

    def __init__(self, model: str = DEEP_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter
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
        _meter(self.meter, resp)
        items = json.loads(_first_text(resp))["candidates"]
        return candidates_from_items(items, users)


class CloudFastModel:
    """The phraser: a small, fast model that renders retrieved pages into words — nothing more."""

    def __init__(self, model: str = FAST_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter
        self.client = _client()

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=PHRASE_SYSTEM,
            messages=[{"role": "user", "content": phrase_user(question, pages, buffer)}],
        )
        _meter(self.meter, resp)
        return _first_text(resp).strip()

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
import re
import time

from .schema import ArcDraft, Candidate, Page, Provenance, Turn

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


# Deliberately TINY — the fast brain runs a weak model, and a long prompt is something for it to
# trip over (it will quote forbidden phrases back at you). The facts arrive pre-chewed as
# `key = value` (spec §48), so its job is near-lookup: find the value, say it. Conflict handling
# is NOT its job — the deep brain resolves conflicts by queueing questions (spec §36), so the
# notes it reads are already settled; giving a weak model a "say both versions" rule only makes
# it agonise (observed: it spiralled on a clean note). Keep this short.
PHRASE_SYSTEM = (
    "You are the user's personal assistant, answering them from a list of facts about them, each "
    "written `key = value`. The value is the answer. Speak DIRECTLY TO the user in the second person "
    "— say 'you' and 'your', never 'the user' or 'they' (the facts are written about them in the "
    "third person; rephrase to address the user). Reply with just the answer, in as few words as "
    "possible — no preamble, no restating the question. If no fact answers it, say: I don't know."
)


def _note(p: Page) -> str:
    # pure key = value for the fast brain (spec §48): nothing to misparse. The sentence stays in
    # the wiki for humans; the weak model reads only the bare triple. Fall back to the sentence
    # only when a fact has no gist.
    return f"- {p.gene} = {p.gist}" if p.gist else f"- {p.gene}: {p.content}"


SUMMARISE_SYSTEM = (
    "You consolidate several related facts about ONE aspect of a user into a single dense, "
    "self-contained sentence — preserving the key specifics (names, dates, preferences, what they "
    "tried/dropped). It must read well years later without the originals, and be rich in the words "
    "someone would use to ASK about this. Reply with ONLY the sentence — no preamble, no list."
)


def summarise_user(topic: str, facts: list[str]) -> str:
    joined = "\n".join(f"- {f}" for f in facts)
    return f"Topic: {topic}\nFacts to consolidate into one sentence:\n{joined}"


ARC_SYSTEM = (
    "You are given several facts about ONE user, in time order — one aspect of them that CHANGED "
    "over time. Narrate the change as a short arc for a memory the user can read back:\n"
    "- `gist`: one line, 'initially X → now Y' (add ', because Z' ONLY if a fact states the reason).\n"
    "- `beats`: the ordered moments, each a short clause, dated where a fact gives a date, carrying "
    "the reason ONLY IF the facts state one.\n"
    "NEVER invent a reason the facts don't give — if no 'why' is stated, say WHAT changed without a "
    "'because'. Use only what the facts contain. Reply with ONLY JSON matching the schema."
)

ARC_SCHEMA = {
    "type": "object",
    "properties": {
        "gist": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["gist", "beats"],
    "additionalProperties": False,
}


def arc_user(facts: list[str]) -> str:
    joined = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    return f"Facts about the user, in time order (oldest first):\n{joined}"


def phrase_user(question: str, pages: list[Page], buffer: list[Turn]) -> str:
    notes = "\n".join(_note(p) for p in pages)
    recent = "\n".join(f"{t.speaker}: {t.text}" for t in buffer[-6:])
    return (
        f"Retrieved notes:\n{notes or '- (none)'}\n\n"
        f"Recent conversation:\n{recent}\n\nQuestion: {question}"
    )


# Some questions aren't "say the value" — they're "pick the option that fits" (PersonaMem's whole
# task is response-SELECTION). The phrase prompt is wrong for that: the weak model starts reasoning
# in prose and gets cut off before it ever names a choice. Detect the lettered-option shape and
# switch to a selection prompt. Still grounded (spec, the one principle): retrieval decided WHAT is
# known; the model only maps those facts onto the option that matches — it doesn't decide truth.
_MC_OPTION_RE = re.compile(r"(?m)^\s*\(([a-z])\)\s")


def is_multiple_choice(question: str) -> bool:
    """True when the question offers ≥2 lettered options — an (a)/(b)/(c) selection, not a lookup."""
    return len(_MC_OPTION_RE.findall(question)) >= 2


SELECT_SYSTEM = (
    "You are given facts about the user (each written `key = value`) and a question with lettered "
    "options. Pick the ONE option most consistent with those facts. "
    "Reply with ONLY that single letter — no words, no reasoning, no explanation. "
    "If the facts don't settle it, still pick the option that best fits what is known about the user."
)


def select_user(question: str, pages: list[Page], buffer: list[Turn]) -> str:
    notes = "\n".join(_note(p) for p in pages)
    return (
        f"Facts I remember about the user:\n{notes or '- (none)'}\n\n"
        f"{question}\n\nAnswer with only the letter."
    )


def answer_prompt(question: str, pages: list[Page], buffer: list[Turn]) -> tuple[str, str]:
    """(system, user) for the fast brain: a selection prompt for MC questions, else the phraser."""
    if is_multiple_choice(question):
        return SELECT_SYSTEM, select_user(question, pages, buffer)
    return PHRASE_SYSTEM, phrase_user(question, pages, buffer)


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
- `content` is one self-contained sentence addressed to the user in the SECOND PERSON — "You drive \
a Tesla", "You started a music podcast in 2018" (not "The user drives…") — understandable years \
later without the conversation. The wiki is notes written TO the user.
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

    def summarise(self, topic: str, facts: list[str]) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=400, system=SUMMARISE_SYSTEM,
            messages=[{"role": "user", "content": summarise_user(topic, facts)}],
        )
        _meter(self.meter, resp)
        return _first_text(resp).strip()

    def arc(self, facts: list[str]) -> ArcDraft:
        resp = self.client.messages.create(
            model=self.model, max_tokens=800,
            output_config={"format": {"type": "json_schema", "schema": ARC_SCHEMA}},
            system=ARC_SYSTEM,
            messages=[{"role": "user", "content": arc_user(facts)}],
        )
        _meter(self.meter, resp)
        data = json.loads(_first_text(resp))
        return ArcDraft(gist=data.get("gist", ""), beats=list(data.get("beats", [])))


class CloudFastModel:
    """The phraser: a small, fast model that renders retrieved pages into words — nothing more."""

    def __init__(self, model: str = FAST_MODEL, meter=None) -> None:
        self.model = model
        self.meter = meter
        self.client = _client()

    def answer(self, question: str, pages: list[Page], buffer: list[Turn]) -> str:
        system, user = answer_prompt(question, pages, buffer)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=16 if is_multiple_choice(question) else 300,  # MC wants a bare letter
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        _meter(self.meter, resp)
        return _first_text(resp).strip()

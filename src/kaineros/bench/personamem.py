"""PersonaMem adapter (github.com/bowen-upenn/PersonaMem, MIT): replay persona sessions into a
Session, probe with the benchmark's multiple-choice questions, score by letter/text match.

The whole adapter is data transformation — no new model plumbing. Each MC question becomes an
ordinary probe STRING through runtime.respond(): retrieval routes pages exactly as in chat, the
fast model phrases, and we string-match the pick. The grounding rule survives untouched.

Two sources:
  load_sample()         - the small checked-in fixture (evals/personamem-sample.toml); offline,
                          deterministic, what the tests run on the fakes.
  load_dataset_slice()  - a slice of the real dataset (evals/personamem/, git-ignored, downloaded
                          from HuggingFace). Best-effort on the published schema; the first real
                          run may need a column-name adjustment.

Context note: the real benchmark asks questions in-situ (mid-timeline). This pilot adapter
compiles the whole slice then probes — same information reaching the system, minus ordering
subtleties; good enough to decide whether the full run is worth the compute.
"""
from __future__ import annotations

import csv
import json
import re as _re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import Turn

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
SAMPLE = EVALS_DIR / "personamem-sample.toml"
SAMPLE_BIG = EVALS_DIR / "personamem-sample-big.toml"
DATA_DIR = EVALS_DIR / "personamem"
LETTERS = "abcdefgh"


@dataclass
class Probe:
    qid: str
    qtype: str
    question: str
    options: list[str]
    answer: str                    # the correct option's text
    after_session: int             # ask once sessions [0..after_session] are compiled

    @property
    def letter(self) -> str:
        return LETTERS[self.options.index(self.answer)]

    @property
    def text(self) -> str:
        opts = "\n".join(f"({LETTERS[i]}) {o}" for i, o in enumerate(self.options))
        return f"{self.question}\n{opts}\nAnswer with the letter of the best option."


@dataclass
class Slice:
    name: str
    sessions: list[list[str]]      # user turn texts, per session
    probes: list[Probe] = field(default_factory=list)


def load_sample(path: Path = SAMPLE) -> Slice:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    sessions = [s["turns"] for s in data["session"]]
    probes = [
        Probe(
            qid=f"sample-{i}",
            qtype=p.get("type", "recall"),
            question=p["question"],
            options=p["options"],
            answer=p["answer"],
            after_session=p.get("after_session", len(sessions) - 1),
        )
        for i, p in enumerate(data["probe"])
    ]
    return Slice(name=data.get("name", "personamem-sample"), sessions=sessions, probes=probes)


def _strip_letter(s: str) -> str:
    """'(a) It's great to see…' -> 'It's great to see…'."""
    return _re.sub(r"^\s*\(?[a-hA-H]\)?[\.\):]?\s*", "", str(s)).strip()


def _parse_options(raw: str) -> list[str]:
    """all_options is a stringified list, each item lettered ('(a) text'). Return clean texts."""
    val = None
    for parse in (json.loads, __import__("ast").literal_eval):
        try:
            got = parse(raw)
            if isinstance(got, list) and got:
                val = got
                break
        except (ValueError, SyntaxError):
            continue
    if val is None:
        val = [p for p in raw.replace("\r", "").split("\n") if p.strip()]
    opts = [_strip_letter(v) for v in val]
    if not opts:
        raise RuntimeError(f"couldn't parse all_options: {raw[:120]!r}")
    return opts


def _answer_text(correct: str, options: list[str]) -> str:
    """correct_answer is a letter, usually '(c)'. Map it to the option text."""
    m = _re.match(r"\s*\(?([a-hA-H])\)?", correct or "")
    if m:
        i = LETTERS.index(m.group(1).lower())
        if i < len(options):
            return options[i]
    stripped = _strip_letter(correct)
    if stripped in options:  # some dialects store the text
        return stripped
    raise RuntimeError(f"can't resolve correct_answer {correct!r} against {len(options)} options")


def _user_turns(messages: list, end_index: int | None) -> list[str]:
    """The user's turns from a context's message list (up to end_index if given), 'User: ' stripped."""
    msgs = messages[: end_index + 1] if end_index is not None else messages
    turns = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
            turns.append(_re.sub(r"^\s*User:\s*", "", str(m["content"])).strip())
    return [t for t in turns if t]


def _load_context(ctx_path: Path, ctx_id: str) -> list | None:
    """Scan the JSONL (one `{context_id: [messages]}` per line) for the target context's messages."""
    with open(ctx_path, encoding="utf-8") as f:
        for line in f:
            if ctx_id not in line:  # cheap prefilter before the JSON parse
                continue
            rec = json.loads(line)
            if isinstance(rec, dict) and ctx_id in rec:
                return rec[ctx_id]
    return None


def load_dataset_slice(
    size: str = "32k", persona: str | None = None, limit: int = 10,
    max_sessions: int | None = None, data_dir: Path = DATA_DIR
) -> Slice:
    """A slice of the real PersonaMem dataset. `max_sessions` caps the compiled context (for a
    quick smoke run that shakes out the loader before a full 10-20 min persona)."""
    qcsv = data_dir / f"questions_{size}.csv"
    ctx = data_dir / f"shared_contexts_{size}.jsonl"
    if not qcsv.exists() or not ctx.exists():
        raise RuntimeError(
            f"PersonaMem data not found under {data_dir}.\n"
            "  download it (MIT-licensed) with:\n"
            "    pip install huggingface_hub\n"
            "    hf download bowen-upenn/PersonaMem --repo-type dataset "
            f"--local-dir {data_dir}\n"
            f"  expected files: {qcsv.name}, {ctx.name}"
        )
    with open(qcsv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError(f"{qcsv} parsed empty")
    pid = str(persona) if persona is not None else str(rows[0].get("persona_id"))
    mine = [r for r in rows if str(r.get("persona_id")) == pid]
    if not mine:
        raise RuntimeError(f"no questions for persona_id={pid!r}")
    # a persona may span several shared contexts — take the one with the most questions
    from collections import Counter

    ctx_id = Counter(r["shared_context_id"] for r in mine).most_common(1)[0][0]
    mine = [r for r in mine if r["shared_context_id"] == ctx_id][:limit]

    messages = _load_context(ctx, ctx_id)
    if messages is None:
        raise RuntimeError(f"context {ctx_id[:12]}... not found in {ctx.name}")

    def _end(r) -> int:
        try:
            return int(r.get("end_index_in_shared_context"))
        except (TypeError, ValueError):
            return len(messages) - 1

    # compile the context up to the furthest question's in-situ position; smoke caps by sessions
    turns = _user_turns(messages, max(_end(r) for r in mine))
    sessions = [turns[i : i + 10] for i in range(0, len(turns), 10)]
    if max_sessions is not None:
        sessions = sessions[:max_sessions]

    probes = []
    for i, r in enumerate(mine):
        options = _parse_options(r["all_options"])
        probes.append(
            Probe(
                qid=str(r.get("question_id", f"q{i}")),
                qtype=r.get("question_type", "unknown"),
                question=r["user_question_or_message"],
                options=options,
                answer=_answer_text(r["correct_answer"], options),
                after_session=len(sessions) - 1,
            )
        )
    return Slice(name=f"personamem-{size}-p{pid}", sessions=sessions, probes=probes)


def score_answer(answer: str, probe: Probe) -> bool:
    """Correct if the reply picks the right letter or names the right option.

    Tolerant of the system's own honesty: the runtime's hedge prefix ("If I remember
    rightly: b") and quoted/parenthesised letters ('"a"', '(b)', 'B).') all score on the
    letter they pick — hedging is a confidence statement, not a wrong answer.
    """
    ans = answer.lower().strip()
    if not ans:
        return False
    from ..runtime import HEDGE_PREFIX

    hedge = HEDGE_PREFIX.lower().strip()
    if ans.startswith(hedge):
        ans = ans[len(hedge):].lstrip(" :").strip()
    # the whole reply is one (possibly wrapped) letter: b / "a" / (c) / B).
    m = _re.match(r'^[\s"\'`(\[]*([a-h])[\s"\'`)\].:,]*$', ans)
    if m:
        return m.group(1) == probe.letter
    # a letter followed by a delimiter then text: "b) green tea" — but NOT "a glass of milk"
    m = _re.match(r'^[\s"\'`(\[]*([a-h])[")\].:,]', ans)
    if m and m.group(1) == probe.letter:
        return True
    if probe.answer.lower() in ans:
        # naming the right option only counts if it doesn't also name a wrong one
        others = [o for o in probe.options if o != probe.answer]
        return not any(o.lower() in ans for o in others)
    return False


class _CountingJudge:
    """Transparent tally around whatever judge the session runs — how often does the deep brain
    actually get asked, and what kind of question? Pure delegation; verdicts untouched."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.counts = {"better": 0, "same_claim": 0, "same_account": 0, "conflicts": 0}

    def better(self, gene, a, b):
        self.counts["better"] += 1
        return self.inner.better(gene, a, b)

    def same_claim(self, gene, a, b):
        self.counts["same_claim"] += 1
        return self.inner.same_claim(gene, a, b)

    def same_account(self, gene, a, b):
        self.counts["same_account"] += 1
        return self.inner.same_account(gene, a, b)

    def conflicts(self, a, b):
        self.counts["conflicts"] += 1
        return self.inner.conflicts(a, b)


def run_slice(session, sl: Slice, say=lambda s: None, wait_idle=None) -> tuple[list[dict], dict]:
    """Feed sessions, settle, probe. Returns (rows, population_stats). UI-free: `say` narrates,
    `wait_idle` drains a background worker (the CLI passes its progress-printing drain)."""
    by_pos: dict[int, list[Probe]] = {}
    for p in sl.probes:
        by_pos.setdefault(min(p.after_session, len(sl.sessions) - 1), []).append(p)

    total = len(sl.sessions) + len(sl.probes)  # progress units: each session, each probe
    done = 0

    def pct() -> str:
        return f"[{done}/{total} {100 * done // total}%]"

    counting = _CountingJudge(session.compiler.judge)
    session.compiler.judge = counting
    inserted = 0
    rows: list[dict] = []
    for i, turns in enumerate(sl.sessions):
        say(f"{pct()} session {i + 1}/{len(sl.sessions)}: compiling {len(turns)} turn(s) "
            "(extract + judge - slow on a real deep model)...")
        d0, t0 = session.deep_meter.total, time.time()
        for text in turns:
            session.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        session.consolidate()
        if wait_idle is not None:
            wait_idle()
        inserted += getattr(session.compiler.last_report, "inserted", 0)
        done += 1
        say(f"{pct()} session {i + 1} compiled in {time.time() - t0:.0f}s "
            f"({session.deep_meter.total - d0:,} deep tok, {len(session.store.pages())} page(s))")
        if i not in by_pos:
            continue
        # let settled winners earn their pages (rank-only — cheap), then ONE cleanup pass to
        # settle cross-page work. Doing full cleanup on every one of these fired 3x the judge
        # calls on a big mind for no benefit (the pairs are unchanged between them).
        for _ in range(session.compiler.promote_after):
            session.compiler.housekeep(cleanup=False)
        session.compiler.housekeep(cleanup=True)
        for probe in by_pos[i]:
            say(f"{pct()} probe: {probe.question[:60]}")
            d0, f0 = session.deep_meter.total, session.fast_meter.total
            t0 = time.time()
            resp = session.runtime.respond(probe.text, [])  # empty buffer: memory, not context
            done += 1
            rows.append(
                {
                    "qid": probe.qid,
                    "type": probe.qtype,
                    "expected": f"({probe.letter}) {probe.answer}",
                    "answer": resp.answer,
                    "correct": score_answer(resp.answer, probe) and not resp.abstained,
                    "abstained": resp.abstained,
                    "used": [p.gene for p in resp.used],  # which pages the lookup actually read
                    "deep_tok": session.deep_meter.total - d0,
                    "fast_tok": session.fast_meter.total - f0,
                    "seconds": round(time.time() - t0, 2),
                }
            )
    session.compiler.judge = counting.inner  # unwrap — leave the session as we found it
    pairwise = sum(counting.counts.values())
    candidates = sum(len(p) for p in session.store.pool.values())
    stats = {
        "genes": len(session.store.pool),
        "candidates": candidates,
        "pages": len(session.store.pages()),
        "inserted": inserted,
        "judge_calls": dict(counting.counts, total=pairwise),
        "rank_per_element": round(pairwise / inserted, 1) if inserted else None,
    }
    return rows, stats


def summarise(rows: list[dict], stats: dict | None = None) -> list[str]:
    n = len(rows)
    if not n:
        return ["no probes ran"]
    right = sum(r["correct"] for r in rows)
    abstained = sum(r["abstained"] for r in rows)
    lines = [f"accuracy {right}/{n} ({right / n:.2f})   abstained {abstained}"]
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    for t in sorted(by_type):
        rs = by_type[t]
        ok = sum(r["correct"] for r in rs)
        lines.append(f"  {t}: {ok}/{len(rs)}")
    if stats:
        jc = stats["judge_calls"]
        lines.append(
            f"population: {stats['inserted']} extracted -> {stats['candidates']} candidate(s) "
            f"across {stats['genes']} gene pool(s) -> {stats['pages']} page(s) promoted"
        )
        lines.append(
            f"judge calls: {jc['total']} (better {jc['better']}, same_claim {jc['same_claim']}, "
            f"same_account {jc['same_account']}, conflicts {jc['conflicts']})"
            + (f" - avg {stats['rank_per_element']} rankings per element" if stats["rank_per_element"] is not None else "")
        )
    lines.append("(PersonaMem's published frontier-model ceiling is ~0.52 on the full set)")
    return lines

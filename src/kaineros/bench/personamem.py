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
import random
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
V2_DIR = EVALS_DIR / "personamem-v2"  # the modern (Dec 2025) benchmark — different schema, see below
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


def persona_ids(size: str = "32k", n: int | None = None, data_dir: Path = DATA_DIR) -> list[str]:
    """The distinct persona_ids in the question set, in file order (first `n` if given). Lets the
    bench run several personas and aggregate — one persona is too few questions to mean anything."""
    qcsv = data_dir / f"questions_{size}.csv"
    if not qcsv.exists():
        raise RuntimeError(f"PersonaMem data not found: {qcsv} (see load_dataset_slice for download)")
    seen: list[str] = []
    with open(qcsv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = str(r.get("persona_id"))
            if pid and pid not in seen:
                seen.append(pid)
    return seen[:n] if n is not None else seen


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


# --- PersonaMem-v2 (Dec 2025) ----------------------------------------------------------------------
# A different schema from v1: options aren't pre-lettered — each row carries `correct_answer` (text)
# plus `incorrect_answers` (a JSON list of texts), and the context is a PER-PERSONA chat-history JSON
# (`chat_history_32k_link`) of full user+assistant dialogue, not a shared-context id. `updated`/
# `prev_pref` mark preference changes; `pref_type` categorises. We ingest the USER turns (parity with
# v1 — the only variable vs the baseline stays raw-context-vs-compiled-memory), build a 4-way MC from
# correct+incorrect (deterministically shuffled to avoid position bias), and reuse Probe/score_answer.
def _v2_content(raw: str) -> str:
    """`user_query` is a python-dict repr {'role':'user','content':'...'} — pull the content."""
    import ast

    for parse in (ast.literal_eval, json.loads):
        try:
            got = parse(raw)
            if isinstance(got, dict):
                return str(got.get("content", "")).strip()
        except (ValueError, SyntaxError):
            continue
    m = _re.search(r"['\"]content['\"]\s*:\s*['\"](.*)['\"]\s*}\s*$", str(raw), _re.S)
    return (m.group(1) if m else str(raw)).strip()


def _v2_list(raw: str) -> list[str]:
    """`incorrect_answers` is a stringified list of answer texts."""
    import ast

    for parse in (json.loads, ast.literal_eval):
        try:
            got = parse(raw)
            if isinstance(got, list):
                return [str(x).strip() for x in got if str(x).strip()]
        except (ValueError, SyntaxError):
            continue
    return []


def v2_persona_ids(n: int | None = None, data_dir: Path = V2_DIR) -> list[str]:
    qcsv = data_dir / "benchmark" / "text" / "benchmark.csv"
    if not qcsv.exists():
        raise RuntimeError(f"PersonaMem-v2 not found: {qcsv} (see load_v2_slice for the download)")
    seen: list[str] = []
    with open(qcsv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = str(r.get("persona_id"))
            if pid and pid not in seen:
                seen.append(pid)
    return seen[:n] if n is not None else seen


def load_v2_slice(persona: str | None = None, limit: int = 30,
                  max_sessions: int | None = None, data_dir: Path = V2_DIR) -> Slice:
    """A slice of the modern PersonaMem-v2 (32k tier, text). Same `Slice` shape as v1, so run_slice /
    run_baseline work unchanged."""
    qcsv = data_dir / "benchmark" / "text" / "benchmark.csv"
    if not qcsv.exists():
        raise RuntimeError(
            f"PersonaMem-v2 questions not found under {data_dir}.\n"
            "  download the text benchmark + the 32k chat histories you need, e.g.:\n"
            "    hf download bowen-upenn/PersonaMem-v2 --repo-type dataset "
            f"--local-dir {data_dir} --include 'benchmark/text/benchmark.csv' "
            "--include 'data/chat_history_32k/*'"
        )
    rows = list(csv.DictReader(open(qcsv, newline="", encoding="utf-8")))
    pid = str(persona) if persona is not None else str(rows[0].get("persona_id"))
    mine = [r for r in rows if str(r.get("persona_id")) == pid][:limit]
    if not mine:
        raise RuntimeError(f"no v2 questions for persona_id={pid!r}")

    hist = data_dir / mine[0]["chat_history_32k_link"]
    if not hist.exists():
        raise RuntimeError(
            f"chat history not downloaded: {hist}\n  fetch it with:\n"
            f"    hf download bowen-upenn/PersonaMem-v2 --repo-type dataset --local-dir {data_dir} "
            f"--include '{mine[0]['chat_history_32k_link']}'"
        )
    chat = json.load(open(hist, encoding="utf-8")).get("chat_history", [])
    turns = [str(m["content"]).strip() for m in chat
             if isinstance(m, dict) and m.get("role") == "user" and m.get("content")]
    sessions = [turns[i : i + 10] for i in range(0, len(turns), 10)]
    if max_sessions is not None:
        sessions = sessions[:max_sessions]

    probes = []
    for i, r in enumerate(mine):
        correct = str(r["correct_answer"]).strip()
        options = [correct] + _v2_list(r["incorrect_answers"])
        random.Random(f"{pid}-{i}").shuffle(options)  # deterministic, kills position bias
        updated = str(r.get("updated", "")).strip().lower() == "true"
        probes.append(
            Probe(
                qid=str(r.get("question_id", f"{pid}-q{i}")),
                # group the preference-UPDATE questions (v2's evolution analog) under one type
                qtype="updated_preference" if updated else str(r.get("pref_type", "unknown")),
                question=_v2_content(r["user_query"]),
                options=options,
                answer=correct,
                after_session=len(sessions) - 1,
            )
        )
    return Slice(name=f"personamem-v2-32k-p{pid}", sessions=sessions, probes=probes)


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


def run_slice(
    session, sl: Slice, say=lambda s: None, wait_idle=None, think_seconds: float = 60.0,
    skip_ingest: bool = False, checkpoint_dir: str | None = None
) -> tuple[list[dict], dict]:
    """Feed sessions, settle, probe. Returns (rows, population_stats). UI-free: `say` narrates,
    `wait_idle` drains a background worker (the CLI passes its progress-printing drain).

    Ingest is cheap (spec §49) — the judge doesn't run at insert, and no cross-page grooming.
    Then the deep brain GROOMS for a wall-clock budget (`think_seconds`) before probing: it runs
    cleanup passes until the time is up OR the mind converges (a pass makes no new comparisons).
    This models 'how long has the deep brain had to think' — a minute for a quick run, or make it
    long to simulate an overnight think. More thinking time = a cleaner wiki = better answers."""
    total = len(sl.sessions) + len(sl.probes)  # progress units: each session, each probe
    done = 0

    def pct() -> str:
        return f"[{done}/{total} {100 * done // total}%]"

    counting = _CountingJudge(session.compiler.judge)
    session.compiler.judge = counting
    # ingest keeps the CHEAP rank+promote (bounded by new facts) but NOT the expensive cross-page
    # cleanup — push the cadence out of reach so cleanup only happens in the explicit groom phase.
    session.cleanup_every = 10**9
    inserted = 0
    rows: list[dict] = []
    t_ingest = t_groom = t_probe = 0.0

    # PHASE 1 — INGEST (extract + append), or boot from a saved checkpoint (spec §49). Ingest is
    # the deterministic, expensive part; checkpointing it lets you iterate on grooming fast.
    if skip_ingest:
        done += len(sl.sessions)
        say(f"{pct()} booted from cached ingest — extraction skipped "
            f"({len(session.store.pages())} pages)")
    else:
        t0 = time.time()
        for i, turns in enumerate(sl.sessions):
            say(f"{pct()} session {i + 1}/{len(sl.sessions)}: ingesting {len(turns)} turn(s)...")
            d0 = session.deep_meter.total
            for text in turns:
                session.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
            session.consolidate()  # extract + append + cheap rank/promote, no cross-page cleanup
            if wait_idle is not None:
                wait_idle()
            inserted += getattr(session.compiler.last_report, "inserted", 0)
            done += 1
            say(f"{pct()} session {i + 1} ingested ({session.deep_meter.total - d0:,} deep tok, "
                f"{len(session.store.pages())} pages)")
        t_ingest = time.time() - t0
        if checkpoint_dir is not None:
            from ..persist import save_store

            save_store(session.store, checkpoint_dir)
            say(f"{pct()} ingest checkpoint saved — rerun with `ingest=cached` to skip extraction")

    # PHASE 2 — GROOM: the deep brain THINKS for a wall-clock budget, running cleanup passes until
    # the time is up OR the mind SETTLES. Settled = two passes that CHANGED nothing (no fusion,
    # curation, promotion, or consolidation). We converge on changes, not on judge-CHECKS: a
    # random-pair background sweep keeps sampling new pairs every pass, so a check-count never hits
    # zero on a settled mind — the old convergence spun the full budget for no effect.
    say(f"{pct()} deep brain thinking (grooming) for up to {think_seconds:.0f}s...")
    t0 = time.time()
    deadline = t0 + think_seconds
    gp = 0
    idle = 0  # consecutive passes with zero changes
    while time.time() < deadline:
        before = sum(counting.counts.values())
        session.compiler.housekeep(cleanup=True)
        gp += 1
        changes = session.compiler.last_report.actions  # mutations to the clean layer this pass
        new = sum(counting.counts.values()) - before     # judge calls (progress only)
        left = max(0, int(deadline - time.time()))
        say(f"{pct()} ...thought {gp} pass(es), {new} checks, {changes} changes, "
            f"{left}s left, {len(session.store.pages())} pages")
        idle = idle + 1 if changes == 0 else 0
        if idle >= 2:  # nothing changed for two passes running -> settled, stop wasting the budget
            say(f"{pct()} deep brain settled (no changes for {idle} passes, after {gp} total)")
            break
    t_groom = time.time() - t0

    # PHASE 3 — PROBE (against the groomed mind)
    t0 = time.time()
    for probe in sl.probes:
        say(f"{pct()} probe: {probe.question[:60]}")
        d0, f0 = session.deep_meter.total, session.fast_meter.total
        pt0 = time.time()
        resp = session.runtime.respond(probe.text, [])  # empty buffer: memory, not context
        done += 1
        rows.append({
            "qid": probe.qid, "type": probe.qtype,
            "expected": f"({probe.letter}) {probe.answer}", "answer": resp.answer,
            "correct": score_answer(resp.answer, probe) and not resp.abstained,
            "abstained": resp.abstained, "used": [p.gene for p in resp.used],
            "deep_tok": session.deep_meter.total - d0, "fast_tok": session.fast_meter.total - f0,
            "seconds": round(time.time() - pt0, 2),
        })
    t_probe = time.time() - t0

    session.compiler.judge = counting.inner  # unwrap — leave the session as we found it
    pairwise = sum(counting.counts.values())
    stats = {
        "genes": len(session.store.pool),
        "candidates": sum(len(p) for p in session.store.pool.values()),
        "pages": len(session.store.pages()),
        "inserted": inserted,
        "judge_calls": dict(counting.counts, total=pairwise),
        "rank_per_element": round(pairwise / inserted, 1) if inserted else None,
        "phase_seconds": {"ingest": round(t_ingest), "groom": round(t_groom), "probe": round(t_probe)},
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


# --- single-model baseline -----------------------------------------------------------------------
# The comparison point for the two-speed system: ONE frontier model reads the whole raw history and
# answers each question directly — no compiled memory, no retrieval. Same probes and scorer as
# run_slice, so Kaineros-vs-frontier is apples-to-apples on identical questions.
BASELINE_SYSTEM = (
    "You are a personalized assistant with a perfect record of your past conversations with one "
    "user. You are given everything the user has told you, in order, then a question with lettered "
    "options. Choose the ONE option that best fits everything the user has revealed about "
    "themselves over time. Reply with ONLY that single letter — no words, no explanation."
)


def baseline_user(history: str, question: str) -> str:
    return (f"Everything the user has told you, in order:\n{history}\n\n"
            f"{question}\n\nAnswer with only the letter.")


def run_baseline(slices: list[Slice], ask, say=lambda s: None) -> list[dict]:
    """Feed each probe the FULL raw history + question to a single model (`ask(system, user) -> str`)
    reading the context directly. Rows match run_slice's shape so `summarise` scores them the same."""
    rows: list[dict] = []
    total = sum(len(sl.probes) for sl in slices)
    done = 0
    for sl in slices:
        # the same source material the two-speed system ingested (user turns), so the only variable
        # is raw-context vs compiled-memory — not what information each side got to see
        history = "\n".join(t for sess in sl.sessions for t in sess)
        for probe in sl.probes:
            done += 1
            say(f"[{done}/{total}] {sl.name}: {probe.question[:50]}")
            t0 = time.time()
            ans = ask(BASELINE_SYSTEM, baseline_user(history, probe.text))
            rows.append({
                "qid": probe.qid, "type": probe.qtype,
                "expected": f"({probe.letter}) {probe.answer}", "answer": ans,
                "correct": score_answer(ans, probe), "abstained": False, "used": [],
                "deep_tok": 0, "fast_tok": 0, "seconds": round(time.time() - t0, 2),
                "persona": sl.name,
            })
    return rows

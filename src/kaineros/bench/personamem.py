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
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..schema import Turn

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
SAMPLE = EVALS_DIR / "personamem-sample.toml"
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


def _parse_options(raw: str) -> list[str]:
    """all_options arrives as a stringified list; be tolerant about the exact dialect."""
    for parse in (json.loads, __import__("ast").literal_eval):
        try:
            val = parse(raw)
            if isinstance(val, list) and val:
                return [str(v) for v in val]
        except (ValueError, SyntaxError):
            continue
    parts = [p.strip() for p in raw.replace("\r", "").split("\n") if p.strip()]
    if len(parts) > 1:
        return parts
    raise RuntimeError(f"couldn't parse all_options: {raw[:120]!r}")


def _sessions_from_context(obj) -> list[list[str]]:
    """Pull the user's turns out of one shared-context record, tolerantly."""
    msgs = obj.get("messages") if isinstance(obj, dict) else obj
    if not isinstance(msgs, list):
        raise RuntimeError(f"unrecognised context record shape: {type(obj).__name__}")
    turns = [
        str(m.get("content", ""))
        for m in msgs
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content")
    ]
    if not turns:
        raise RuntimeError("context record held no user messages - schema drift, adjust adapter")
    # no explicit session markers in the flat record: chunk into pseudo-sessions so the compiler
    # gets its natural "gone quiet" boundaries rather than one giant block
    size = 10
    return [turns[i : i + size] for i in range(0, len(turns), size)]


def load_dataset_slice(
    size: str = "32k", persona: str | None = None, limit: int = 10, data_dir: Path = DATA_DIR
) -> Slice:
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
    pid = persona if persona is not None else rows[0].get("persona_id")
    mine = [r for r in rows if r.get("persona_id") == pid][:limit]
    if not mine:
        raise RuntimeError(f"no questions for persona_id={pid!r}")

    contexts = [json.loads(line) for line in open(ctx, encoding="utf-8") if line.strip()]
    record = None
    for c in contexts:
        if isinstance(c, dict) and str(c.get("persona_id", "")) == str(pid):
            record = c
            break
    if record is None and contexts:
        record = contexts[0]  # single-record files / schema drift: take what there is
    sessions = _sessions_from_context(record)

    probes = []
    for i, r in enumerate(mine):
        options = _parse_options(r["all_options"])
        answer = r["correct_answer"].strip()
        if answer not in options:  # some dialects store the letter, not the text
            low = answer.lower().strip("().")
            if low in LETTERS[: len(options)]:
                answer = options[LETTERS.index(low)]
            else:
                raise RuntimeError(f"correct_answer {answer!r} not among options for q{i}")
        probes.append(
            Probe(
                qid=r.get("question_id", f"q{i}"),
                qtype=r.get("question_type", "unknown"),
                question=r["user_question"],
                options=options,
                answer=answer,
                after_session=len(sessions) - 1,
            )
        )
    return Slice(name=f"personamem-{size}-{pid}", sessions=sessions, probes=probes)


def score_answer(answer: str, probe: Probe) -> bool:
    """Correct if the reply picks the right letter or names the right option."""
    ans = answer.lower().strip()
    if not ans:
        return False
    lead = ans.lstrip("(").split(")")[0].split(".")[0].split(":")[0].strip()
    if lead == probe.letter:
        return True
    if probe.answer.lower() in ans:
        # naming the right option only counts if it doesn't also name a wrong one
        others = [o for o in probe.options if o != probe.answer]
        return not any(o.lower() in ans for o in others)
    return False


def run_slice(session, sl: Slice, say=lambda s: None, wait_idle=None) -> list[dict]:
    """Feed sessions, settle, probe. UI-free: `say` narrates, `wait_idle` drains a background
    worker (the CLI passes its progress-printing drain; sync sessions need neither)."""
    by_pos: dict[int, list[Probe]] = {}
    for p in sl.probes:
        by_pos.setdefault(min(p.after_session, len(sl.sessions) - 1), []).append(p)

    rows: list[dict] = []
    for i, turns in enumerate(sl.sessions):
        say(f"session {i + 1}/{len(sl.sessions)}: {len(turns)} turn(s)")
        for text in turns:
            session.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        session.consolidate()
        if wait_idle is not None:
            wait_idle()
        if i not in by_pos:
            continue
        for _ in range(session.compiler.promote_after):  # let settled winners earn their pages
            session.compiler.housekeep()
        for probe in by_pos[i]:
            d0, f0 = session.deep_meter.total, session.fast_meter.total
            t0 = time.time()
            resp = session.runtime.respond(probe.text, [])  # empty buffer: memory, not context
            rows.append(
                {
                    "qid": probe.qid,
                    "type": probe.qtype,
                    "expected": f"({probe.letter}) {probe.answer}",
                    "answer": resp.answer,
                    "correct": score_answer(resp.answer, probe) and not resp.abstained,
                    "abstained": resp.abstained,
                    "deep_tok": session.deep_meter.total - d0,
                    "fast_tok": session.fast_meter.total - f0,
                    "seconds": round(time.time() - t0, 2),
                }
            )
    return rows


def summarise(rows: list[dict]) -> list[str]:
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
    lines.append("(PersonaMem's published frontier-model ceiling is ~0.52 on the full set)")
    return lines

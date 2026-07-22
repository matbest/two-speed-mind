"""Eval harness (Slice 5, T9a): grade what the mind ends up knowing.

Feeds the example conversations in evals/conversations.toml to a session and checks, scenario by
scenario, that the expected knowledge (a) got promoted into the clean layer and (b) comes back
when probed with a question. Grading is graded, not exact — real models phrase freely.

Usage:
    python -m kaineros.evals            # cloud models (spends tokens; needs the [cloud] extra)
    python -m kaineros.evals --fakes    # free smoke run on the fakes (expect failures)
    python -m kaineros.evals --only berlin   # scenarios whose name contains "berlin"
    python -m kaineros.evals --corpus conflicts --metrics   # scorecard: AA / CRS / SEH / lag / cost

Not part of pytest — pytest stays free and deterministic on the fakes.
"""
from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path

from .cli import Session
from .schema import Turn

CORPUS_DIR = Path(__file__).resolve().parents[2] / "evals"
CORPUS = CORPUS_DIR / "conversations.toml"


def load_corpus(path: Path = CORPUS) -> list[dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)["scenario"]


def corpus_path(name: str) -> Path:
    return CORPUS_DIR / f"{name}.toml"


def build_session(fakes: bool, openrouter: bool = False, free: bool = False) -> Session:
    if fakes:
        return Session()
    if openrouter or free:
        from .openrouter import (
            DEEP_MODEL,
            FAST_MODEL,
            FREE_DEEP_MODEL,
            FREE_FAST_MODEL,
            OpenRouterFastModel,
            OpenRouterJudge,
            OpenRouterSlowModel,
            ensure_key,
            preflight,
        )

        deep = FREE_DEEP_MODEL if free else DEEP_MODEL
        fast = FREE_FAST_MODEL if free else FAST_MODEL
        ensure_key()
        preflight(fast)
        session = Session()  # metered adapters so --metrics can report token cost
        session.compiler.judge = OpenRouterJudge(deep, meter=session.deep_meter)
        session.slow = OpenRouterSlowModel(deep, meter=session.deep_meter)
        session.runtime.model = OpenRouterFastModel(fast, meter=session.fast_meter)
    else:
        from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

        preflight()
        session = Session()
        session.compiler.judge = CloudJudge(meter=session.deep_meter)
        session.slow = CloudSlowModel(meter=session.deep_meter)
        session.runtime.model = CloudFastModel(meter=session.fast_meter)
    session.cloud = True
    return session


def run_scenario(
    scenario: dict, fakes: bool, openrouter: bool = False, free: bool = False
) -> list[tuple[str, bool, str]]:
    """Returns one (label, passed, detail) per expectation."""
    session = build_session(fakes, openrouter, free)
    for text in scenario["turns"]:
        session.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        session.consolidate()
    for _ in range(session.compiler.promote_after):
        session.compiler.housekeep()  # let settled winners finish earning their pages

    pages = session.store.pages()
    pages_text = " ".join(p.content.lower() for p in pages)
    results: list[tuple[str, bool, str]] = []
    for exp in scenario.get("expect", []):
        kws = [k.lower() for k in exp["keywords"]]
        stored = all(k in pages_text for k in kws)
        results.append(
            (
                f"stored: {'+'.join(kws)}",
                stored,
                "pages: " + ("; ".join(f"[{p.gene}] {p.content}" for p in pages) or "(none)"),
            )
        )
        if "question" in exp:
            absent = [a.lower() for a in exp.get("absent", [])]
            # probe with an EMPTY buffer: this tests what the mind REMEMBERS (a fresh session
            # tomorrow), not what still sits in short-term conversational context
            resp = session.runtime.respond(exp["question"], [])
            answered = (
                (not resp.abstained)
                and all(k in resp.answer.lower() for k in kws)
                and all(a not in resp.answer.lower() for a in absent)
            )
            results.append(
                (f"answered: {exp['question']}", answered, f"answer: {resp.answer}")
            )
    return results


def _correct(resp, kws, absent) -> bool:
    ans = resp.answer.lower()
    return (not resp.abstained) and all(k in ans for k in kws) and all(a not in ans for a in absent)


def _lag(scenario, fakes, openrouter, free, cap=8):
    """Displacement lag: housekeeping passes until the right answer is served (spec §6 cost).

    Insert every turn's candidates at once, then count passes until the probe is correct — for a
    contested update the newcomer must win the pairwise contest, which takes >1 pass.
    """
    s = build_session(fakes, openrouter, free)
    users = [Turn(text=t, speaker="user", created_at=time.time()) for t in scenario["turns"]]
    for c in s.slow.extract(users):
        s.compiler.insert(c)
    exp = next((e for e in scenario.get("expect", []) if "question" in e), None)
    if exp is None:
        return None
    kws = [k.lower() for k in exp["keywords"]]
    absent = [a.lower() for a in exp.get("absent", [])]
    for n in range(1, cap + 1):
        s.compiler.housekeep()
        if _correct(s.runtime.respond(exp["question"], []), kws, absent):
            return n
    return None  # never settled within the cap


def score_scenario(scenario, fakes=False, openrouter=False, free=False) -> dict:
    """A metric row for one scenario: AA, retrieval-hit, conflict recognition, cost, lag."""
    ctype = scenario.get("conflict_type")
    s = build_session(fakes, openrouter, free)
    for text in scenario["turns"]:
        s.buffer.append(Turn(text=text, speaker="user", created_at=time.time()))
        s.consolidate()
    for _ in range(s.compiler.promote_after):
        s.compiler.housekeep()

    contested = any(len(pool) > 1 for pool in s.store.pool.values())
    recognized = bool(s.store.questions) or contested  # queued a question, or a pool defended
    aa = seh = probes = 0
    for exp in scenario.get("expect", []):
        if "question" not in exp:
            continue
        probes += 1
        kws = [k.lower() for k in exp["keywords"]]
        absent = [a.lower() for a in exp.get("absent", [])]
        resp = s.runtime.respond(exp["question"], [])
        aa += _correct(resp, kws, absent)
        seh += any(all(k in p.content.lower() for k in kws) for p in resp.used)  # gold page pulled

    row = {
        "name": scenario["name"],
        "type": ctype,
        "aa": aa / probes if probes else None,
        "seh": seh / probes if probes else None,
        "deep_tok": s.deep_meter.total,
        "fast_tok": s.fast_meter.total,
    }
    if ctype == "static":
        if scenario.get("expect_conflict", True):
            # CRS is the benchmark's static-conflict metric — did it NOTICE the contradiction
            # rather than answer right by luck? The field's ceiling here is ~0.25.
            row["crs"] = 1.0 if recognized else 0.0
        else:
            # ground truth: no genuine conflict (idiom / distractor) — CRS doesn't apply, and
            # flagging a conflict (a queued question) is the error worth counting
            row["false_flag"] = len(s.store.questions)
    if ctype in ("static", "dynamic"):
        row["lag"] = _lag(scenario, fakes, openrouter, free)
    if ctype == "conditional":
        row["false_q"] = len(s.store.questions)  # a question here is a FALSE positive; lower better
    return row


def run_metrics(scenarios, fakes, openrouter, free, out=print) -> None:
    backend = "fakes" if fakes else ("openrouter free" if free else ("openrouter" if openrouter else "cloud"))
    out(f"=== METRICS (backend: {backend}) ===")
    out("AA=answer accuracy  SEH=gold page retrieved  CRS=conflict recognised  lag=passes to settle\n")
    rows = [score_scenario(s, fakes, openrouter, free) for s in scenarios]
    for r in rows:
        parts = [f"AA {r['aa']:.2f}" if r["aa"] is not None else "AA n/a"]
        parts.append(f"SEH {r['seh']:.2f}" if r["seh"] is not None else "SEH n/a")
        if "crs" in r:
            parts.append(f"CRS {r['crs']:.2f}")
        if "false_flag" in r:
            parts.append(f"CRS n/a (no real conflict) | false-flag {r['false_flag']}")
        if r.get("lag") is not None:
            parts.append(f"lag {r['lag']}")
        if "false_q" in r:
            parts.append(f"false-Q {r['false_q']}")
        parts.append(f"cost {r['deep_tok']:,}+{r['fast_tok']:,} tok")
        out(f"[{r.get('type') or 'plain'}] {r['name']}")
        out("   " + " | ".join(parts))
    aas = [r["aa"] for r in rows if r["aa"] is not None]
    crss = [r["crs"] for r in rows if "crs" in r]
    deep = sum(r["deep_tok"] for r in rows)
    fast = sum(r["fast_tok"] for r in rows)
    out("")
    out(f"overall: AA {sum(aas)/len(aas):.2f}" if aas else "overall: AA n/a")
    if crss:
        out(f"         CRS {sum(crss)/len(crss):.2f} on static conflict (recognition) "
            "- the field's benchmarked ceiling is ~0.25")
    out(f"         cost {deep:,} deep + {fast:,} fast tokens total")


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp1252; model output is unicode
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = argv if argv is not None else sys.argv[1:]
    fakes = "--fakes" in args
    free = "--free" in args
    openrouter = "--openrouter" in args or free
    only = None
    if "--only" in args:
        only = args[args.index("--only") + 1].lower()
    path = CORPUS
    if "--corpus" in args:  # e.g. --corpus conflicts -> evals/conflicts.toml
        path = corpus_path(args[args.index("--corpus") + 1])
        if not path.exists():
            print(f"no corpus at {path}")
            return 1

    scenarios = load_corpus(path)
    if only:
        scenarios = [s for s in scenarios if only in s["name"].lower()]
    if not scenarios:
        print("no matching scenarios")
        return 1

    if not fakes:
        try:
            build_session(fakes=False, openrouter=openrouter, free=free)
        except RuntimeError as exc:
            print(f"error: {exc}")
            return 1

    if "--metrics" in args:
        run_metrics(scenarios, fakes, openrouter, free)
        return 0

    backend = (
        "fakes" if fakes
        else ("openrouter (free tier)" if free else ("openrouter" if openrouter else "cloud models"))
    )
    print(f"running {len(scenarios)} scenario(s) on {backend}\n")
    passed = failed = 0
    for scenario in scenarios:
        print(f"== {scenario['name']}")
        for label, ok, detail in run_scenario(scenario, fakes, openrouter, free):
            mark = "PASS" if ok else "FAIL"
            passed += ok
            failed += not ok
            print(f"  {mark}  {label}")
            if not ok:
                print(f"        {detail}")
        print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

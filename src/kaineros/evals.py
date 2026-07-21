"""Eval harness (Slice 5, T9a): grade what the mind ends up knowing.

Feeds the example conversations in evals/conversations.toml to a session and checks, scenario by
scenario, that the expected knowledge (a) got promoted into the clean layer and (b) comes back
when probed with a question. Grading is graded, not exact — real models phrase freely.

Usage:
    python -m kaineros.evals            # cloud models (spends tokens; needs the [cloud] extra)
    python -m kaineros.evals --fakes    # free smoke run on the fakes (expect failures)
    python -m kaineros.evals --only berlin   # scenarios whose name contains "berlin"

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
        session = Session(
            judge=OpenRouterJudge(deep),
            slow=OpenRouterSlowModel(deep),
            fast=OpenRouterFastModel(fast),
        )
    else:
        from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

        preflight()
        session = Session(judge=CloudJudge(), slow=CloudSlowModel(), fast=CloudFastModel())
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

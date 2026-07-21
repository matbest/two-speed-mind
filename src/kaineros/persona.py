"""Persona harness (spec §35): feed a scripted conversation to the mind and watch a wiki form.

Each persona lives in personas/<name>/conversation.json (a description + the user's turns). Running
one builds that persona's own wiki at personas/<name>/mind/ so you can open it afterwards, and
reports the deep/fast token cost as it goes — the "how would this do locally?" signal, made
concrete on a realistic person.

Usage:
    python -m kaineros.persona <name> [--free|--openrouter|--fakes]
    python -m kaineros.persona            # list the personas
    kaineros persona <name> --free        # via the installed command

Consolidation is SYNCHRONOUS here (not the app's background worker): each turn is fully compiled
before the next, so the deep brain always has "long enough" to build the wiki.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .cli import Session

PERSONAS = Path(__file__).resolve().parents[2] / "personas"


def load(name: str) -> dict:
    path = PERSONAS / name / "conversation.json"
    if not path.exists():
        raise RuntimeError(f"no persona '{name}' — expected {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_personas() -> list[str]:
    if not PERSONAS.exists():
        return []
    return sorted(d.name for d in PERSONAS.iterdir() if (d / "conversation.json").exists())


def _session_for(name: str, fakes: bool, openrouter: bool, free: bool) -> Session:
    mind = str(PERSONAS / name / "mind")
    if fakes:
        return Session(store_dir=mind)  # synchronous by default — the persona's own wiki
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
        s = Session(store_dir=mind)
        s.compiler.judge = OpenRouterJudge(deep, meter=s.deep_meter)
        s.slow = OpenRouterSlowModel(deep, meter=s.deep_meter)
        s.runtime.model = OpenRouterFastModel(fast, meter=s.fast_meter)
        return s
    from .cloud import CloudFastModel, CloudJudge, CloudSlowModel, preflight

    preflight()
    s = Session(store_dir=mind)
    s.compiler.judge = CloudJudge(meter=s.deep_meter)
    s.slow = CloudSlowModel(meter=s.deep_meter)
    s.runtime.model = CloudFastModel(meter=s.fast_meter)
    return s


def run(name: str, *, fakes=False, openrouter=False, free=False, out=print) -> Session:
    convo = load(name)
    turns = convo["turns"]
    session = _session_for(name, fakes, openrouter, free)
    out(f"persona: {name} — {convo.get('description', '')}")
    out(f"{len(turns)} turns; building {session.store_dir}\n")
    for i, text in enumerate(turns, 1):
        resp = session.turn(text)
        out(f"[{i}/{len(turns)}] you : {text}")
        out(f"          mind: {resp.answer}")
        out(
            f"          -> {len(session.store.pages())} pages | "
            f"deep {session.deep_meter.total:,} tok, fast {session.fast_meter.total:,} tok"
        )
    out("\n=== the wiki this persona produced ===")
    for p in session.store.pages():
        out(f"  [{p.gene}] {p.content}")
    out(
        f"\ntotals — deep brain {session.deep_meter.total:,} tokens, "
        f"fast brain {session.fast_meter.total:,} tokens"
    )
    out(f"browse the wiki: {Path(session.store_dir) / 'kainome'}")
    return session


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = argv if argv is not None else sys.argv[1:]
    names = [a for a in args if not a.startswith("-")]
    if not names:
        avail = ", ".join(list_personas()) or "(none)"
        print(f"personas: {avail}")
        print("run one: python -m kaineros.persona <name> [--free|--openrouter|--fakes]")
        return 0
    try:
        run(
            names[0],
            fakes="--fakes" in args,
            openrouter="--openrouter" in args,
            free="--free" in args,
        )
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

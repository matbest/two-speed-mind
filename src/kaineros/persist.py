"""The mind on disk (spec §20-23). Pure serialization — no policy, no models.

Layout mirrors the two layers, matching each layer's nature:
    <home>/pool.json           the working population — churns every pass, one file
    <home>/pages.json          the clean layer's machine record (what load reads back)
    <home>/kainome/            the human wiki, rendered from pages.json on every save:
        index.md               every page, listed
        <gene>.md              one readable page per promoted fact (generated — edits overwritten)

Saves are atomic (write temp, rename). Loading is honest: a missing home is a fresh mind; a
corrupt file raises with a clear message — memory is never silently discarded.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .schema import Candidate, Page, Provenance, Question
from .store import Store

SCHEMA_VERSION = 1


def _safe(gene: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", gene.lower())


def _atomic_write(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"corrupt mind file: {path} ({exc}) — fix or delete it; refusing to silently "
            "start with an empty memory"
        ) from exc


def _prov_dict(p: Provenance) -> dict:
    return {
        "source_turn_ids": list(p.source_turn_ids),
        "source_texts": list(p.source_texts),
        "created_at": p.created_at,
        "stated": p.stated,
        "confidence": p.confidence,
        "stakes": p.stakes,
        "supersedes": p.supersedes,
    }


def _prov(d: dict) -> Provenance:
    return Provenance(
        source_turn_ids=tuple(d.get("source_turn_ids", ())),
        source_texts=tuple(d.get("source_texts", ())),
        created_at=d.get("created_at", 0.0),
        stated=d.get("stated", True),
        confidence=d.get("confidence", "medium"),
        stakes=d.get("stakes", "high"),
        supersedes=d.get("supersedes", False),
    )


def append_turns(home: str | Path, turns) -> None:
    """The primary source (spec §46): verbatim turns, append-only, exactly-once (the caller is
    the compile boundary). The pool and wiki are DERIVED state; this is what they derive from."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "turns.jsonl", "a", encoding="utf-8") as f:
        for t in turns:
            f.write(
                json.dumps(
                    {"id": t.id, "at": t.created_at, "speaker": t.speaker, "text": t.text},
                    ensure_ascii=False,
                )
                + "\n"
            )


def save_store(store: Store, home: str | Path) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)

    pool_doc = {
        "schema_version": SCHEMA_VERSION,
        "pool": {
            gene: [
                {
                    "id": c.id,
                    "content": c.content,
                    "tags": list(c.tags),
                    "wins": c.wins,
                    "provenance": _prov_dict(c.provenance),
                }
                for c in candidates  # list order IS the rank order
            ]
            for gene, candidates in store.pool.items()
        },
    }
    _atomic_write(home / "pool.json", pool_doc)

    pages_doc = {
        "schema_version": SCHEMA_VERSION,
        "pages": [
            {
                "gene": page.gene,
                "content": page.content,
                "tags": list(page.tags),
                "provenance": _prov_dict(page.provenance),
                "rank_history": page.rank_history,
            }
            for page in store.clean.values()  # dict order = promotion order
        ],
    }
    _atomic_write(home / "pages.json", pages_doc)

    _atomic_write(
        home / "questions.json",
        {
            "schema_version": SCHEMA_VERSION,
            "questions": [
                {
                    "id": q.id,
                    "text": q.text,
                    "genes": list(q.genes),
                    "status": q.status,
                    "created_at": q.created_at,
                    "asked_at": q.asked_at,
                    "answered_at": q.answered_at,
                }
                for q in store.questions
            ],
        },
    )
    _render_wiki(store, home / "kainome")


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)) if epoch else "unknown"


def _render_wiki(store: Store, kainome: Path) -> None:
    """The human wiki: index.md + one <gene>.md per page, regenerated every save."""
    kainome.mkdir(exist_ok=True)
    keep = {"index.md"}
    for gene, page in store.clean.items():
        name = _safe(gene) + ".md"
        keep.add(name)
        prov = page.provenance
        said = "stated directly" if prov.stated else "inferred"
        receipts = ", ".join(prov.source_turn_ids) or "none recorded"
        history = "\n".join(
            f"- {entry.get('event', '?')} {_when(entry.get('at', 0.0))}"
            for entry in page.rank_history
        )
        tags_line = f"- tags: {', '.join(page.tags)}\n" if page.tags else ""
        # the audit line (spec §46): the user's own words, so a page is checkable at a glance
        quotes = "".join(f'- said as: "{t}"\n' for t in prov.source_texts[:3])
        body = (
            f"# {page.gene}\n\n"
            f"{page.content}\n\n"
            f"{tags_line}"
            f"- {said}, confidence {prov.confidence}, stakes {prov.stakes}\n"
            f"- first said {_when(prov.created_at)}, receipts {receipts}\n"
            f"{quotes}"
            f"{history}\n"
        )
        tmp = kainome / (name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, kainome / name)

    facts = "\n".join(
        f"- {page.content} ([{page.gene}]({_safe(gene)}.md))"
        + (f" `{', '.join(page.tags)}`" if page.tags else "")  # the cue line (spec §44): the
        for gene, page in store.clean.items()                   # words a QUESTION would use
    )
    index = f"# kainome\n\n{facts or '(nothing believed yet)'}\n"
    tmp = kainome / "index.md.tmp"
    tmp.write_text(index, encoding="utf-8")
    os.replace(tmp, kainome / "index.md")

    for stale in list(kainome.glob("*.md")) + list(kainome.glob("*.json")):
        if stale.name not in keep:  # retired pages (and pre-wiki json files) leave the wiki
            stale.unlink()


def load_store(home: str | Path) -> Store:
    home = Path(home)
    store = Store()

    pool_file = home / "pool.json"
    if pool_file.exists():
        doc = _read(pool_file)
        for gene, candidates in doc.get("pool", {}).items():
            store.pool[gene] = [
                Candidate(
                    gene=gene,
                    content=c["content"],
                    provenance=_prov(c.get("provenance", {})),
                    id=c["id"],
                    wins=c.get("wins", 0),
                    tags=tuple(c.get("tags", ())),
                )
                for c in candidates
            ]

    q_file = home / "questions.json"
    if q_file.exists():
        for d in _read(q_file).get("questions", []):
            store.questions.append(
                Question(
                    text=d["text"],
                    genes=tuple(d.get("genes", ())),
                    status=d.get("status", "pending"),
                    id=d["id"],
                    created_at=d.get("created_at", 0.0),
                    asked_at=d.get("asked_at", 0.0),
                    answered_at=d.get("answered_at", 0.0),
                )
            )

    pages_file = home / "pages.json"
    if pages_file.exists():
        doc = _read(pages_file)
        for d in doc.get("pages", []):  # list order = promotion order (fusion reads it)
            store.clean[d["gene"]] = Page(
                gene=d["gene"],
                content=d["content"],
                provenance=_prov(d.get("provenance", {})),
                rank_history=d.get("rank_history", []),
                tags=tuple(d.get("tags", ())),
            )
    else:
        # legacy layout (pre-wiki): one JSON per page under kainome/; order from promotion times
        kainome = home / "kainome"
        if kainome.exists():
            pages = [
                Page(
                    gene=d["gene"],
                    content=d["content"],
                    provenance=_prov(d.get("provenance", {})),
                    rank_history=d.get("rank_history", []),
                )
                for d in (_read(f) for f in sorted(kainome.glob("*.json")))
            ]
            pages.sort(key=lambda p: p.rank_history[0]["at"] if p.rank_history else 0.0)
            for page in pages:
                store.clean[page.gene] = page

    return store


def erase(home: str | Path) -> None:
    """Delete the persisted mind (pool + pages + wiki). Used by `/forget all` after confirmation."""
    home = Path(home)
    for name in ("pool.json", "pages.json", "questions.json", "turns.jsonl"):
        f = home / name
        if f.exists():
            f.unlink()
    kainome = home / "kainome"
    if kainome.exists():
        for f in list(kainome.glob("*.json")) + list(kainome.glob("*.md")):
            f.unlink()

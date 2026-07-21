"""The mind on disk (spec §20-23). Pure serialization — no policy, no models.

Layout mirrors the two layers, matching each layer's nature:
    <home>/pool.json            the working population — churns every pass, one file
    <home>/kainome/<gene>.json  the wiki — one file per promoted page, browsable and diffable

Saves are atomic (write temp, rename). Loading is honest: a missing home is a fresh mind; a
corrupt file raises with a clear message — memory is never silently discarded.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .schema import Candidate, Page, Provenance
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
        "created_at": p.created_at,
        "stated": p.stated,
        "confidence": p.confidence,
        "stakes": p.stakes,
    }


def _prov(d: dict) -> Provenance:
    return Provenance(
        source_turn_ids=tuple(d.get("source_turn_ids", ())),
        created_at=d.get("created_at", 0.0),
        stated=d.get("stated", True),
        confidence=d.get("confidence", "medium"),
        stakes=d.get("stakes", "high"),
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
                    "wins": c.wins,
                    "provenance": _prov_dict(c.provenance),
                }
                for c in candidates  # list order IS the rank order
            ]
            for gene, candidates in store.pool.items()
        },
    }
    _atomic_write(home / "pool.json", pool_doc)

    kainome = home / "kainome"
    kainome.mkdir(exist_ok=True)
    keep: set[str] = set()
    for gene, page in store.clean.items():
        name = _safe(gene) + ".json"
        keep.add(name)
        _atomic_write(
            kainome / name,
            {
                "schema_version": SCHEMA_VERSION,
                "gene": page.gene,
                "content": page.content,
                "provenance": _prov_dict(page.provenance),
                "rank_history": page.rank_history,
            },
        )
    for stale in kainome.glob("*.json"):
        if stale.name not in keep:  # retired pages leave the wiki
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
                )
                for c in candidates
            ]

    kainome = home / "kainome"
    if kainome.exists():
        pages = []
        for f in sorted(kainome.glob("*.json")):
            d = _read(f)
            pages.append(
                Page(
                    gene=d["gene"],
                    content=d["content"],
                    provenance=_prov(d.get("provenance", {})),
                    rank_history=d.get("rank_history", []),
                )
            )
        # clean-layer insertion order = promotion order (fusion's "older key survives" reads it);
        # restore it from each page's first promotion timestamp
        pages.sort(key=lambda p: p.rank_history[0]["at"] if p.rank_history else 0.0)
        for page in pages:
            store.clean[page.gene] = page

    return store


def erase(home: str | Path) -> None:
    """Delete the persisted mind (pool + kainome). Used by `/forget all` after confirmation."""
    home = Path(home)
    pool_file = home / "pool.json"
    if pool_file.exists():
        pool_file.unlink()
    kainome = home / "kainome"
    if kainome.exists():
        for f in kainome.glob("*.json"):
            f.unlink()

"""User profiles — named minds, each with its own wiki on disk (spec §34).

A profile is just a named home directory: %LOCALAPPDATA%\\kaineros\\profiles\\<name>\\mind\\ holds
that person's pool, pages, wiki, and (later) actions — fully isolated from every other profile.
Swapping profiles swaps the whole mind. The legacy unnamed mind (kaineros\\mind) remains the
default so existing data is never orphaned.
"""
from __future__ import annotations

import os
from pathlib import Path


def _root() -> Path:
    base = os.environ.get("KAINEROS_HOME") or (
        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "kaineros"
    )
    return Path(base) / "profiles"


def mind_dir(name: str) -> str:
    """The mind home for a named profile."""
    return str(_root() / name / "mind")


def list_profiles() -> list[str]:
    root = _root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())

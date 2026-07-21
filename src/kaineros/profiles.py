"""User profiles — named minds, each with its own wiki on disk (spec §34).

A profile is just a named home directory: %LOCALAPPDATA%\\kaineros\\profiles\\<name>\\mind\\ holds
that person's pool, pages, wiki, and (later) actions — fully isolated from every other profile.
Swapping profiles swaps the whole mind. The legacy unnamed mind (kaineros\\mind) remains the
default so existing data is never orphaned.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT = "default"


def _base() -> Path:
    return Path(
        os.environ.get("KAINEROS_HOME")
        or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "kaineros")
    )


def _root() -> Path:
    return _base() / "profiles"


def mind_dir(name: str) -> str:
    """The mind home for a named profile."""
    return str(_root() / name / "mind")


def default_mind_dir() -> str:
    """The default profile's mind — migrating a pre-profiles mind (kaineros\\mind) into it once,
    so no name given still means a real, listable 'default' profile without orphaning old data."""
    dest = _root() / DEFAULT / "mind"
    legacy = _base() / "mind"
    if not dest.exists() and legacy.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(dest))
    return str(dest)


def list_profiles() -> list[str]:
    root = _root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())

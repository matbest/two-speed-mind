"""The raw model-call transcript: every prompt in, every reply out, for both brains.

Off by default. The CLI enables it for cloud backends, one JSONL file per app run — so a
benchmark's whole conversation with the deep and fast minds can be read back verbatim:

    {"at": ..., "brain": "deep", "backend": "claude-cli", "model": "sonnet",
     "purpose": "judge.same_claim", "system": "...", "input": "...", "output": "...",
     "tokens": 412}

Logging happens at the ADAPTER boundary — the strings recorded are exactly what crossed the
wire (including any schema-fallback rewrites), not a reconstruction. Thread-safe: the
background worker logs through the same lock. Pure observation; nothing reads this file back.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

_path: Path | None = None
_lock = threading.Lock()
# a small in-memory tail of the most recent calls, so the cockpit can show what we're asking the
# models live. Thread-safe (same lock as the file write); the worker appends, the main thread
# reads and paints — the panel is NEVER painted off the main thread (that corrupts the terminal).
_recent: deque[dict] = deque(maxlen=8)


def enable(directory: str | Path) -> Path:
    """Start a fresh log file under `directory`; returns its path."""
    global _path
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _path = directory / f"calls-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    return _path


def path() -> Path | None:
    return _path


def log(
    brain: str,
    backend: str,
    model: str,
    purpose: str,
    system: str,
    input_text: str,
    output: str,
    tokens: int | None = None,
) -> None:
    entry = {
        "at": time.time(),
        "brain": brain,
        "backend": backend,
        "model": model,
        "purpose": purpose,
        "system": system,
        "input": input_text,
        "output": output,
        "tokens": tokens,
    }
    with _lock:
        _recent.append(entry)  # always feeds the live panel, even if file logging is off
        if _path is not None:
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent() -> list[dict]:
    """A snapshot of the last few calls, newest last — for the live cockpit panel."""
    with _lock:
        return list(_recent)

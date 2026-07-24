"""two-speed mind — a slow-deep compiler and a fast reader over a self-selecting knowledge base."""

__version__ = "0.0.1"


def version() -> str:
    """A build stamp that bumps on every commit: `0.<commit-count> (<short-sha>[+dirty])`.

    Derived from git at the repo (works for the editable install, which points here). `+dirty`
    marks uncommitted edits — so a benchmark result always names the exact code that produced it.
    Falls back to `__version__` when git isn't available.
    """
    import subprocess
    from pathlib import Path

    repo = str(Path(__file__).resolve().parents[2])

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=5
        ).stdout.strip()

    try:
        count = _git("rev-list", "--count", "HEAD")
        sha = _git("rev-parse", "--short", "HEAD")
        if count and sha:
            dirty = "+dirty" if _git("status", "--porcelain") else ""
            return f"0.{count} ({sha}{dirty})"
    except Exception:
        pass
    return __version__

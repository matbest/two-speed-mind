"""Kaineros desktop shell — pywebview host + JS bridge.

One Python process: pywebview embeds a WebView2 browser that renders the local
frontend in ``app/web/``. The frontend calls back into Python through the
``js_api`` bridge (``window.pywebview.api.ask(...)``).

The engine (``kaineros``) is IMPORT-ONLY here — this file never modifies it. For
the scaffold we build a ``Session`` with the deterministic FAKES so the app runs
fully offline out of the box (a default ``Session()`` uses fakes). A seam for a
real backend is left in ``build_session`` but deliberately not wired.

Run:  ``python -m app``   (from the repo root, or after ``pip install -e .``)
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

# --- Make the import-only engine importable -----------------------------------
# The repo uses a src-layout (pyproject: pythonpath = ["src"]). When kaineros is
# not pip-installed, add the sibling ``src/`` dir so ``import kaineros`` works.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kaineros.cli import Session  # noqa: E402  (path shim must run first)

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


def current_user() -> str:
    """The OS user — the app keeps ONE memory per OS user (conversations are just
    views over that single memory)."""
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


def build_session(backend: str = "fakes") -> Session:
    """Construct the engine Session.

    ``backend="fakes"`` (the scaffold default) → a deterministic, offline mind.
    Other backends (``--cloud`` etc.) are a future seam: kaineros' own ``cli.main``
    wires the cloud adapters; the desktop app would do the equivalent here. Kept on
    fakes on purpose so the skeleton has no network/model dependency.
    """
    if backend != "fakes":
        # TODO(real-backend): import kaineros.cloud adapters and pass judge/slow/fast.
        raise NotImplementedError(
            f"backend {backend!r} not wired in the scaffold — using fakes only"
        )
    return Session()  # default args == deterministic fakes, no network


class Api:
    """The JS <-> Python bridge exposed to the frontend as ``window.pywebview.api``.

    Simple and synchronous for the scaffold: one ``Session`` behind one method.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def whoami(self) -> str:
        """The OS user shown in the sidebar header."""
        return current_user()

    def ask(self, text: str) -> dict:
        """Run one conversation turn through the engine and return the split result.

        The engine keeps the phrased answer and the grounded reason apart
        (Response.answer vs Response.why); we forward both, unmixed.
        """
        text = (text or "").strip()
        if not text:
            return {"answer": "", "why": "", "abstained": False}
        resp = self._session.turn(text)
        return {
            "answer": resp.answer,
            "why": resp.why,
            "abstained": bool(getattr(resp, "abstained", False)),
        }


def main() -> int:
    import webview

    session = build_session("fakes")
    api = Api(session)
    webview.create_window(
        title="Kaineros",
        url=str(INDEX_HTML),
        js_api=api,
        width=1100,
        height=720,
        min_size=(900, 600),
        background_color="#0d1117",
    )
    # gui=None lets pywebview pick the platform default (WebView2/EdgeChromium on
    # Windows). debug=True enables the WebView2 devtools (right-click → Inspect).
    webview.start(debug=bool(os.environ.get("KAINEROS_DEBUG")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

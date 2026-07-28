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


def build_session(backend: str = "fakes", profile: str = "default") -> Session:
    """Construct the engine Session, loading a named profile's mind FROM DISK.

    ``profile`` picks which mind on disk to load (``"default"`` = the OS user's own;
    any other name = that profile, e.g. a ``bench-…`` persona). ``Session(store_dir=…)``
    loads its promoted pages, so the wiki and search have real content — unlike the
    bare in-memory scaffold.

    ``backend="fakes"`` keeps the models offline (real pages, deterministic phrasing —
    no keys needed). ``backend="claude"`` wires the real brains (deep = ``claude -p``,
    fast = Haiku), the same mix ``kaineros --claude`` uses, so answers are real.
    """
    from kaineros import profiles

    mind = (profiles.default_mind_dir()
            if profile in (None, "", profiles.DEFAULT)
            else profiles.mind_dir(profile))

    if backend == "fakes":
        s = Session(store_dir=mind)  # loads the profile's pages; deterministic fakes phrase them
        s.profile_name = profile or profiles.DEFAULT
        return s

    if backend == "claude":
        from kaineros.claude_cli import ClaudeCLIJudge, ClaudeCLISlowModel
        from kaineros.claude_cli import preflight as cli_preflight
        from kaineros.openrouter import FAST_MODEL, OpenRouterFastModel, ensure_key
        from kaineros.openrouter import preflight as or_preflight

        cli_preflight()          # proves the `claude` CLI is logged in
        ensure_key()             # OpenRouter key for the fast brain
        or_preflight(FAST_MODEL)
        s = Session(store_dir=mind, background=True, cleanup_every=4)
        s.compiler.judge = ClaudeCLIJudge(meter=s.deep_meter)
        s.slow = ClaudeCLISlowModel(meter=s.deep_meter)
        s.compiler.summariser = s.slow
        s.compiler.arc_model = s.slow
        s.runtime.model = OpenRouterFastModel(FAST_MODEL, meter=s.fast_meter)
        s.cloud = True
        s.profile_name = profile or profiles.DEFAULT
        return s

    raise NotImplementedError(f"backend {backend!r} not wired")


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


def _arg(argv: list[str], flag: str, default: str) -> str:
    """Read `--flag value` from argv, else the default."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main() -> int:
    import webview

    argv = sys.argv[1:]
    # `python -m app` -> fakes on the default profile. Add `--claude` for real answers, and
    # `--profile <name>` to load a specific mind (e.g. a bench persona).
    backend = "claude" if "--claude" in argv else "fakes"
    profile = _arg(argv, "--profile", "default")
    try:
        session = build_session(backend, profile)
    except Exception as exc:  # noqa: BLE001 - a backend/login failure shouldn't crash the window
        print(f"[kaineros] backend '{backend}' unavailable ({exc}); falling back to fakes.")
        backend, session = "fakes", build_session("fakes", profile)
    print(f"[kaineros] profile '{session.profile_name}' loaded — "
          f"{len(session.store.pages())} page(s), backend={backend}")
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

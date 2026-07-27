# Kaineros desktop app (scaffold)

A Windows desktop shell for the **Kaineros** two-speed-mind engine. One Python
process runs [pywebview](https://pywebview.flowrl.com/), which embeds a WebView2
browser rendering the local frontend in `web/`. The frontend calls Python over
the pywebview `js_api` bridge.

This is a **runnable skeleton**, not the finished product: a real three-pane
layout, an animated low-poly Three.js face with four states + TTS lip-sync, and a
real (fakes-backed) chat round-trip. Anything stubbed is marked below.

## Run it

From the repo root:

```
pip install -r app/requirements.txt
python -m app
```

The engine (`kaineros`) is imported from the sibling `src/` directory
(`app/main.py` adds it to `sys.path`), so you do **not** need to `pip install` the
engine first — though `pip install -e .` from the repo root also works.

The app boots **fully offline** on the engine's deterministic fakes (a default
`Session()` uses fakes — no network, no model). To see the window you must run it
yourself; a GUI window can't be opened in a headless/CI environment.

### WebView2 dependency (Windows)

pywebview's default Windows backend uses the **Microsoft Edge WebView2** runtime:

- **Windows 11** — bundled with the OS; nothing to install.
- **Windows 10** — usually present via Edge, but if the window fails to appear,
  install the **Evergreen WebView2 Runtime** (free, from Microsoft:
  "Download the WebView2 Runtime"). This machine is Windows 10.

## What's working vs stubbed

| Piece | Status |
|---|---|
| Three-pane layout (convo list / chat / face), dark theme | working |
| Sidebar header shows the OS user (`os.getlogin`), one memory per user | working |
| Chat round-trip through the engine (`Api.ask` → `Session.turn`) | working (fakes) |
| Answer **and** grounded `why` shown, kept apart | working |
| Face: `idle` (breathe/sway + blink) | working |
| Face: `listening` (look down/toward chat on focus/typing) | working |
| Face: `thinking` (look up-left on request-in-flight) | working |
| Face: `replying` (look at user + jaw lip-sync) | working |
| TTS via `speechSynthesis`, jaw driven by word `boundary` events + mute toggle | working (needs a system voice; jaw still flaps if TTS is muted/absent) |
| Three.js | **vendored locally** at `web/vendor/three.min.js` (r160 UMD, ~670 KB) |
| Conversation threads / "New conversation" | **stub** — they clear the visible transcript only; all threads are views over the one memory |
| Real (cloud/local) model backend | **seam only** — `build_session()` raises for non-`fakes`; wire the `kaineros.cloud` adapters here later |

Notes:
- If WebGL is unavailable, `face.js` shows a labelled placeholder and the rest of
  the app keeps working (the 3D face is the only piece that degrades).
- Opened directly in a plain browser (no pywebview), the bridge is absent and
  `ask()` returns a clearly-marked echo stub — handy for eyeballing the UI.

## Layout

```
app/
  __init__.py        package marker
  __main__.py        `python -m app` entrypoint
  main.py            pywebview window + Api bridge (ask/whoami); imports kaineros
  requirements.txt   pywebview (Three.js is vendored, not a pip dep)
  README.md          this file
  web/
    index.html       three-pane shell; loads vendor/three, face.js, app.js
    style.css        dark theme, CSS-grid three-pane layout
    app.js           chat UI, js_api bridge calls, Web Speech TTS, face wiring
    face.js          Three.js low-poly head + 4-state machine + jaw lip-sync
    vendor/
      three.min.js   Three.js r160 (vendored — offline, no CDN)
```

## Packaging plan (not built here)

The intended distribution path — **plan only**, no installer is built by this
scaffold:

1. **PyInstaller (onedir)** — bundle the Python runtime, `pywebview`, and the
   `kaineros` engine into a `dist/Kaineros/` folder. Use `--windowed` (no
   console), `--add-data` to include `app/web/` (frontend + vendored Three.js),
   and confirm the WebView2 loader DLLs pywebview needs are collected. Prefer
   **onedir** over onefile for faster start-up and simpler WebView2 behaviour.
   - Because the engine lives in `src/`, either `pip install -e .` into the build
     env (so `import kaineros` resolves) or add `src/` via `--paths`.
2. **WebView2 runtime** — do **not** bundle the browser engine; rely on the
   Evergreen runtime. The installer should detect it and, on Windows 10 machines
   without it, chain-install the Evergreen **bootstrapper** (a tiny official
   downloader) before first launch.
3. **Inno Setup** — wrap `dist/Kaineros/` into a signed `KainerosSetup.exe`:
   Start-menu + optional desktop shortcut, per-user install under
   `%LOCALAPPDATA%` (matches where the engine already stores its mind), and an
   uninstaller. Add the WebView2 bootstrapper as a prerequisite step.
4. **Signing** — Authenticode-sign the exe and installer to avoid SmartScreen
   warnings (out of scope for the scaffold).
```

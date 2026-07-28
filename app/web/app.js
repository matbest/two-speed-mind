/* app.js — chat UI, the pywebview bridge, speech, and face-state wiring.
 *
 * Round-trip: send -> face "thinking" -> window.pywebview.api.ask(text)
 *          -> render answer + why -> speak it -> face "replying" (jaw lip-syncs)
 *          -> back to "idle".
 *
 * The four face states are driven by real app events:
 *   listening  — input focused / user typing
 *   thinking   — request in flight
 *   replying   — speechSynthesis is speaking the answer
 *   idle       — otherwise
 */
(function () {
  "use strict";

  var messagesEl = document.getElementById("messages");
  var form = document.getElementById("composer");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");
  var stateLabel = document.getElementById("state-label");
  var muteBtn = document.getElementById("mute");
  var userEl = document.getElementById("os-user");
  var newConvoBtn = document.getElementById("new-convo");
  var threadList = document.getElementById("thread-list");

  var muted = false;
  var pending = false; // a request is in flight
  var streamingBody = null; // the bot bubble body the search streams tokens into
  var traceEl = document.getElementById("search-trace");

  // --- live search trace (spec §52): pages the fast brain opens, hop by hop ----
  function clearTrace() { if (traceEl) traceEl.innerHTML = ""; }
  function addTrace(hop, gene, raw) {
    if (!traceEl) return;
    var line = document.createElement("div");
    line.className = "trace-line" + (raw ? " raw" : "");
    line.textContent = "▸ " + hop + "  " + gene + (raw ? "  · raw snippet" : "");
    traceEl.appendChild(line);
  }

  // --- face-state helper: keeps the on-screen label in sync too ---------------
  function face(stateName) {
    if (window.Face) window.Face.setState(stateName);
    if (stateLabel) stateLabel.textContent = stateName;
  }

  // --- messages ---------------------------------------------------------------
  function addMessage(role, text, why, abstained) {
    var el = document.createElement("div");
    el.className = "msg " + role + (abstained ? " abstain" : "");
    var body = document.createElement("div");
    body.textContent = text;
    el.appendChild(body);
    if (why) {
      var w = document.createElement("div");
      w.className = "why";
      w.textContent = "why · " + why;
      el.appendChild(w);
    }
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function addTyping() {
    var el = document.createElement("div");
    el.className = "msg bot typing";
    el.textContent = "…";
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  // --- speech (Web Speech API); drives the jaw via face.js --------------------
  function speak(text) {
    if (muted || !("speechSynthesis" in window) || !text) {
      // No TTS: still show a brief "replying" beat so the face looks at the user.
      face("replying");
      window.Face && window.Face.setSpeaking(true);
      setTimeout(function () {
        window.Face && window.Face.setSpeaking(false);
        if (!pending) face("idle");
      }, Math.min(2500, 400 + text.length * 18));
      return;
    }
    try { window.speechSynthesis.cancel(); } catch (e) {}
    var u = new SpeechSynthesisUtterance(text);
    u.rate = 1.02;
    u.pitch = 1.0;
    u.onstart = function () {
      face("replying");
      window.Face && window.Face.setSpeaking(true);
    };
    // Word boundaries give crisp lip-sync accents; the base flap covers gaps.
    u.onboundary = function () { window.Face && window.Face.jawPulse(); };
    u.onend = u.onerror = function () {
      window.Face && window.Face.setSpeaking(false);
      if (!pending) face("idle");
    };
    window.speechSynthesis.speak(u);
  }

  // --- the bridge: call Python's ask(text) ------------------------------------
  function callAsk(text) {
    // pywebview injects window.pywebview.api after the page loads. Guard so the
    // page also works when opened in a plain browser (returns a stub echo).
    if (window.pywebview && window.pywebview.api && window.pywebview.api.ask) {
      return window.pywebview.api.ask(text);
    }
    return Promise.resolve({
      answer: "(no Python bridge — open via `python -m app`) You said: " + text,
      why: "bridge unavailable; this is a browser-only stub",
      abstained: false,
    });
  }

  function send(text) {
    if (pending) return;
    text = (text || "").trim();
    if (!text) return;
    addMessage("user", text);
    input.value = "";
    pending = true;
    sendBtn.disabled = true;
    clearTrace();
    face("thinking"); // request sent — the face searches while it looks up-left

    // a bot bubble the search streams tokens into (window.onSearch fills it live)
    var el = document.createElement("div");
    el.className = "msg bot";
    var body = document.createElement("div");
    el.appendChild(body);
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    streamingBody = body;

    Promise.resolve(callAsk(text)).then(function (resp) {
      resp = resp || {};
      streamingBody = null;
      // non-streamed turns (a greeting, a stored statement) had no tokens — fill the bubble now.
      if (!resp.streamed || !body.textContent) body.textContent = resp.answer || "";
      if (resp.abstained) el.classList.add("abstain");
      if (resp.why) {
        var w = document.createElement("div");
        w.className = "why";
        w.textContent = "why · " + resp.why;
        el.appendChild(w);
      }
      if (typeof resp.seconds === "number") {
        var tm = document.createElement("div");
        tm.className = "resp-time";
        tm.textContent = resp.seconds.toFixed(1) + " seconds";
        el.appendChild(tm);
      }
      pending = false;
      sendBtn.disabled = false;
      speak(body.textContent);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }).catch(function (err) {
      streamingBody = null;
      body.textContent = "Error: " + (err && err.message || err);
      pending = false;
      sendBtn.disabled = false;
      face("idle");
    });
  }

  // Python pushes SearchEvents here as the step-by-step search runs (spec §52): each `reading`
  // flicks the face's gaze to a new shelf + logs the file; each `token` streams into the bubble.
  window.onSearch = function (ev) {
    if (!ev) return;
    if (ev.type === "reading") {
      if (window.Face) window.Face.searchGlance();
      addTrace(ev.hop, ev.gene, ev.raw);
    } else if (ev.type === "token") {
      if (streamingBody) {
        streamingBody.textContent += ev.text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }
    // "done" is handled when the ask() promise resolves.
  };

  // --- events -----------------------------------------------------------------
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    send(input.value);
  });

  // listening: while the user is focused/typing (and nothing's in flight)
  input.addEventListener("focus", function () { if (!pending) face("listening"); });
  input.addEventListener("keydown", function () { if (!pending) face("listening"); });
  input.addEventListener("blur", function () {
    if (!pending && (!window.Face || window.Face.state === "listening")) face("idle");
  });

  muteBtn.addEventListener("click", function () {
    muted = !muted;
    muteBtn.classList.toggle("muted", muted);
    muteBtn.textContent = muted ? "🔇" : "🔊";
    if (muted) { try { window.speechSynthesis.cancel(); } catch (e) {} }
  });

  // New conversation / thread switching — stubs: they clear the middle pane. All
  // threads are views over the SAME single memory (per OS user), so we don't wipe
  // the engine; we just reset the visible transcript.
  function clearTranscript(greeting) {
    messagesEl.innerHTML = "";
    addMessage("bot", greeting || "New conversation. Ask me anything.", "");
    if (!pending) face("idle");
  }
  newConvoBtn.addEventListener("click", function () {
    clearTranscript("New conversation started. (Same memory, fresh thread.)");
  });
  threadList.addEventListener("click", function (e) {
    var btn = e.target.closest(".thread");
    if (!btn) return;
    Array.prototype.forEach.call(threadList.children, function (c) {
      c.classList.toggle("active", c === btn);
    });
    clearTranscript("Loaded thread: " + btn.textContent + " (stub view).");
  });

  // --- boot -------------------------------------------------------------------
  function boot() {
    // Face first, so state changes have somewhere to land.
    if (window.Face) window.Face.init(document.getElementById("face-canvas"));
    face("idle");

    // OS user in the sidebar header (via the bridge; falls back gracefully).
    if (window.pywebview && window.pywebview.api && window.pywebview.api.whoami) {
      Promise.resolve(window.pywebview.api.whoami()).then(function (u) {
        userEl.textContent = u || "user";
      });
    } else {
      userEl.textContent = "user";
    }

    addMessage("bot", "Hi — I'm Kaineros. Ask me anything, or tell me something to remember.", "");
  }

  // pywebview fires 'pywebviewready' once the api is injected; if we're already
  // past that (or in a plain browser), just boot. This script is at the end of
  // <body>, so DOMContentLoaded may already have fired — don't rely on it.
  var booted = false;
  function bootOnce() { if (!booted) { booted = true; boot(); } }
  if (window.pywebview && window.pywebview.api) {
    bootOnce();
  } else {
    // Real bridge: wait for the api injection.
    window.addEventListener("pywebviewready", bootOnce, { once: true });
    // Plain-browser preview (no pywebview): boot shortly after, only if the
    // bridge never showed up.
    setTimeout(function () { if (!window.pywebview) bootOnce(); }, 60);
  }
})();

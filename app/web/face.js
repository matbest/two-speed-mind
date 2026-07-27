/* face.js — a procedural low-poly head in Three.js with an animated state machine.
 *
 * MetaMask-fox *vibe*: a few flat-shaded primitives (icosahedron head, two eyes,
 * a hinged lower jaw), a couple of lights, and a per-state "look-at" target the
 * head lerps toward. Kept deliberately simple and heavily commented so the four
 * states are easy to tune.
 *
 * Public API (used by app.js):
 *   Face.init(containerEl)          -> boots the scene (or a no-WebGL fallback)
 *   Face.setState("idle" | "listening" | "thinking" | "replying")
 *   Face.setSpeaking(bool)          -> drives the jaw while replying
 *   Face.jawPulse()                 -> a single open/close nudge (word boundary)
 *
 * States and where the face looks:
 *   idle       — breathing/sway + occasional blink, gaze drifts forward.
 *   listening  — user is typing: look DOWN and slightly toward the chat (left).
 *   thinking   — request sent: look UP and to the LEFT.
 *   replying   — speaking: look at the USER (forward/centre); jaw opens/closes.
 */
window.Face = (function () {
  "use strict";

  var THREE = window.THREE;

  // --- module state -----------------------------------------------------------
  var scene, camera, renderer, root, head, jaw, leftEye, rightEye;
  var clock;
  var container;
  var enabled = false;

  var state = "idle";
  var speaking = false;
  var jawPulseUntil = 0; // ms timestamp; a boundary-driven open decays to this

  // Per-state gaze target as a small pitch/yaw offset (radians) that the head
  // rotation lerps toward. +pitch looks down, +yaw looks to ITS left (screen right
  // is mirrored — tuned by eye, see below). Values are gentle on purpose.
  var GAZE = {
    // pitch (x), yaw (y)
    idle:      { x: 0.02, y: 0.00 },
    listening: { x: 0.32, y: -0.18 }, // down + slightly toward the chat pane
    thinking:  { x: -0.28, y: 0.30 }, // up and to the left
    replying:  { x: 0.00, y: 0.00 },  // straight at the user
  };

  // blink schedule
  var nextBlinkAt = 0;
  var blinkUntil = 0;

  function _color(hex) { return new THREE.Color(hex); }

  function _buildHead() {
    root = new THREE.Group();
    scene.add(root);

    // HEAD — low-poly icosahedron, flat shaded for facets.
    var headGeo = new THREE.IcosahedronGeometry(1.0, 1);
    var skin = new THREE.MeshStandardMaterial({
      color: _color(0x6f8cff),
      flatShading: true,
      roughness: 0.55,
      metalness: 0.15,
    });
    head = new THREE.Mesh(headGeo, skin);
    root.add(head);

    // BROW ridge — a thin flat box across the upper face, for a bit of character.
    var browGeo = new THREE.BoxGeometry(1.15, 0.12, 0.25);
    var browMat = new THREE.MeshStandardMaterial({
      color: _color(0x4a63c8), flatShading: true, roughness: 0.6,
    });
    var brow = new THREE.Mesh(browGeo, browMat);
    brow.position.set(0, 0.34, 0.86);
    head.add(brow);

    // EYES — two small spheres set into the front face.
    var eyeGeo = new THREE.SphereGeometry(0.17, 10, 8);
    var eyeMat = new THREE.MeshStandardMaterial({
      color: _color(0xf2f6ff), flatShading: true, roughness: 0.3, metalness: 0.1,
      emissive: _color(0x101820), emissiveIntensity: 0.4,
    });
    leftEye = new THREE.Mesh(eyeGeo, eyeMat);
    rightEye = new THREE.Mesh(eyeGeo, eyeMat.clone());
    leftEye.position.set(-0.34, 0.12, 0.92);
    rightEye.position.set(0.34, 0.12, 0.92);
    // pupils
    var pupilGeo = new THREE.SphereGeometry(0.07, 8, 6);
    var pupilMat = new THREE.MeshStandardMaterial({ color: _color(0x0b1020), flatShading: true });
    var lp = new THREE.Mesh(pupilGeo, pupilMat); lp.position.set(0, 0, 0.13);
    var rp = new THREE.Mesh(pupilGeo, pupilMat.clone()); rp.position.set(0, 0, 0.13);
    leftEye.add(lp); rightEye.add(rp);
    head.add(leftEye); head.add(rightEye);

    // JAW — a hinged lower box. We rotate it about a pivot near the top of the box
    // so it swings open like a mouth. Parent a group at the hinge, put the jaw mesh
    // below it, then rotate the group.
    jaw = new THREE.Group();
    jaw.position.set(0, -0.30, 0.55); // hinge point, roughly mid-face
    var jawGeo = new THREE.BoxGeometry(0.9, 0.42, 0.55);
    var jawMesh = new THREE.Mesh(jawGeo, skin.clone());
    jawMesh.position.set(0, -0.21, 0.10); // hangs below the hinge
    jaw.add(jawMesh);
    head.add(jaw);
  }

  function _lights() {
    var key = new THREE.DirectionalLight(0xffffff, 1.15);
    key.position.set(2, 3, 4);
    scene.add(key);
    var rim = new THREE.DirectionalLight(0x6ea8ff, 0.6);
    rim.position.set(-3, 1, -2);
    scene.add(rim);
    scene.add(new THREE.AmbientLight(0x2a3550, 0.9));
  }

  function _fallback(msg) {
    // No WebGL (or Three.js missing): draw a static placeholder so the rest of the
    // app still runs. Clearly marked; the 3D face is the only stubbed piece here.
    enabled = false;
    if (!container) return;
    var el = document.createElement("div");
    el.className = "face-fallback";
    el.textContent = msg || "3D face unavailable (no WebGL). App runs; face is stubbed.";
    container.appendChild(el);
  }

  function init(containerEl) {
    container = containerEl;
    if (!THREE) { _fallback("Three.js not loaded — face stubbed."); return false; }
    try {
      scene = new THREE.Scene();
      var w = container.clientWidth || 360;
      var h = container.clientHeight || 480;
      camera = new THREE.PerspectiveCamera(38, w / h, 0.1, 100);
      camera.position.set(0, 0.1, 4.1);

      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(window.devicePixelRatio || 1);
      renderer.setSize(w, h);
      container.appendChild(renderer.domElement);

      _buildHead();
      _lights();
      clock = new THREE.Clock();
      enabled = true;

      window.addEventListener("resize", _onResize);
      _loop();
      return true;
    } catch (err) {
      _fallback("WebGL init failed — face stubbed. " + (err && err.message || ""));
      return false;
    }
  }

  function _onResize() {
    if (!enabled || !container) return;
    var w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  // Smoothly move `current` toward `target` by a rate-per-second factor.
  function _lerp(current, target, dt, rate) {
    var k = 1 - Math.pow(1 - rate, dt * 60); // frame-rate independent-ish
    return current + (target - current) * k;
  }

  function _loop() {
    if (!enabled) return;
    requestAnimationFrame(_loop);
    var dt = Math.min(clock.getDelta(), 0.05);
    var t = clock.elapsedTime;
    var now = performance.now();

    // --- gaze: lerp head rotation toward the current state's target -----------
    var g = GAZE[state] || GAZE.idle;
    // idle adds a slow sway so it never looks frozen.
    var swayY = state === "idle" ? Math.sin(t * 0.6) * 0.10 : 0;
    var swayX = state === "idle" ? Math.sin(t * 0.9) * 0.04 : 0;
    var targetX = g.x + swayX;
    var targetY = g.y + swayY;
    head.rotation.x = _lerp(head.rotation.x, targetX, dt, 0.08);
    head.rotation.y = _lerp(head.rotation.y, targetY, dt, 0.08);

    // breathing: gentle vertical bob + scale, strongest at idle.
    var breathAmp = state === "idle" ? 1.0 : 0.5;
    root.position.y = Math.sin(t * 1.4) * 0.03 * breathAmp;
    var breathe = 1 + Math.sin(t * 1.4) * 0.010 * breathAmp;
    root.scale.setScalar(breathe);

    // --- blink (idle/listening/replying; not while thinking-away) -------------
    if (now > nextBlinkAt) {
      blinkUntil = now + 110;                 // eyes shut ~110ms
      nextBlinkAt = now + 2600 + Math.random() * 3600;
    }
    var blinking = now < blinkUntil;
    var eyeScaleY = blinking ? 0.12 : 1.0;
    leftEye.scale.y = _lerp(leftEye.scale.y, eyeScaleY, dt, 0.5);
    rightEye.scale.y = _lerp(rightEye.scale.y, eyeScaleY, dt, 0.5);

    // --- jaw: open/close while replying+speaking ------------------------------
    var jawTarget = 0; // radians of opening
    if (state === "replying" && (speaking || now < jawPulseUntil)) {
      // A base oscillation gives continuous lip flap; boundary pulses add accents.
      var osc = (Math.sin(t * 20) * 0.5 + 0.5);       // 0..1 fast flap
      var pulse = now < jawPulseUntil ? 0.5 : 0.0;    // extra open on word boundary
      jawTarget = 0.15 + osc * 0.35 + pulse;
      jawTarget = Math.min(jawTarget, 0.7);
    }
    jaw.rotation.x = _lerp(jaw.rotation.x, jawTarget, dt, 0.45);

    renderer.render(scene, camera);
  }

  // --- public setters ---------------------------------------------------------
  function setState(next) {
    if (!GAZE[next]) return;
    state = next;
  }
  function setSpeaking(v) { speaking = !!v; }
  function jawPulse() { jawPulseUntil = performance.now() + 130; }

  return {
    init: init,
    setState: setState,
    setSpeaking: setSpeaking,
    jawPulse: jawPulse,
    get state() { return state; },
    get enabled() { return enabled; },
  };
})();

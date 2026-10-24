/* Ticket E4 — the mouth that says the words.
   Brent's drill verdict (2026-10-21 15:21): "His talking
   should look like he is saying the words with his voice
   not this repeat." The baked /avatar/idle.mp4 talking
   loop is retired as the speech presentation. Now the
   mouth is drawn on the poster face itself, driven by
   the REAL TTS audio: og_r97's wave meter already runs a
   Web Audio analyser on the very element that plays OG's
   voice, and this module reads that analyser's live
   time-domain RMS (ogWaveMeter.level()) — mouth openness
   tracks the actual speech envelope, syllable by
   syllable, and sits shut at silence.

   Honesty law (the E2 invariant, one layer down):
   - No audio through the graph -> level 0 -> no mouth.
     A typed exchange with no voice playing shows a calm,
     attentive face — never a flapping loop.
   - If Web Audio is unavailable, ogWaveMeter.level does
     not exist and the mouth never opens; playback itself
     is untouched (this module never touches the audio
     element, never creates an audio graph).
   - The loop runs only while speech is live (speakingNow
     or the meter active) plus a short settle; at rest
     this module does nothing at all.

   Geometry — RE-MEASURED for Fix B round 2 (2026-10-23,
   Brent's phone drill: "look at where his mouth is
   talking from — that's not right"). The E4 constants
   rested on a poster reading of (0.49, 0.612) remapped
   through the look layer's documented medallion reframe
   — but the reframe math does not describe the live top
   bar: measured in the rendered page (canvas-pixel
   centroid of the drawn mouth vs the artwork's lip line
   in the same screenshots), the old medallion anchor
   painted the mouth ~0.076 of the face height too high —
   over the philtrum, not on the lips. Round 2 anchors
   every face to measurements, not derivations:
   - the artwork's lip line: poster gradient peak
     (0.50, 0.594); hero wrap, grounded in a rendered
     screenshot: (0.502, 0.585).
   - hero: MX = .500, MY = .588 (the two readings'
     midpoint; the hero's object-fit: cover presentation
     matched its derivation, so this is a small
     correction from .486 / .612).
   - top bar / profile: the lips sit at canvas fractions
     (0.505, 0.621) in the live page; MY = .610 puts the
     fully-open centre (MY + drop) on the lip line and
     the closed sliver a hair above it; MX = .505.
   The size coefficients scale with the face itself
   (cheeks ~46 px across here vs ~19 px in the 68 px
   medallion, a factor ~1.9 on the coefficients after
   the canvas growth), so the drawn mouth keeps the same
   proportion of the face it had in E4.
   The drawn cavity widens/deepens with openness and its
   centre drops a touch as the jaw opens. One canvas per
   showcase wrapper (hero / top bar / profile — the same
   three faces og_idle drives), painted over the still,
   pointer-transparent. window.OGMOUTH exposes the live
   state for the gate, OGIDLE / OGRAIN style.

   Defensive by contract (the og_r97 shape): feature-
   detected, try/caught, never throws. */
(function () {
  'use strict';
  if (window.OGMOUTH) return;

  /* Per-face geometry: [selector, mouth centre x/y as
     canvas fractions, radius coefficients, jaw-drop
     coefficient, canvas corner radius]. The hero entry
     is Ticket E5's (see header); the other two keep the
     E4 medallion numbers byte-for-byte. */
  var FACES = [
    { sel: '#emptyState .og-ava-wrap', mx: 0.500, my: 0.588,
      rx0: 0.040, rx1: 0.051, ry0: 0.004, ry1: 0.036,
      drop: 0.015, rad: '0' },
    { sel: '.og-top-ava', mx: 0.505, my: 0.610,
      rx0: 0.021, rx1: 0.027, ry0: 0.003, ry1: 0.027,
      drop: 0.011, rad: '50%' },
    { sel: '.og-profile-ava', mx: 0.505, my: 0.610,
      rx0: 0.021, rx1: 0.027, ry0: 0.003, ry1: 0.027,
      drop: 0.011, rad: '50%' }
  ];
  var state = { active: false, level: 0, open: 0, frames: 0 };
  window.OGMOUTH = {
    version: 1,
    state: function () {
      return { version: 1, active: state.active,
               level: +state.level.toFixed(4),
               open: +state.open.toFixed(3),
               frames: state.frames };
    }
  };

  var faces = [];
  function build() {
    FACES.forEach(function (g) {
      var wrap = document.querySelector(g.sel);
      if (!wrap || !wrap.querySelector('img.og-avatar')) return;
      var c = document.createElement('canvas');
      c.className = 'og-mouth-canvas';
      c.setAttribute('aria-hidden', 'true');
      c.style.cssText = 'position:absolute;inset:0;width:100%;' +
        'height:100%;border-radius:' + g.rad + ';pointer-events:none;';
      wrap.appendChild(c);
      faces.push({ wrap: wrap, canvas: c, g: g,
                   ctx: c.getContext ? c.getContext('2d') : null });
    });
  }

  function speaking() {
    try {
      return (typeof speakingNow !== 'undefined') && !!speakingNow;
    } catch (e) { return false; }
  }
  function meterOn() {
    try {
      return !!(window.ogWaveMeter && ogWaveMeter.active &&
                ogWaveMeter.active());
    } catch (e) { return false; }
  }
  function level() {
    try {
      if (window.ogWaveMeter && ogWaveMeter.level) {
        return ogWaveMeter.level() || 0;
      }
    } catch (e) { }
    return 0;
  }

  function sizeCanvases() {
    faces.forEach(function (f) {
      var w = f.wrap.clientWidth || 0, h = f.wrap.clientHeight || 0;
      if (!w || !h) return;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (f.canvas.width !== Math.round(w * dpr)) {
        f.canvas.width = Math.round(w * dpr);
        f.canvas.height = Math.round(h * dpr);
      }
    });
  }

  function drawFace(f) {
    var ctx = f.ctx, c = f.canvas;
    if (!ctx || !c.width) return;
    var W = c.width, H = c.height;
    ctx.clearRect(0, 0, W, H);
    var o = state.open;
    if (o <= 0.03) return;
    var g = f.g;
    var cx = g.mx * W, cy = (g.my + g.drop * o) * H;
    var rx = W * (g.rx0 + g.rx1 * o);
    var ry = H * (g.ry0 + g.ry1 * o);
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(24,9,3,0.94)';
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, Math.PI * 0.15, Math.PI * 0.85);
    ctx.strokeStyle = 'rgba(255,180,80,0.28)';
    ctx.lineWidth = Math.max(1, H * 0.004);
    ctx.stroke();
  }

  var raf = 0;
  function loop() {
    raf = 0;
    var lvl = level();
    state.level = lvl;
    var target = (lvl - 0.015) * 2.6;
    if (target < 0) target = 0;
    if (target > 1) target = 1;
    var k = target > state.open ? 0.55 : 0.22;
    state.open += (target - state.open) * k;
    if (state.open < 0.003 && target === 0) state.open = 0;
    faces.forEach(drawFace);
    state.frames++;
    if (speaking() || meterOn() || state.open > 0) {
      raf = requestAnimationFrame(loop);
    } else {
      state.active = false;
      state.level = 0;
      faces.forEach(drawFace);
    }
  }
  function kick() {
    if (state.active) return;
    state.active = true;
    sizeCanvases();
    if (!raf) raf = requestAnimationFrame(loop);
  }

  function init() {
    try { build(); } catch (e) { }
    if (!faces.length) return;
    /* A 200 ms glance that only flips the switch when
       speech is live — the Main Thread Rule shape. */
    setInterval(function () {
      if (speaking() || meterOn()) kick();
    }, 200);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();

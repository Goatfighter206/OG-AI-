/* Ticket E: natural avatar idle — the showcase avatar stops
   looping and starts living. Before this module the showcase
   still ran styles.css's ogIdle2 keyframes: an exactly-4.8 s
   loop with its two simulated blinks (brightness dips) baked
   at fixed phases (8-10% and 54-56%) of every cycle, over a
   ~5-6 s looping idle video (og-avatar-js) — two metronomes,
   and the eye learns both within a minute.

   This module is the single motion authority for the three
   showcase wrappers (#emptyState .og-ava-wrap, .og-top-ava,
   .og-profile-ava). It drives each wrapper's transform from
   parts that never share a period:

     breathing    — a swell whose period (3.4-5.2 s) and
                    depth are re-drawn at the end of every
                    single breath, so no two breaths match.
                    Ticket E4 (Brent's drill, 2026-10-21):
                    the breath rides the FACE inside the ring
                    — the still itself rises and swells on
                    the breath phase while the wrapper (and
                    the ring on it) stays steady — at
                    medallion size the old wrapper-scale
                    breath (<=1.3%) was under a pixel and
                    read as frozen. AVATAR MOTION step
                    (2026-10-23): the rise (up to 4.2 px) and
                    swell (2.0-4.0%) are sized to READ at
                    phone scale — see the amplitude note
                    below. While real voice plays (the
                    og_r97 meter active) the face's breath
                    eases to ~1/3 so the artwork holds
                    steady under the speech-driven mouth;
     head drift   — six slow sines with incommensurate
                    periods (7.3-17.9 s) summed for rotate /
                    translate — the sum's true period is far
                    longer than any session;
     weight shift — a random-walk resting offset: a new
                    target every 5-11 s, eased toward with a
                    fresh time constant (1.4-2.6 s) each time;
     blink        — REMOVED by Ticket H3 (2026-10-22):
                    the 110 ms whole-poster brightness dip
                    read on the phone as a flash on OG. This
                    module writes transforms only; the
                    avatar's brightness never changes.

   Amplitudes (AVATAR MOTION step, 2026-10-23 — Brent's
   standard: "move naturally and real, not on a loop"):
   the E-era caps (|translate| <= ~4 px peaks, ~1-2 px
   typical) measured BELOW casual perception on the phone
   — OG read as a still photo (the E-lane Cause C finding).
   The caps are now sized for a face you can SEE living at
   arm's length, still short of cartoon: wrapper translate
   peaks ~7 px (typical ~2.5-3 px), |rotate| peaks ~2.4 deg,
   breath rise up to 4.2 px with a 2.0-4.0% swell on the
   still. Every component stays aperiodic (re-drawn breath,
   incommensurate drift, random-walk shift) — bigger, never
   loopier. One rAF loop, transform strings only; pauses
   on a hidden tab; never starts under
   prefers-reduced-motion (and stops if that flips on).
   While it runs it retires the legacy keyframe idle on the
   showcase stills ONLY (state classes thinking/talking/
   listening/nod/win keep their feedback; chat reply avatars
   and the menu avatar are untouched). window.OGIDLE exposes
   state for the gate, same shape as OGRAIN. */
(function () {
  "use strict";
  if (window.OGIDLE) return;

  var mq = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;

  var api = { version: 1, running: false, reduced: !!(mq && mq.matches),
              frames: 0, targets: 0, sample: null };
  window.OGIDLE = {
    version: 1,
    state: function () {
      return { version: api.version, running: api.running,
               reduced: api.reduced, frames: api.frames,
               targets: api.targets, sample: api.sample };
    }
  };
  if (api.reduced) return;

  var SELS = ["#emptyState .og-ava-wrap", ".og-top-ava",
              ".og-profile-ava"];
  var els = [];
  SELS.forEach(function (sel) {
    var el = document.querySelector(sel);
    if (el) els.push(el);
  });
  if (!els.length) return;
  api.targets = els.length;

  var css = document.createElement("style");
  css.id = "og-idle-css";
  css.textContent =
    "html.og-idle-on .og-ava-wrap,html.og-idle-on .og-top-ava," +
    "html.og-idle-on .og-profile-ava{will-change:transform}" +
    "html.og-idle-on .og-ava-wrap img.og-avatar:not(.thinking)" +
    ":not(.talking):not(.listening):not(.nod):not(.win)," +
    "html.og-idle-on .og-top-ava img.og-avatar:not(.thinking)" +
    ":not(.talking):not(.listening):not(.nod):not(.win)," +
    "html.og-idle-on .og-profile-ava img.og-avatar:not(.thinking)" +
    ":not(.talking):not(.listening):not(.nod):not(.win)" +
    "{animation:none}";
  document.head.appendChild(css);
  document.documentElement.classList.add("og-idle-on");

  function rr(a, b) { return a + Math.random() * (b - a); }

  function makeTarget(el, seed) {
    return {
      el: el,
      img: el.querySelector("img.og-avatar"),
      vid: el.querySelector("video"),
      /* breathing: phase advances on a period that is
         re-drawn, with a new depth, at every cycle end */
      bPhase: rr(0, 6.283), bPeriod: rr(3400, 5200),
      bAmp: rr(0.010, 0.020),
      /* drift: fixed incommensurate periods, random phases */
      ph: [rr(0, 6.283), rr(0, 6.283), rr(0, 6.283),
           rr(0, 6.283), rr(0, 6.283), rr(0, 6.283)],
      /* weight shift: eased random walk */
      sx: 0, sy: 0, sr: 0, tx: 0, ty: 0, tr: 0,
      tau: rr(1400, 2600), nextShift: rr(2500, 6000),
      /* (blink state removed — Ticket H3, see frame()) */
    };
  }
  var targets = els.map(makeTarget);

  var raf = 0, last = 0, t = 0;

  function frame(now) {
    if (!api.running) return;
    raf = requestAnimationFrame(frame);
    var dt = last ? Math.min(now - last, 100) : 16;
    last = now;
    t += dt;
    api.frames++;
    for (var i = 0; i < targets.length; i++) {
      var g = targets[i];

      /* breathing */
      g.bPhase += dt * 6.28318 / g.bPeriod;
      if (g.bPhase >= 6.28318) {
        g.bPhase -= 6.28318;
        g.bPeriod = rr(3400, 5200);
        g.bAmp = rr(0.010, 0.020);
      }
      var breath01 = (1 - Math.cos(g.bPhase)) / 2;

      /* weight shift: new target, new easing constant */
      if (t >= g.nextShift) {
        g.tx = rr(-3.4, 3.4); g.ty = rr(-2.4, 2.4);
        g.tr = rr(-1.1, 1.1);
        g.tau = rr(1400, 2600);
        g.nextShift = t + rr(5000, 11000);
      }
      var k = 1 - Math.exp(-dt / g.tau);
      g.sx += (g.tx - g.sx) * k;
      g.sy += (g.ty - g.sy) * k;
      g.sr += (g.tr - g.sr) * k;

      /* head drift: summed incommensurate sines */
      var dx = 2.6 * Math.sin(t / 8900 * 6.28318 + g.ph[2]) +
               1.2 * Math.sin(t / 15700 * 6.28318 + g.ph[3]);
      var dy = 1.9 * Math.sin(t / 10300 * 6.28318 + g.ph[4]) +
               1.0 * Math.sin(t / 17900 * 6.28318 + g.ph[5]);
      var rot = 0.80 * Math.sin(t / 7300 * 6.28318 + g.ph[0]) +
                0.50 * Math.sin(t / 11700 * 6.28318 + g.ph[1]);

      var x = dx + g.sx, y = dy + g.sy, r = rot + g.sr,
          s = 1;
      g.el.style.transform = "translate3d(" + x.toFixed(2) + "px," +
        y.toFixed(2) + "px,0) rotate(" + r.toFixed(3) + "deg) " +
        "scale(" + s.toFixed(4) + ")";
      if (i === 0) api.sample = { tx: +x.toFixed(2),
        ty: +y.toFixed(2), rot: +r.toFixed(3),
        scale: +s.toFixed(4) };

      /* Ticket E4: the breath itself rides the still INSIDE
         the wrapper — chest rise (translateY) + swell
         (1 + depth, depth = bAmp*2) on the breath phase,
         so the face visibly breathes while the wrapper and
         its ring hold steady. Only while the still is the
         face showing (a playing clip owns the frame
         instead); a mood-class CSS animation on the still
         overrides this inline transform while it runs, as
         before.
         AVATAR MOTION step (2026-10-23): the rise rides
         the same per-breath depth draw as the swell —
         bAmp IS this breath's depth, re-drawn every
         cycle: rise peak = 2.2 + bAmp*100 = 3.2-4.2 px,
         swell 2.0-4.0% — no two breaths match in period,
         rise, or swell, and the sizes read at phone
         scale. While real voice is playing (the og_r97 wave
         meter active), the breath on the still eases to
         ~1/3: the mouth is drawn on the wrapper over this
         still, and a face that holds steadier while he
         talks keeps the speech-driven mouth registered
         (and reads natural — people don't bob on their
         breath mid-sentence). The wrapper's own drift is
         untouched: it carries face and mouth together. */
      if (g.img) {
        var vidPlaying = g.vid && !g.vid.hidden &&
          g.vid.readyState >= 2 && !g.vid.paused;
        var stillShown = g.img.style.visibility !== "hidden" &&
          g.img.offsetWidth > 0;
        if (stillShown && !vidPlaying) {
          var bFac = 1;
          try {
            if (window.ogWaveMeter && ogWaveMeter.active())
              bFac = 0.35;
          } catch (e) { bFac = 1; }
          g.img.style.transform = "translate3d(0," +
            (-(breath01 * (2.2 + g.bAmp * 100) * bFac)).toFixed(2) +
            "px,0) scale(" +
            (1 + breath01 * g.bAmp * 2 * bFac).toFixed(4) + ")";
        } else if (g.img.style.transform) {
          g.img.style.transform = "";
        }
      }

      /* Ticket H3 (2026-10-22, Brent's drill): NO blink.
         The simulated blink was a brightness 0.7 filter
         over the WHOLE poster for 110 ms every 2.6-7 s —
         at medallion size that is not an eyelid, it is a
         flash on OG (measured on the owner's own drill
         video: the avatar's mean luminance dips ~4% for
         ~3 frames and snaps back). It is removed whole:
         this module never writes a filter again, so the
         avatar's brightness is steady in every state.
         Breathing, drift, and weight shift (transform
         only) are untouched. */
    }
  }

  function start() {
    if (api.running || api.reduced) return;
    api.running = true;
    last = 0;
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    api.running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else start();
  });
  if (mq && mq.addEventListener) mq.addEventListener("change",
    function (e) {
      if (e.matches) {
        api.reduced = true;
        stop();
        document.documentElement.classList.remove("og-idle-on");
        targets.forEach(function (g) {
          g.el.style.transform = "";
        });
      }
    });

  if (!document.hidden) start();
})();

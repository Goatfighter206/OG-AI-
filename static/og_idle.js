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
                    single breath, so no two breaths match;
     head drift   — six slow sines with incommensurate
                    periods (7.3-17.9 s) summed for rotate /
                    translate — the sum's true period is far
                    longer than any session;
     weight shift — a random-walk resting offset: a new
                    target every 5-11 s, eased toward with a
                    fresh time constant (1.4-2.6 s) each time;
     blink        — poster still only (a video's eyes are
                    baked; see the ceiling note in the round
                    report): a 110 ms brightness dip every
                    2.6-7 s, 18% of them double.

   Amplitudes are capped small (alive, not distracting):
   |translate| <= ~4 px, |rotate| <= ~1.8 deg, scale within
   [1, 1.013] plus the eased shift. One rAF loop, transform
   strings only; pauses on a hidden tab; never starts under
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
      bAmp: rr(0.006, 0.013),
      /* drift: fixed incommensurate periods, random phases */
      ph: [rr(0, 6.283), rr(0, 6.283), rr(0, 6.283),
           rr(0, 6.283), rr(0, 6.283), rr(0, 6.283)],
      /* weight shift: eased random walk */
      sx: 0, sy: 0, sr: 0, tx: 0, ty: 0, tr: 0,
      tau: rr(1400, 2600), nextShift: rr(2500, 6000),
      /* blink (still only) */
      nextBlink: rr(1800, 4500), blinkUntil: 0, blinkAgain: 0
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
        g.bAmp = rr(0.006, 0.013);
      }
      var breath = g.bAmp * (1 - Math.cos(g.bPhase)) / 2;

      /* weight shift: new target, new easing constant */
      if (t >= g.nextShift) {
        g.tx = rr(-2.2, 2.2); g.ty = rr(-1.6, 1.6);
        g.tr = rr(-0.9, 0.9);
        g.tau = rr(1400, 2600);
        g.nextShift = t + rr(5000, 11000);
      }
      var k = 1 - Math.exp(-dt / g.tau);
      g.sx += (g.tx - g.sx) * k;
      g.sy += (g.ty - g.sy) * k;
      g.sr += (g.tr - g.sr) * k;

      /* head drift: summed incommensurate sines */
      var dx = 1.5 * Math.sin(t / 8900 * 6.28318 + g.ph[2]) +
               0.7 * Math.sin(t / 15700 * 6.28318 + g.ph[3]);
      var dy = 1.1 * Math.sin(t / 10300 * 6.28318 + g.ph[4]) +
               0.6 * Math.sin(t / 17900 * 6.28318 + g.ph[5]);
      var rot = 0.55 * Math.sin(t / 7300 * 6.28318 + g.ph[0]) +
                0.35 * Math.sin(t / 11700 * 6.28318 + g.ph[1]);

      var x = dx + g.sx, y = dy + g.sy, r = rot + g.sr,
          s = 1 + breath;
      g.el.style.transform = "translate3d(" + x.toFixed(2) + "px," +
        y.toFixed(2) + "px,0) rotate(" + r.toFixed(3) + "deg) " +
        "scale(" + s.toFixed(4) + ")";
      if (i === 0) api.sample = { tx: +x.toFixed(2),
        ty: +y.toFixed(2), rot: +r.toFixed(3),
        scale: +s.toFixed(4) };

      /* blink: only when the poster still is what's showing
         (the avatar JS hides the still while a clip plays) */
      if (g.img && g.blinkUntil && t >= g.blinkUntil) {
        g.img.style.filter = "";
        g.blinkUntil = 0;
        if (g.blinkAgain) {
          g.nextBlink = t + 260; g.blinkAgain = 0;
        } else {
          g.nextBlink = t + rr(2600, 7000);
        }
      }
      if (g.img && !g.blinkUntil && t >= g.nextBlink) {
        var vidPlaying = g.vid && !g.vid.hidden &&
          g.vid.readyState >= 2 && !g.vid.paused;
        var stillShown = g.img.style.visibility !== "hidden" &&
          g.img.offsetWidth > 0;
        if (stillShown && !vidPlaying) {
          g.img.style.filter = "brightness(.7)";
          g.blinkUntil = t + 110;
          g.blinkAgain = Math.random() < 0.18 ? 1 : 0;
        } else {
          g.nextBlink = t + 1200;
        }
      }
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
          if (g.img) g.img.style.filter = "";
        });
      }
    });

  if (!document.hidden) start();
})();

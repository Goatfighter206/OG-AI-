/* AVATAR CIRCLE + BLINK — OG blinks (2026-10-24).
   Brent's directive (2026-10-24 10:02 PDT): "Make it
   look like he's in a circle not a square but you don't
   wanna see any edges and he need to be able to blink."
   The circle half lives in og_look.css (the hero joins
   the top-bar / profile medallions); this module is the
   blink half.

   The poster is a still — its eyes cannot move (the E3
   asset ceiling) — so the blink is an eyelid layer drawn
   on a canvas over the eye line of each showcase face
   (hero / top bar / profile — the same three wrappers
   og_idle and og_mouth serve), exactly the og_mouth
   pattern one floor up: a per-wrapper canvas, pointer-
   transparent, painted only while a blink runs.

   The H3 law holds (Brent, 2026-10-22): NO whole-poster
   brightness dip — that read as a flash on his phone and
   og_idle's simulated blink was removed whole for it.
   These are real LIDS: skin-toned shapes that descend
   over the eyes and lift again, nothing else on the
   face changes brightness, ever.

   Rhythm — Brent's no-loop law governs: the gap between
   blinks is a fresh random draw of 2.0-6.0 s every time
   (human range), about one blink in eight is followed
   ~0.24-0.42 s later by a second one (the double-blink
   people actually do), and the close/hold/open is
   ~85/35/130 ms. There is no period anywhere in this
   module to learn. Blinking continues while OG speaks
   (people blink while talking); the mouth lives on its
   own canvas and is untouched.

   Placement — measured, not derived (the Fix B lesson):
   the eye line was read off the RENDERED medallions in
   the gate harness (the poster's own fractions remapped
   through the E4 reframe under-predict the live face,
   the same miss Fix B found on the lips). Eyes sit at
   wrapper fractions L (0.417, 0.443) / R (0.578, 0.440),
   openings ~0.068 x 0.027, tilting ~6 deg down toward
   the nose. Lid colours are sampled from the poster
   bitmap itself (median skin above each eye, darkest
   quartile for the lash), with baked fallbacks — the
   layer matches the art because it is made of the art's
   own colours.

   Honesty / scope:
   - The breath moves the still a few px under the
     wrapper (og_idle); while a blink runs, this canvas
     mirrors the still's own inline transform frame by
     frame, so the lids ride the breath WITH the artwork
     instead of floating over it.
   - Blinks paint only while the default poster is the
     face showing. A visitor's E6 identity art is a
     different face with a different eye line — rather
     than paste lids in the wrong place, the layer
     stands down for it (the poster check is per blink).
   - prefers-reduced-motion: never starts (and stops if
     it flips on), the same obedience og_idle keeps.
   - A face whose still is hidden or whose job-state
     clip is playing never blinks (the clip is another
     face angle; lids would land on the wrong eyes).
   - Defensive by contract (the og_mouth shape):
     feature-detected, try/caught, never throws.
   window.OGBLINK exposes the live state for the gate,
   OGIDLE / OGMOUTH style. */
(function () {
  'use strict';
  if (window.OGBLINK) return;

  var mq = window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)')
    : null;

  var EYES = [
    { cx: 0.417, cy: 0.443, tilt: 0.10 },
    { cx: 0.578, cy: 0.440, tilt: -0.10 }
  ];
  var EYE_W = 0.076, EYE_H = 0.038;
  var CLOSE_MS = 85, HOLD_MS = 35, OPEN_MS = 130;
  var GAP_MIN = 2000, GAP_MAX = 6000, DOUBLE_P = 0.12;

  var state = { reduced: !!(mq && mq.matches), faces: 0,
                blinks: 0, doubles: 0, coverage: 0,
                intervals: [], lastGap: 0 };
  window.OGBLINK = {
    version: 1,
    state: function () {
      return { version: 1, reduced: state.reduced,
               faces: state.faces, blinks: state.blinks,
               doubles: state.doubles,
               coverage: +state.coverage.toFixed(3),
               intervals: state.intervals.slice(),
               lastGap: state.lastGap };
    }
  };
  if (state.reduced) return;

  function rr(a, b) { return a + Math.random() * (b - a); }

  /* --- palette: sampled from the poster bitmap ------- */
  var pal = {
    skinL: [176, 120, 88], skinR: [176, 120, 88],
    lash: [54, 36, 24]
  };
  function shade(c, f) {
    return [Math.min(255, Math.round(c[0] * f)),
            Math.min(255, Math.round(c[1] * f)),
            Math.min(255, Math.round(c[2] * f))];
  }
  function rgba(c, a) {
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' +
      a + ')';
  }
  function medianPatch(d, W, x0, x1, y0, y1) {
    var rs = [], gs = [], bs = [];
    for (var y = y0; y < y1; y++) {
      for (var x = x0; x < x1; x++) {
        var i = (y * W + x) * 4;
        rs.push(d[i]); gs.push(d[i + 1]); bs.push(d[i + 2]);
      }
    }
    var med = function (a) {
      a.sort(function (p, q) { return p - q; });
      return a[a.length >> 1];
    };
    return [med(rs), med(gs), med(bs)];
  }
  function samplePalette(img) {
    try {
      var c = document.createElement('canvas');
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      var x2 = c.getContext('2d');
      x2.drawImage(img, 0, 0);
      var W = c.width, H = c.height;
      var d = x2.getImageData(0, 0, W, H).data;
      pal.skinL = medianPatch(d, W,
        Math.round(W * 0.360), Math.round(W * 0.452),
        Math.round(H * 0.350), Math.round(H * 0.370));
      pal.skinR = medianPatch(d, W,
        Math.round(W * 0.545), Math.round(W * 0.637),
        Math.round(H * 0.350), Math.round(H * 0.370));
      /* lash: darkest quartile along the lash line */
      var dark = [];
      for (var y = Math.round(H * 0.384);
           y < Math.round(H * 0.396); y++) {
        for (var x = Math.round(W * 0.350);
             x < Math.round(W * 0.645); x++) {
          var i = (y * W + x) * 4;
          dark.push([d[i] + d[i + 1] + d[i + 2],
                     d[i], d[i + 1], d[i + 2]]);
        }
      }
      dark.sort(function (p, q) { return p[0] - q[0]; });
      var q = dark.slice(0, Math.max(8, dark.length >> 2));
      var n = q.length >> 1;
      pal.lash = [q[n][1], q[n][2], q[n][3]];
    } catch (e) { /* fallbacks stand */ }
  }

  /* --- faces ------------------------------------------ */
  var SELS = ['#emptyState .og-ava-wrap', '.og-top-ava',
              '.og-profile-ava'];
  var faces = [];
  function build() {
    SELS.forEach(function (sel) {
      var wrap = document.querySelector(sel);
      if (!wrap || !wrap.querySelector('img.og-avatar')) return;
      var c = document.createElement('canvas');
      c.className = 'og-blink-canvas';
      c.setAttribute('aria-hidden', 'true');
      c.style.cssText = 'position:absolute;inset:0;width:100%;' +
        'height:100%;border-radius:50%;pointer-events:none;';
      wrap.appendChild(c);
      faces.push({
        wrap: wrap, canvas: c,
        ctx: c.getContext ? c.getContext('2d') : null,
        img: wrap.querySelector('img.og-avatar'),
        vid: wrap.querySelector('video'),
        timer: 0, raf: 0, lastStart: 0, sampled: false
      });
    });
    state.faces = faces.length;
  }

  function eligible(f) {
    try {
      if (!f.wrap.clientWidth || !f.wrap.clientHeight) {
        return false;
      }
      var img = f.img;
      if (!img || !img.complete || !img.naturalWidth) {
        return false;
      }
      if ((img.currentSrc || '').indexOf(
            '/avatar/poster.webp') === -1) return false;
      if (img.style.visibility === 'hidden' ||
          img.offsetWidth === 0) return false;
      var vid = f.vid;
      if (vid && !vid.hidden && vid.readyState >= 2 &&
          !vid.paused) return false;
      /* covered faces stand down: while one of the
         app's own full surfaces lies over a face — the
         profile sheet, a seat screen, the full browser
         window — that face is not the one being looked
         at, so it does not blink underneath. (The menu
         drawer needs no rule here: the look layer hides
         the capsule outright while it is open, and a
         hidden wrapper fails the size check above.) */
      var prof = document.getElementById('ogProfile');
      if (prof && !prof.hidden &&
          !prof.contains(f.wrap)) return false;
      var seat = document.getElementById('ogSeatScreen');
      if (seat && seat.offsetWidth > 0 &&
          !seat.contains(f.wrap)) {
        var sr = seat.getBoundingClientRect();
        var wr = f.wrap.getBoundingClientRect();
        if (wr.bottom > sr.top) return false;
      }
      var bp = document.getElementById('browserPanel');
      if (bp && !bp.hidden && bp.offsetWidth > 0 &&
          (bp.classList.contains('og-panel-full') ||
           bp.classList.contains('og-r43-full'))) {
        return false;
      }
      return true;
    } catch (e) { return false; }
  }

  function sizeCanvas(f) {
    var w = f.wrap.clientWidth || 0, h = f.wrap.clientHeight || 0;
    if (!w || !h) return false;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var W = Math.round(w * dpr), H = Math.round(h * dpr);
    if (f.canvas.width !== W || f.canvas.height !== H) {
      f.canvas.width = W; f.canvas.height = H;
    }
    return true;
  }

  function drawEye(f, eye, skin, cov) {
    var ctx = f.ctx, W = f.canvas.width, H = f.canvas.height;
    var cx = eye.cx * W, cy = eye.cy * H;
    var a = (EYE_W * W) / 2, b = (EYE_H * H) / 2;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(eye.tilt);
    /* the almond (the eye opening, a touch generous) */
    ctx.beginPath();
    ctx.moveTo(-a, 0);
    ctx.quadraticCurveTo(0, -2.1 * b, a, 0);
    ctx.quadraticCurveTo(0, 1.25 * b, -a, 0);
    ctx.closePath();
    ctx.clip();
    var edgeY = -1.2 * b + cov * (2.4 * b);
    var grad = ctx.createLinearGradient(0, -1.2 * b, 0, edgeY);
    grad.addColorStop(0, rgba(shade(skin, 1.07), 1));
    grad.addColorStop(0.6, rgba(skin, 1));
    grad.addColorStop(1, rgba(shade(skin, 0.78), 1));
    ctx.fillStyle = grad;
    ctx.fillRect(-a, -1.2 * b, 2 * a, edgeY + 1.2 * b);
    /* soft shadow just above the lash line (lid depth) */
    if (edgeY > -1.2 * b) {
      var sh = ctx.createLinearGradient(
        0, edgeY - 0.9 * b, 0, edgeY);
      sh.addColorStop(0, 'rgba(46,24,12,0)');
      sh.addColorStop(1, 'rgba(46,24,12,0.30)');
      ctx.fillStyle = sh;
      ctx.fillRect(-a, edgeY - 0.9 * b, 2 * a, 0.9 * b);
    }
    ctx.restore();
    /* the lash line rides the lid's edge */
    if (cov > 0.04) {
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(eye.tilt);
      ctx.beginPath();
      ctx.moveTo(-a * 0.98, edgeY - b * 0.05);
      ctx.quadraticCurveTo(0, edgeY + b * 0.38, a * 0.98,
        edgeY - b * 0.05);
      ctx.strokeStyle = rgba(pal.lash, 0.85);
      ctx.lineWidth = Math.max(1, H * 0.0055);
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.restore();
    }
  }

  function drawFace(f, cov) {
    var ctx = f.ctx;
    if (!ctx || !f.canvas.width) return;
    ctx.clearRect(0, 0, f.canvas.width, f.canvas.height);
    if (cov <= 0.01) return;
    drawEye(f, EYES[0], pal.skinL, cov);
    drawEye(f, EYES[1], pal.skinR, cov);
  }

  function coverageAt(ms) {
    if (ms < CLOSE_MS) {
      var p = ms / CLOSE_MS;
      return p * p * (3 - 2 * p);
    }
    if (ms < CLOSE_MS + HOLD_MS) return 1;
    var q = (ms - CLOSE_MS - HOLD_MS) / OPEN_MS;
    if (q >= 1) return 0;
    return Math.pow(1 - q, 2);
  }

  function animate(f) {
    var t0 = performance.now();
    var step = function () {
      f.raf = 0;
      var cov = coverageAt(performance.now() - t0);
      /* ride the breath with the artwork (see header) */
      try {
        f.canvas.style.transform =
          f.img.style.transform || '';
      } catch (e) { }
      state.coverage = cov;
      drawFace(f, cov);
      if (cov > 0 || performance.now() - t0 <
            CLOSE_MS + HOLD_MS + OPEN_MS) {
        f.raf = requestAnimationFrame(step);
      } else {
        state.coverage = 0;
        f.canvas.style.transform = '';
      }
    };
    f.raf = requestAnimationFrame(step);
  }

  function schedule(f, delay, isDouble) {
    f.timer = setTimeout(function () {
      f.timer = 0;
      fire(f, isDouble);
    }, delay);
  }

  function fire(f, isDouble) {
    if (state.reduced || document.hidden) {
      schedule(f, rr(1200, 2600), false);
      return;
    }
    if (!eligible(f)) {
      schedule(f, rr(900, 1800), false);
      return;
    }
    if (!f.sampled) {
      f.sampled = true;
      samplePalette(f.img);
    }
    if (!sizeCanvas(f)) {
      schedule(f, rr(900, 1800), false);
      return;
    }
    var now = performance.now();
    if (f.lastStart) {
      var gap = (now - f.lastStart) / 1000;
      state.intervals.push(+gap.toFixed(3));
      if (state.intervals.length > 20) {
        state.intervals.shift();
      }
      state.lastGap = +gap.toFixed(3);
    }
    f.lastStart = now;
    state.blinks++;
    if (isDouble) state.doubles++;
    animate(f);
    if (!isDouble && Math.random() < DOUBLE_P) {
      schedule(f, rr(240, 420), true);
    } else {
      schedule(f, rr(GAP_MIN, GAP_MAX), false);
    }
  }

  function stopAll() {
    faces.forEach(function (f) {
      if (f.timer) clearTimeout(f.timer);
      if (f.raf) cancelAnimationFrame(f.raf);
      f.timer = 0; f.raf = 0;
      if (f.ctx) {
        f.ctx.clearRect(0, 0, f.canvas.width,
          f.canvas.height);
      }
      f.canvas.style.transform = '';
    });
    state.coverage = 0;
  }

  function startAll() {
    faces.forEach(function (f) {
      if (!f.timer && !f.raf) schedule(f, rr(1000, 2600),
        false);
    });
  }

  function init() {
    try { build(); } catch (e) { }
    if (!faces.length) return;
    startAll();
    document.addEventListener('visibilitychange',
      function () {
        if (document.hidden) {
          faces.forEach(function (f) {
            if (f.timer) clearTimeout(f.timer);
            f.timer = 0;
          });
        } else { startAll(); }
      });
    if (mq && mq.addEventListener) {
      mq.addEventListener('change', function (e) {
        if (e.matches) {
          state.reduced = true;
          stopAll();
        }
      });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();

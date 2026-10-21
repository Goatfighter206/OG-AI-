/* Ticket H — OG's OWN code rain (OG W background layer).
 *
 * Brent's rule (2026-10-19): "it needs to be its own unique
 * thing" — NOT a Matrix copy. What makes this OG's and not
 * the movie's, by construction:
 *
 * GLYPHS — the rain speaks OG's trade, not katakana. The
 * pool is built ONLY from four OG groups: the wordmark
 * letters O and G, the digits 0-9 (money, counts, code),
 * trade marks ($ # % @ & §), and job-site geometry
 * (▲ ▼ ◆ ● ■ ★ ✦ — triangles, diamonds, blocks, a star).
 * There is no kana of any kind anywhere in the table.
 *
 * COLOR — Brent's black + gold. Trails run deep amber to
 * antique gold (#6e5510 -> #d4af37); the head of each fall
 * is pale gold (#ffdf8a). No green exists in this file.
 *
 * RHYTHM — Matrix rain is a uniform wall rushing at one
 * speed. OG's falls clock in like a crew: columns arrive
 * in squads of 3-5 sharing a speed tier, fall at a slow
 * drizzle (never a rush), run SHORT trails (8-15 glyphs),
 * and clock out for a beat when they finish — the field
 * breathes instead of walling. A slow brightness swell
 * drifts sideways across the columns (~18 s period).
 *
 * SURFACE DISCIPLINE — one fixed canvas, z-index -1 (the
 * .animated-bg pattern this page already ships), pointer-
 * events none: it is a background, never a tap target,
 * never a stacked page. It carries no OG face or avatar.
 * While it runs it owns the backdrop (see the surface
 * contract at the style injection): the legacy nebula
 * layer retires and the chat column wears a 55% veil, so
 * what Brent sees behind OG is the rain, on-palette.
 * A Ticket G hook (<html data-og-rain-mode="day|night">,
 * or OGRAIN.setMode) re-treats the rain per theme without
 * touching this file's logic.
 *
 * PERFORMANCE — full redraw per frame from column state
 * (no fade accumulation, so brightness is bounded and the
 * readability math in the gate is exact). FPS capped
 * (30 full / 18 trimmed), DPR capped (1.5 / 1), pauses
 * when the tab hides, freezes to a still under
 * prefers-reduced-motion, trims on low-power signals
 * (few cores, little device memory, save-data, low
 * battery). Kill switch: localStorage ogRain="off" or
 * ?rain=0 — the module then never creates the canvas.
 * The whole module is failure-silent: it can never throw
 * into the page.
 */
(function () {
  'use strict';
  try {

  /* ---------------- glyph table (OG's own) ---------------- */
  var GLYPH_GROUPS = {
    word:   'OG',
    digits: '0123456789',
    marks:  '$#%@&\u00A7',
    shapes: '\u25B2\u25BC\u25C6\u25CF\u25A0\u2605\u2726'
  };
  /* Wordmark letters ride double weight — OG signs his rain. */
  var GLYPH_POOL = GLYPH_GROUPS.word + GLYPH_GROUPS.word +
                   GLYPH_GROUPS.digits + GLYPH_GROUPS.marks +
                   GLYPH_GROUPS.shapes;

  /* ---------------- palette (black + gold, no green) ---- */
  var HEAD_RGB  = [255, 223, 138];  /* pale gold head      */
  var TRAIL_HI  = [212, 175, 55];   /* antique gold #d4af37 */
  var TRAIL_LO  = [110, 85, 16];    /* deep amber  #6e5510  */
  var HEAD_ALPHA = 0.78;           /* caps, before canvas  */
  var TRAIL_ALPHA = 0.52;          /* opacity + mode mult  */
  var CANVAS_OPACITY_NIGHT = 0.85;
  var CANVAS_OPACITY_DAY = 0.30;

  var FONT = '15px "Share Tech Mono", ui-monospace, Menlo, Consolas, monospace';
  var CELL_H = 18;

  /* ---------------- profile (pure, gate-probeable) ------ */
  function OG_RAIN_PROFILE(env) {
    env = env || {};
    if (env.reducedMotion) {
      return { mode: 'static', trimmed: true, fps: 0,
               colGap: 22, dprCap: 1 };
    }
    var lowPower = !!env.saveData ||
      (env.cores > 0 && env.cores <= 2) ||
      (env.deviceMemory > 0 && env.deviceMemory <= 2) ||
      !!env.batteryLow;
    if (lowPower) {
      return { mode: 'anim', trimmed: true, fps: 18,
               colGap: 22, dprCap: 1 };
    }
    return { mode: 'anim', trimmed: false, fps: 30,
             colGap: 17, dprCap: 1.5 };
  }

  function liveEnv() {
    var nav = window.navigator || {};
    var env = {
      cores: nav.hardwareConcurrency || 0,
      deviceMemory: nav.deviceMemory || 0,
      saveData: !!(nav.connection && nav.connection.saveData),
      batteryLow: false,
      reducedMotion: false,
      width: window.innerWidth || 0
    };
    try {
      env.reducedMotion = !!window.matchMedia(
        '(prefers-reduced-motion: reduce)').matches;
    } catch (e) {}
    return env;
  }

  /* ---------------- kill switch -------------------------- */
  function killed() {
    try {
      if (/[?&]rain=0(?:&|$)/.test(window.location.search || '')) {
        return true;
      }
      return window.localStorage &&
             window.localStorage.getItem('ogRain') === 'off';
    } catch (e) { return false; }
  }

  /* ---------------- state -------------------------------- */
  var canvas = null, ctx = null, rafId = 0, timerId = 0;
  var columns = [], W = 0, H = 0, lastTs = 0, carryMs = 0;
  var profile = OG_RAIN_PROFILE(liveEnv());
  var running = false, pausedReason = null, frames = 0;
  var mode = 'night';
  try {
    mode = document.documentElement.getAttribute('data-og-rain-mode') ||
           'night';
  } catch (e) {}

  function alphaMult() { return mode === 'day' ? 0.5 : 1.0; }

  function pickGlyph() {
    return GLYPH_POOL.charAt(
      Math.floor(Math.random() * GLYPH_POOL.length));
  }

  function makeColumn(x, squadSpeed, warm) {
    var len = 8 + Math.floor(Math.random() * 8);      /* 8-15 */
    var glyphs = [];
    for (var i = 0; i < len; i++) glyphs.push(pickGlyph());
    /* Warm start: the first field is dealt along the whole
       fall path, so the rain is alive from the first frame
       — a background that takes a minute to arrive reads
       as broken. Respawns (warm=false) enter above the
       screen and keep the clock-in rhythm. */
    var y = warm
      ? -len * CELL_H + Math.random() * (H + len * CELL_H)
      : -Math.random() * H * 0.6 - len * CELL_H;
    return {
      x: x, y: y,
      speed: squadSpeed * (0.9 + Math.random() * 0.2),
      len: len, glyphs: glyphs,
      idle: 0, phase: Math.random() * Math.PI * 2
    };
  }

  function buildColumns() {
    columns = [];
    var gap = profile.colGap;
    var count = Math.max(1, Math.floor(W / gap));
    var squadLeft = 0, squadSpeed = 0;
    for (var i = 0; i < count; i++) {
      if (squadLeft <= 0) {                 /* a new squad clocks in */
        squadLeft = 3 + Math.floor(Math.random() * 3);   /* 3-5 */
        squadSpeed = 24 + Math.random() * 36;            /* 24-60 px/s */
      }
      squadLeft--;
      columns.push(makeColumn((i + 0.5) * gap, squadSpeed, true));
    }
  }

  var curDpr = 1;
  function resize() {
    if (!canvas) return;
    W = window.innerWidth || 0;
    H = window.innerHeight || 0;
    var dpr = Math.min(window.devicePixelRatio || 1, profile.dprCap);
    curDpr = dpr;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildColumns();
  }

  function rgba(rgb, a) {
    return 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' +
           Math.max(0, Math.min(1, a)).toFixed(3) + ')';
  }

  function drawFrame(nowMs) {
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    ctx.font = FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    var mult = alphaMult();
    var swell = 0.92 + 0.08 * Math.sin(nowMs / 9000);
    for (var c = 0; c < columns.length; c++) {
      var col = columns[c];
      if (col.idle > 0) continue;
      var colSwell = swell * (0.9 + 0.1 *
        Math.sin(nowMs / 9000 + col.phase));
      for (var i = 0; i < col.len; i++) {
        var gy = col.y - i * CELL_H;
        if (gy < -CELL_H || gy > H) continue;
        var t = col.len <= 1 ? 0 : i / (col.len - 1);
        var a, rgb;
        if (i === 0) {
          a = HEAD_ALPHA * colSwell * mult;
          rgb = HEAD_RGB;
        } else {
          a = TRAIL_ALPHA * Math.pow(1 - t, 1.6) * colSwell * mult;
          rgb = [
            Math.round(TRAIL_LO[0] + (TRAIL_HI[0] - TRAIL_LO[0]) * (1 - t)),
            Math.round(TRAIL_LO[1] + (TRAIL_HI[1] - TRAIL_LO[1]) * (1 - t)),
            Math.round(TRAIL_LO[2] + (TRAIL_HI[2] - TRAIL_LO[2]) * (1 - t))
          ];
        }
        if (a <= 0.004) continue;
        ctx.fillStyle = rgba(rgb, a);
        ctx.fillText(col.glyphs[i], col.x, gy);
      }
    }
    frames++;
  }

  function step(ts) {
    rafId = 0;
    if (!running) return;
    if (!lastTs) lastTs = ts;
    var dt = ts - lastTs;
    lastTs = ts;
    var frameMs = 1000 / Math.max(1, profile.fps);
    carryMs += dt;
    if (carryMs >= frameMs) {
      var adv = Math.min(carryMs, 250) / 1000;   /* seconds, clamped */
      carryMs = 0;
      for (var c = 0; c < columns.length; c++) {
        var col = columns[c];
        if (col.idle > 0) {
          col.idle -= adv;
          if (col.idle <= 0) {                    /* clock back in */
            col.y = -col.len * CELL_H - Math.random() * 40;
            col.speed = (24 + Math.random() * 36);
            for (var g = 0; g < col.len; g++) {
              col.glyphs[g] = pickGlyph();
            }
          }
          continue;
        }
        col.y += col.speed * adv;
        if (Math.random() < 0.02) {               /* subtle churn */
          col.glyphs[Math.floor(Math.random() * col.len)] = pickGlyph();
        }
        if (col.y - col.len * CELL_H > H) {       /* shift finished */
          col.idle = 0.4 + Math.random() * 2.4;   /* a beat of dark */
        }
      }
      drawFrame(ts);
      /* DPR can settle after first paint (display zoom,
         moving between screens): re-fit when it moves. */
      if (frames % 30 === 0 &&
          Math.min(window.devicePixelRatio || 1, profile.dprCap)
            !== curDpr) resize();
    }
    if (running) rafId = window.requestAnimationFrame(step);
  }

  function start() {
    if (!canvas || running || profile.mode !== 'anim') return;
    if (document.hidden) { pausedReason = 'hidden'; return; }
    running = true;
    pausedReason = null;
    lastTs = 0; carryMs = 0;
    rafId = window.requestAnimationFrame(step);
  }

  function stop(reason) {
    running = false;
    pausedReason = reason || null;
    if (rafId) { window.cancelAnimationFrame(rafId); rafId = 0; }
  }

  function applyProfile() {
    profile = OG_RAIN_PROFILE(liveEnv());
    if (canvas) {
      canvas.style.opacity = String(
        mode === 'day' ? CANVAS_OPACITY_DAY : CANVAS_OPACITY_NIGHT);
    }
    if (profile.mode === 'static') {
      stop('reduced-motion');
      resize();
      drawFrame(0); drawFrame(16); drawFrame(32);  /* a still field */
      return;
    }
    resize();
    start();
  }

  /* ---------------- theme hook (Ticket G's door) --------- */
  function setMode(next) {
    if (next !== 'day' && next !== 'night') return;
    mode = next;
    try {
      document.documentElement.setAttribute('data-og-rain-mode', mode);
    } catch (e) {}
    if (canvas) {
      canvas.style.opacity = String(
        mode === 'day' ? CANVAS_OPACITY_DAY : CANVAS_OPACITY_NIGHT);
    }
  }

  /* ---------------- boot ---------------------------------- */
  function boot() {
    if (killed()) return;
    if (!document.body) return;
    canvas = document.createElement('canvas');
    canvas.id = 'ogRainCanvas';
    canvas.setAttribute('aria-hidden', 'true');
    var style = document.createElement('style');
    style.id = 'og-rain-css';
    /* Surface contract (proven in the H lab — three
       separate burials, each measured): (1) <body> is
       position:relative with z-index auto, so its opaque
       background paints in the positioned layer, ABOVE a
       negative-z child — the canvas sat under the body's
       own paint (the shipped .animated-bg orbs die the
       same death). Giving body z-index:0 while the rain
       runs makes it a stacking context, so the canvas
       resolves inside it: above the body background,
       below every content child. (2) The chat column's own
       background is opaque #101010 and would bury the
       layer anyway. (3) The legacy .animated-bg nebula
       (orange/purple/blue) shares the z-plane and floods
       through any opening off-palette. So while the rain
       runs it OWNS the backdrop: body becomes a context,
       the nebula retires, and the chat column wears a 55%
       veil of its own near-black — the rain reads clearly
       in the open field and stays dimmed under the reading
       column (the gate measures text contrast over exactly
       this composite). Kill the rain (ogRain=off) and this
       style is never injected: the page is byte-for-byte
       its old self. */
    style.textContent =
      '#ogRainCanvas{position:fixed;inset:0;width:100%;height:100%;' +
      'z-index:-1;pointer-events:none;display:block;border:0;' +
      'margin:0;padding:0}' +
      'body{position:relative;z-index:0}' +
      '.animated-bg{display:none}' +
      'body .chat-container{background:rgba(10,10,10,0.55)}';
    document.body.insertBefore(style, document.body.firstChild);
    document.body.insertBefore(canvas, document.body.firstChild);
    canvas.style.opacity = String(
      mode === 'day' ? CANVAS_OPACITY_DAY : CANVAS_OPACITY_NIGHT);
    try { ctx = canvas.getContext('2d'); } catch (e) { ctx = null; }
    if (!ctx) { stop('no-2d-context'); return; }

    var resizeTimer = 0;
    window.addEventListener('resize', function () {
      if (resizeTimer) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        resizeTimer = 0;
        var wasRunning = running;
        if (wasRunning) stop('resize');
        resize();
        if (profile.mode === 'anim' && !document.hidden) start();
      }, 150);
    });

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { stop('hidden'); }
      else if (profile.mode === 'anim') { start(); }
    });
    window.addEventListener('pagehide', function () { stop('pagehide'); });

    if (window.navigator && window.navigator.getBattery) {
      try {
        window.navigator.getBattery().then(function (batt) {
          var check = function () {
            var low = batt && !batt.charging && batt.level <= 0.2;
            var env = liveEnv();
            env.batteryLow = !!low;
            var next = OG_RAIN_PROFILE(env);
            if (next.trimmed !== profile.trimmed) applyProfile();
          };
          batt.addEventListener('levelchange', check);
          batt.addEventListener('chargingchange', check);
          check();
        }).catch(function () {});
      } catch (e) {}
    }

    applyProfile();
  }

  window.OGRAIN = {
    version: 1,
    GLYPHS: GLYPH_POOL,
    GLYPH_GROUPS: GLYPH_GROUPS,
    PALETTE: {
      head: HEAD_RGB, trailHi: TRAIL_HI, trailLo: TRAIL_LO,
      headAlpha: HEAD_ALPHA, trailAlpha: TRAIL_ALPHA,
      canvasOpacityNight: CANVAS_OPACITY_NIGHT,
      canvasOpacityDay: CANVAS_OPACITY_DAY
    },
    profile: OG_RAIN_PROFILE,
    setMode: setMode,
    stop: function () { stop('api'); },
    start: function () {
      if (profile.mode === 'anim') start();
    },
    state: function () {
      return {
        mode: profile.mode,
        theme: mode,
        running: running,
        pausedReason: pausedReason,
        trimmed: profile.trimmed,
        fps: profile.fps,
        columns: columns.length,
        frames: frames,
        canvas: !!canvas,
        killed: killed()
      };
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  } catch (e) {
    /* Failure-silent by design: a background must never
       break the page it decorates. */
  }
})();

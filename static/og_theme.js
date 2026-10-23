/* Ticket G (cand-g1) — AUTOMATIC light / dark for OG W.
 *
 * Brent's order (2026-10-19): OG W switches light in the
 * daytime and dark at night, AUTOMATICALLY by the
 * visitor's local time — no manual toggle needed, and
 * none is added: there is no settings surface, no
 * stored preference, nothing parked anywhere (No Junk
 * Drawer: the mode is derived state, recomputed from
 * the clock, never saved).
 *
 * The door already existed: og_rain.js ships the
 * Ticket G hook — <html data-og-rain-mode="day|night">
 * plus OGRAIN.setMode — and the rain re-treats itself
 * per mode (night: white-gold on black; day:
 * bronze-gold on the light page, constant within the
 * mode). What was missing, and what this file is:
 *   1. a PAGE theme keyed on that same attribute
 *      (static/og_theme.css — one attribute, so the
 *      page and the rain can never disagree);
 *   2. a CONTROLLER that decides the mode from the
 *      visitor's local clock and keeps it honest:
 *      applied at load, re-checked every 60 s, and
 *      re-checked the moment the tab becomes visible
 *      again (the phone-left-open-across-sunset case).
 *
 * Day window: 07:00-19:00 local. The pre-paint boot
 * stub in index_epic.html's <head> applies the same
 * rule before first paint (so a daytime visitor never
 * flashes the night page, and the rain boots straight
 * into the right mode); this controller is the
 * authority that keeps it true after load.
 *
 * A ?theme=day|night URL parameter overrides the
 * clock — a test/preview door in the same family as
 * the rain's ?rain=0 kill switch, not a UI toggle.
 *
 * Failure-silent, like the rain: a theme helper must
 * never break the page it dresses.
 *
 * Ticket G3 (cand-g3, Brent 2026-10-22 — "the daytime
 * and nighttime follow dusk till dawn"): the fixed
 * 07:00-19:00 boundaries are REPLACED by the visitor's
 * REAL sunrise and sunset — day from sunrise, night
 * from sunset, computed fresh for that day and place
 * by standard solar math (the SunCalc formulation:
 * Julian cycle, solar mean anomaly, hour angle at
 * -0.833 deg). Verified against authoritative Tacoma
 * times for 2026-10-23 (timeanddate + sunrise-sunset
 * .org: rise 07:40 / set 18:07 PDT): computed within
 * +0.6 / +1.8 minutes.
 *
 * WHICH LOCATION, AND WHY: the app's own GPS location
 * switch (og_gps.py, GET /gps/location — the per-account
 * "OG location" permission the user turns on in
 * Permissions/Settings and OG never turns on himself).
 * It is the one standing, explicit location consent the
 * app legitimately holds. If — and only if — that
 * switch is ON, this controller takes ONE browser
 * geolocation fix (the same navigator.geolocation the
 * driving view and the near-me flow already use),
 * computes the day's two times from it IN THE TAB, and
 * forgets the coordinates when the tab closes: they are
 * kept in a JS variable only — never stored, never
 * sent anywhere, no new tracking surface (with the
 * switch off, geolocation is never even called).
 * The weather pack holds no visitor coordinates (it
 * resolves place NAMES) and saved GPS site points are
 * places, not the visitor — neither is an honest
 * stand-in, so neither is used.
 *
 * FALLBACK: switch off / signed out / fetch or fix
 * fails / polar day-night (no sunrise that day) ->
 * the original 07:00-19:00 clock rule, exactly as
 * before. The pre-paint stub still paints that
 * fallback (or the ?theme= override); the moment the
 * solar times resolve, apply() corrects the face —
 * no reload, no persistent wrong face.
 */
(function () {
  'use strict';
  try {

  var DAY_START = 7;   /* fallback rule: 07:00 local */
  var DAY_END = 19;    /* fallback rule: 19:00 local */

  function fromClock(now) {
    var h = now.getHours();
    return (h >= DAY_START && h < DAY_END) ? 'day' : 'night';
  }

  function override() {
    try {
      var m = /[?&]theme=(day|night)(?:&|$)/.exec(
        window.location.search || '');
      return m ? m[1] : null;
    } catch (e) { return null; }
  }

  /* ------------- solar math (SunCalc form.) ------- */
  var RAD = Math.PI / 180, DAY_MS = 86400000,
      J1970 = 2440588, J2000 = 2451545,
      OBLIQ = RAD * 23.4397;

  function toDays(date) {
    return date.valueOf() / DAY_MS - 0.5 + J1970 - J2000;
  }
  function solarTransitJ(ds, M, L) {
    return J2000 + ds + 0.0053 * Math.sin(M)
         - 0.0069 * Math.sin(2 * L);
  }
  /* {sunrise, sunset} Date pair for the solar cycle
     whose transit is nearest `date`, or null when the
     sun never crosses -0.833 deg that cycle (polar
     day/night) — the caller falls back to the clock. */
  function sunPairFor(date, lat, lng) {
    var lw = RAD * -lng, phi = RAD * lat;
    var n = Math.round(toDays(date) - 0.0009 - lw / (2 * Math.PI));
    var ds = 0.0009 + lw / (2 * Math.PI) + n;
    var M = RAD * (357.5291 + 0.98560028 * ds);
    var C = RAD * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M)
                   + 0.0003 * Math.sin(3 * M));
    var L = M + C + RAD * 102.9372 + Math.PI;
    var dec = Math.asin(Math.sin(L) * Math.sin(OBLIQ));
    var Jnoon = solarTransitJ(ds, M, L);
    var cosH = (Math.sin(-0.833 * RAD) - Math.sin(phi) * Math.sin(dec))
             / (Math.cos(phi) * Math.cos(dec));
    if (!(cosH >= -1 && cosH <= 1)) return null;
    var w = Math.acos(cosH);
    var Jset = solarTransitJ(ds + w / (2 * Math.PI), M, L);
    var Jrise = Jnoon - (Jset - Jnoon);
    var rise = new Date((Jrise + 0.5 - J1970) * DAY_MS);
    var set = new Date((Jset + 0.5 - J1970) * DAY_MS);
    if (!(set.getTime() > rise.getTime())) return null;
    return { sunrise: rise, sunset: set };
  }

  /* ------------- the solar basis ------------------- */
  var coords = null;        /* {lat, lng} — tab memory only */
  var pair = null;          /* {dayKey, riseMs, setMs} */
  var basisName = 'clock';  /* 'clock' | 'sun' */

  function dayKey(d) {
    return d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate();
  }
  function fromSun(now) {
    if (!coords) return null;
    var key = dayKey(now);
    if (!pair || pair.dayKey !== key) {
      var p = sunPairFor(now, coords.lat, coords.lng);
      if (!p) { pair = null; return null; }
      pair = { dayKey: key, riseMs: p.sunrise.getTime(),
               setMs: p.sunset.getTime() };
    }
    var ms = now.getTime();
    return (ms >= pair.riseMs && ms < pair.setMs) ? 'day' : 'night';
  }

  function resolve() {
    var ov = override();
    if (ov) return ov;
    var now = new Date();
    return fromSun(now) || fromClock(now);
  }

  function apply() {
    var mode = resolve();
    var de = document.documentElement;
    if (de.getAttribute('data-og-rain-mode') !== mode) {
      de.setAttribute('data-og-rain-mode', mode);
    }
    if (window.OGRAIN && window.OGRAIN.state &&
        window.OGRAIN.state().theme !== mode) {
      window.OGRAIN.setMode(mode);
    }
    return mode;
  }

  apply();
  window.setInterval(apply, 60000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) apply();
  });

  /* ---- G3: resolve the location ONCE, in the moment.
     Only under the account's own GPS location switch
     (see the header): switch off/absent -> geolocation
     is never called and the clock rule stands. Any
     failure anywhere keeps the clock rule. Skipped
     entirely under a ?theme= override (the door is
     supreme and needs no sky). */
  (function resolveLocation() {
    try {
      if (override()) return;
      if (!window.fetch || !navigator.geolocation) return;
      window.fetch('/gps/location', { credentials: 'same-origin' })
        .then(function (r) { return (r && r.ok) ? r.json() : null; })
        .then(function (data) {
          if (!data || data.on !== true) return;
          navigator.geolocation.getCurrentPosition(
            function (pos) {
              try {
                if (!pos || !pos.coords) return;
                coords = { lat: pos.coords.latitude,
                           lng: pos.coords.longitude };
                if (fromSun(new Date())) basisName = 'sun';
                apply();   /* the correction */
              } catch (e) { /* failure-silent */ }
            },
            function () { /* denied/unavailable: clock stands */ },
            { timeout: 10000, maximumAge: 3600000,
              enableHighAccuracy: false });
        })
        .catch(function () { /* failure-silent */ });
    } catch (e) { /* failure-silent */ }
  })();

  /* Gate-probeable, in the OGRAIN style. */
  window.OGTHEME = {
    version: 2,
    dayStart: DAY_START,
    dayEnd: DAY_END,
    mode: resolve,
    fromClock: function () { return fromClock(new Date()); },
    fromSun: function () { return fromSun(new Date()); },
    basis: function () { return basisName; },
    sunTimes: function () {
      if (!pair) return null;
      return { sunrise: pair.riseMs, sunset: pair.setMs };
    },
    override: override,
    apply: apply
  };

  } catch (e) {
    /* Failure-silent by design. */
  }
})();

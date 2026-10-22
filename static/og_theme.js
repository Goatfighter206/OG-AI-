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
 */
(function () {
  'use strict';
  try {

  var DAY_START = 7;   /* 07:00 local */
  var DAY_END = 19;    /* 19:00 local */

  function fromClock() {
    var h = new Date().getHours();
    return (h >= DAY_START && h < DAY_END) ? 'day' : 'night';
  }

  function override() {
    try {
      var m = /[?&]theme=(day|night)(?:&|$)/.exec(
        window.location.search || '');
      return m ? m[1] : null;
    } catch (e) { return null; }
  }

  function resolve() { return override() || fromClock(); }

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

  /* Gate-probeable, in the OGRAIN style. */
  window.OGTHEME = {
    version: 1,
    dayStart: DAY_START,
    dayEnd: DAY_END,
    mode: resolve,
    fromClock: fromClock,
    override: override,
    apply: apply
  };

  } catch (e) {
    /* Failure-silent by design. */
  }
})();

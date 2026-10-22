/* Ticket N2 — Settings consolidation (Brent, 2026-10-21:
   "in the menu area I want anything that's a setting to
   go into the settings icon").

   Every settings-shaped row OG already ships — Voice,
   Shit-talking, Security, Notification settings (rows the
   round modules build into the menu's Account card), the
   Permissions and Languages cards (their own sections),
   and the Location toggle (static, ex-Tools) — lives
   behind ONE door now: the ⚙ Settings row in the menu's
   Settings section, opening the #ogSettings page.

   HOW, and why this shape: the rows keep their OWN
   builders, handlers, fetches and state — this module
   only MOVES the finished DOM nodes into the settings
   page's containers, in a fixed order, and keeps
   re-seating them (a DOM move keeps every listener).
   Nothing is re-implemented, so no setting can be lost
   or drift: if a builder ever stops building its row,
   the row simply isn't here — the census gate reads the
   same ids this file moves.

   Visibility contract (unchanged from the Account-card
   era): account settings show only while signed in —
   this module mirrors the Account section's hidden
   state onto the moved rows/sections on every pass.
   The Location row is a per-session consent, reachable
   signed out too, exactly as it was in Tools.

   Defensive by contract: feature-detected, try/caught,
   never throws. */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  /* Canonical order on the settings page. */
  var ROW_IDS = ['og98VoiceRow', 'og84ShittalkRow',
                 'og91SecurityRow', 'menuNotifSettingsBtn'];
  var SECTION_IDS = ['ogPermCard', 'ogLangCard'];

  function signedIn() {
    var sec = $('menuAccountSec');
    return !!(sec && !sec.hidden);
  }

  function moveInto(node, parent) {
    if (node && parent && node.parentNode !== parent) {
      parent.appendChild(node);
    } else if (node && parent) {
      parent.appendChild(node); /* re-append = canonical order */
    }
  }

  function seat() {
    var card = $('ogSettingsCard');
    var secs = $('ogSettingsSections');
    if (!card || !secs) return;
    var inNow = signedIn();
    ROW_IDS.forEach(function (id) {
      var n = $(id);
      if (n) { moveInto(n, card); n.hidden = !inNow; }
    });
    SECTION_IDS.forEach(function (id) {
      var n = $(id);
      if (!n) return;
      /* A section's own h3 head rides with it (the
         Permissions card's "Phone" head is a sibling). */
      var prev = n.previousElementSibling;
      if (prev && prev.tagName === 'H3'
          && prev.classList.contains('og-menu-sec')
          && prev.parentNode !== secs) {
        moveInto(prev, secs);
        prev.hidden = !inNow;
      }
      moveInto(n, secs);
      n.hidden = !inNow;
    });
    card.hidden = !inNow;
    secs.hidden = !inNow;
    var note = $('ogSettingsAnonNote');
    if (note) note.hidden = inNow;
  }

  function openSettings() {
    var mc = $('menuClose');
    if (mc) mc.click();
    var scrim = $('ogSettingsScrim');
    var panel = $('ogSettings');
    if (scrim) scrim.hidden = false;
    if (panel) panel.hidden = false;
    seat();
  }

  function closeSettings() {
    var scrim = $('ogSettingsScrim');
    var panel = $('ogSettings');
    if (scrim) scrim.hidden = true;
    if (panel) panel.hidden = true;
  }

  function start() {
    var door = $('menuSettingsBtn');
    if (door) door.addEventListener('click', openSettings);
    var close = $('ogSettingsClose');
    if (close) close.addEventListener('click', closeSettings);
    var scrim = $('ogSettingsScrim');
    if (scrim) scrim.addEventListener('click', closeSettings);
    seat();
    /* The round builders land their rows on their own
       cadences (200 ms – 1.5 s polls, some for minutes);
       keep seating until long after the slowest. */
    var tries = 0;
    var iv = setInterval(function () {
      tries += 1;
      try { seat(); } catch (e) { /* next pass */ }
      if (tries > 1200) clearInterval(iv);
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

/* Round 275 — the header bubble's live helper line
   (Brent, 2026-10-13: "the online and ready should
   be showing what he doing or what he has he's
   helpers doing").

   The name OG and the status line now live in ONE
   gold bubble (page markup, og-r275-css). This asset
   is the bubble's activity layer: it polls
   GET /activity/status — the server-side activity
   source (og_activity.py: the last helper claim,
   recorded from the REAL routing result, + the
   caller's live background work read off the watch /
   monitor / crew stores) — and paints the composed
   line into the status pills (#ogTaskPillTop +
   #ogTaskPillProfile + #ogTaskPillHero, the shell
   coordinator's instances — the hero pill added by
   Ticket E3).

   PRIORITY LAW (never demote the truth):
   - This layer writes ONLY over the idle text
     ("Online & Ready") or over its own last write.
     Thinking… / Browsing <host> / Making your video,
     the needs-user states (approval > sign-in >
     browser — the shell coordinator paints those
     OVER the activity channel anyway), the voice
     states, and the signed-out wording are never
     touched: if the pill shows anything else, this
     layer's hands stay off until it returns to idle.
   - The pill elements' textContent is channelled by
     the shell's ONE writer (og_shell.js S4c): a write
     here becomes the coordinator's latest ACTIVITY
     text and a needs state still outranks it.
   - No text from the server = hands off; if this
     layer had painted and the activity clears, it
     restores the idle text itself. A 401 (signed
     out), an error, or the kill switch
     (enabled:false) all read the same: hands off.
   - The server NEVER sends fake activity (its
     honesty law); this layer adds none of its own:
     no rotation, no timers painting text that did
     not come from /activity/status on this poll. */
(function () {
  var IDLE = 'Online & Ready';
  var PILL_IDS = ['ogTaskPillTop', 'ogTaskPillProfile', 'ogTaskPillHero'];
  var mine = null; /* the text this layer last painted */

  function shown(el) {
    try { return el.textContent || ''; } catch (e) { return ''; }
  }

  function paint(text) {
    PILL_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var cur = shown(el);
      if (text) {
        if (cur === IDLE || (mine && cur === mine)) {
          try { el.textContent = text; } catch (e) { }
        }
      } else if (mine && cur === mine) {
        try { el.textContent = IDLE; } catch (e) { }
      }
    });
    mine = text || null;
  }

  function tick() {
    if (!window.fetch) return;
    fetch('/activity/status', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok || !d.enabled) { paint(null); return; }
        paint(typeof d.text === 'string' && d.text ? d.text : null);
      })
      .catch(function () { });
  }

  tick();
  setInterval(tick, 30000);
})();

/* Ticket E7 — OG performs (the page half).
   Brent's words (2026-10-21): sometimes when OG has been
   sitting a while he starts rapping his own songs he
   comes up with; and a user who is shit-talking all the
   time gets a SUNG roast back.

   WHAT THIS ASSET IS: the stage manager, never the
   judge. Every trigger number lives server-side in
   og_perform.py (the idle dice, the bounds, the roast
   count, the cooldowns — one record per account). This
   file only:
     - watches the page's own quiet: last interaction,
       tab visible, no chat turn running (isProcessing),
       no speech running (speakingNow), the hero pill at
       rest (its data-state, the shell's own busy word);
     - couriers each sent user message to /perform/note
       (one seam line in sendMessage) — the server judges
       it with the Round 65 detector; the page never
       decides what counts as an insult;
     - when the page is quiet AND the server says a
       performance is owed (POST /perform/claim), puts it
       on: the lyrics land as OG's own chat message and
       speakReply performs them in his real voice — the
       E4 mouth rides the same audio, exactly like a
       reply. /tts metering is the shipped weekly cap;
       nothing here speaks around it;
     - STOPS the show the moment the user engages: a tap,
       a key, the tab hiding, the page going away — the
       performance yields instantly (stopSpeaking, the
       page's own kill switch) and never resumes itself.
   It never fires during a chat turn, a job, or a browser
   task: those all hold the pill off rest / isProcessing
   up, and the tick below refuses to move while busy.
   One performance at a time; after a show the idle clock
   restarts from the applause, it does not encore.

   Autoplay honesty: a performance speaks through the
   same playBlob a reply uses. If the browser ever blocks
   the audio (no gesture yet this visit), the shipped
   speakReply surfaces its "tap to hear me" state and
   the lyrics are already on screen as a message — the
   song is never lost, it just waits for a tap.

   Defensive by contract (the og_r97 / og_mouth shape):
   page globals are read through try/catch (top-level
   `let` bindings are shared across classic scripts —
   the og_mouth precedent), everything is try/caught,
   this file never throws. window.OGPERFORM is the
   gate's probe, OGIDLE / OGMOUTH style. */
(function () {
  'use strict';
  if (window.OGPERFORM) return;

  var IDLE_AFTER_S = 240;      // 4 quiet minutes before the first ask
  var TICK_MS = 5000;
  var ROAST_LOCAL_TTL_MS = 30 * 60 * 1000;  // mirrors the server TTL

  var lastInteract = Date.now();
  var nextClaimAt = 0;         // server-driven idle backoff
  var performing = false;
  var claiming = false;
  var pendingRoast = false;
  var pendingRoastAt = 0;

  function apiBase() {
    try {
      return (typeof API_URL !== 'undefined' && API_URL) || '';
    } catch (e) { return ''; }
  }
  function flag(name) {
    try {
      if (name === 'isProcessing') return !!isProcessing;
      if (name === 'speakingNow') return !!speakingNow;
    } catch (e) { }
    return false;
  }
  function pillBusy() {
    try {
      var b = document.getElementById('ogHeroBubble');
      return !!b && b.getAttribute('data-state') === 'busy';
    } catch (e) { return false; }
  }
  function busy() {
    return flag('isProcessing') || flag('speakingNow') ||
           pillBusy() || document.hidden;
  }

  function markInteract() { lastInteract = Date.now(); }

  function stopPerformance() {
    if (!performing) return;
    performing = false;
    try { stopSpeaking(); } catch (e) { }
  }

  /* Engagement stops the show. These listeners only act
     while a performance is live; ordinary taps during an
     ordinary reply keep the page's shipped behavior. */
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (ev) {
    document.addEventListener(ev, function () {
      markInteract();
      if (performing) stopPerformance();
    }, true);
  });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stopPerformance();
  });
  window.addEventListener('pagehide', stopPerformance);

  function post(path, body) {
    return fetch(apiBase() + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  /* The courier: one user message, judged server-side. */
  function note(text) {
    post('/perform/note', { message: String(text || '') })
      .then(function (d) {
        if (d && d.roast_due) {
          pendingRoast = true;
          pendingRoastAt = Date.now();
        }
      });
  }

  function perform(d) {
    performing = true;
    var shown = false;
    try {
      addMessage('ai', '\u{1F3B5} "' + d.title + '"\n\n' + d.lyrics);
      shown = true;
    } catch (e) { }
    var done = function () {
      performing = false;
      lastInteract = Date.now();   /* the quiet clock restarts */
      nextClaimAt = Date.now() + 90000;
    };
    if (!shown) { done(); return Promise.resolve(); }
    var p;
    try { p = speakReply(d.speech || d.lyrics); }
    catch (e) { p = null; }
    if (p && p.then) return p.then(done, done);
    done();
    return Promise.resolve();
  }

  function tick() {
    if (performing || claiming) return;
    if (busy()) return;
    if (pendingRoast) {
      if (Date.now() - pendingRoastAt > ROAST_LOCAL_TTL_MS) {
        pendingRoast = false;
      } else {
        claiming = true;
        post('/perform/claim', { kind: 'roast' })
          .then(function (d) {
            claiming = false;
            if (d && d.ok) {
              pendingRoast = false;
              perform(d);
            } else if (d && (d.reason === 'not_armed' ||
                             d.reason === 'expired' ||
                             d.reason === 'register_off')) {
              pendingRoast = false;
            }
            /* cooldown / down: stays pending, a later
               tick asks again — the server owns the math */
          });
        return;
      }
    }
    var idleS = (Date.now() - lastInteract) / 1000;
    if (idleS >= IDLE_AFTER_S && Date.now() >= nextClaimAt) {
      claiming = true;
      post('/perform/claim', { kind: 'idle' })
        .then(function (d) {
          claiming = false;
          if (d && d.ok) { perform(d); return; }
          var retry = (d && d.retry_after_s) || 90;
          nextClaimAt = Date.now() +
            Math.max(90, retry) * 1000;
        });
    }
  }
  setInterval(tick, TICK_MS);

  window.OGPERFORM = {
    version: 1,
    note: note,
    state: function () {
      return { version: 1, performing: performing,
               claiming: claiming, pendingRoast: pendingRoast,
               busy: busy(),
               idle_s: Math.round(
                 (Date.now() - lastInteract) / 1000) };
    },
    _test: {
      setIdle: function (s) {
        lastInteract = Date.now() - s * 1000;
        nextClaimAt = 0;
      },
      tickNow: function () { tick(); },
      stop: stopPerformance
    }
  };
})();

/* Stop-listening on leave (Brent, 2026-10-10): "OG should
   stop listening when the user leaves the app or website
   because I notice he still listening in the background."
   Web side (the Unity side shipped as fix35).

   THE PAGE'S VOICE SHAPE (audited in index_epic.html — this
   asset changes none of it): one global SpeechRecognition
   object (`recognition`, continuous=false,
   interimResults=false), Talk mode = the `voiceChatOn`
   flag toggled by the #voiceBtn click handler, and an
   onend AUTO-RESTART LOOP (onend -> if voiceChatOn ->
   setTimeout(startListening, 300)). The page had NO
   visibilitychange / pagehide / blur handling for voice,
   so Talk kept re-listening in a background tab forever.

   WHAT THIS ASSET DOES, on leave (visibility hidden,
   pagehide, or window blur):
   1. Sets the LEFT LATCH (window.__ogStopListenLeft).
   2. recognition.abort() — abort, not stop: stop() lets a
      pending final result still fire, abort() DISCARDS
      it. There is no interim/composer dictation buffer
      (interimResults=false; a final result lands in the
      composer and sends in the same handler), so result
      delivery IS the discard point — and the wrapped
      onresult below swallows any result that still
      arrives while the latch is set: never inserted,
      never sent. The composer itself is never touched
      (it may hold the user's own typed text).
   3. Turns Talk OFF through the page's OWN tap path:
      #voiceBtn.click() runs the exact OFF branch a user
      tap runs (flag off, 'recording' class off,
      recognition.stop(), stopSpeaking(), status line).
      No parallel state is invented. If that path somehow
      leaves the flag on, a fallback mirrors the OFF
      branch's effects directly.
   4. The wrapped onend consults the latch BEFORE the
      page's original handler, so the restart loop cannot
      resurrect listening after a leave — and even if a
      restart timer was already queued, the page's own
      startListening re-checks voiceChatOn (now off) and
      no-ops.

   ON RETURN (visible / pageshow / focus): NOTHING
   restarts. The latch stays set and Talk stays off until
   the user taps Talk again — a trusted click on
   #voiceBtn is the ONLY latch clear in this file.

   Defensive by contract: every page global is read
   through try/catch (they are top-level `let` bindings —
   the og_r62.js precedent), everything is feature-
   detected, and when no recognition exists the leave
   path is a pure no-op. This file never throws. */
(function () {
  'use strict';

  var leftPage = false;
  var wrapped = false;
  var sessionActive = false; // tracked via the wrapped onstart/onend

  function getRecognition() {
    try { return recognition; } catch (e) { return null; }
  }
  function talkIsOn() {
    try { return !!voiceChatOn; } catch (e) { return false; }
  }
  function voiceButton() {
    try { return document.getElementById('voiceBtn'); }
    catch (e) { return null; }
  }

  // Wrap the page recognition's result/end handlers so the
  // latch is consulted first. Delegates to whatever handler
  // was there (the page's, or og_r62's approval wrap if it
  // landed first — either wrap order is safe because both
  // delegate only when their own gate passes).
  function wrapRecognition() {
    if (wrapped) return true;
    var rec = getRecognition();
    if (!rec) return false;
    if (rec.__ogStopListenWrapped) { wrapped = true; return true; }
    var origStart = rec.onstart;
    var origResult = rec.onresult;
    var origEnd = rec.onend;
    try {
      rec.onstart = function (event) {
        sessionActive = true;
        if (typeof origStart === 'function') {
          return origStart.call(rec, event);
        }
      };
      rec.onresult = function (event) {
        if (leftPage) return; // discarded: not inserted, not sent
        if (typeof origResult === 'function') {
          return origResult.call(rec, event);
        }
      };
      rec.onend = function (event) {
        sessionActive = false;
        if (leftPage) return; // no auto-restart after a leave
        if (typeof origEnd === 'function') {
          return origEnd.call(rec, event);
        }
      };
      rec.__ogStopListenWrapped = true;
      wrapped = true;
      return true;
    } catch (e) { return false; }
  }

  function stopListeningOnLeave() {
    leftPage = true;
    try { window.__ogStopListenLeft = true; } catch (e) { }
    wrapRecognition();
    var rec = getRecognition();
    // Discard first: abort() drops any pending result. When
    // nothing is active and Talk is off this stays a no-op.
    if (rec && (sessionActive || talkIsOn())) {
      try {
        if (typeof rec.abort === 'function') rec.abort();
        else if (typeof rec.stop === 'function') rec.stop();
      } catch (e) {
        try { if (typeof rec.stop === 'function') rec.stop(); }
        catch (e2) { }
      }
    }
    if (!talkIsOn()) return; // idle leave: latch set, rest is a no-op
    // Talk OFF via the page's own tap path.
    var btn = voiceButton();
    if (btn && typeof btn.click === 'function') {
      try { btn.click(); } catch (e) { }
    }
    if (talkIsOn()) {
      // Fallback: mirror the OFF branch's effects directly.
      try { voiceChatOn = false; } catch (e) { }
      if (btn && btn.classList) {
        try { btn.classList.remove('recording'); } catch (e) { }
      }
      if (rec) {
        try { if (typeof rec.stop === 'function') rec.stop(); }
        catch (e) { }
      }
      try {
        if (typeof stopSpeaking === 'function') stopSpeaking();
      } catch (e) { }
      try {
        if (typeof updateStatus === 'function') {
          updateStatus('Voice chat OFF');
        }
      } catch (e) { }
    }
  }

  function onLeave() {
    try { stopListeningOnLeave(); } catch (e) { }
  }

  // ---- leave handlers ------------------------------------
  try {
    document.addEventListener('visibilitychange', function () {
      try { if (document.hidden) onLeave(); } catch (e) { }
      // visible: deliberately nothing — no restart, ever.
    });
  } catch (e) { }
  try { window.addEventListener('pagehide', onLeave); } catch (e) { }
  try { window.addEventListener('blur', onLeave); } catch (e) { }
  // Return handlers exist ONLY to pin the no-restart rule:
  // pageshow / focus perform no action at all.
  try { window.addEventListener('pageshow', function () { }); }
  catch (e) { }
  try { window.addEventListener('focus', function () { }); }
  catch (e) { }

  // ---- the ONLY latch clear: the user's own Talk tap -----
  // Capture phase on document runs before the button's own
  // handler, so a fresh ON tap starts with the latch clear
  // and its results flow. Programmatic clicks (isTrusted
  // false — including this module's own Talk-off click)
  // never clear it.
  try {
    document.addEventListener('click', function (ev) {
      try {
        if (!ev || ev.isTrusted !== true) return;
        var btn = voiceButton();
        if (!btn) return;
        var t = ev.target;
        if (t === btn || (t && btn.contains && btn.contains(t))) {
          leftPage = false;
          try { window.__ogStopListenLeft = false; } catch (e) { }
        }
      } catch (e) { }
    }, true);
  } catch (e) { }

  // recognition is created by the page's initVoice on
  // DOMContentLoaded; poll briefly to wrap it (the og_r62
  // pattern), then stop polling for good.
  if (!wrapRecognition()) {
    var tries = 0;
    var timer = setInterval(function () {
      tries++;
      if (wrapRecognition() || tries > 40) {
        try { clearInterval(timer); } catch (e) { }
      }
    }, 250);
  }

  try {
    window.__ogStopListen = {
      onLeave: stopListeningOnLeave,
      isLeft: function () { return leftPage; },
      wrap: wrapRecognition
    };
  } catch (e) { }
})();

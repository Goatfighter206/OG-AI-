/* Round 97: OG's voice or nothing + the real wave meter
   (Brent, 2026-10-12: "It's a different voice, not OG's" —
   the chat page's legacy device-voice speaker is DELETED
   from index_epic.html in this round; OG now speaks ONLY
   through POST /tts in his server voice, and a failed
   /tts means silence, never a substitute voice. Brent
   then approved the real wave meter 05:23 PDT: "Approve
   — add the real wave meter.")

   WHAT THIS ASSET IS: the live signal source on OG's
   REAL audio while he speaks. A Web Audio analyser is
   wired to the very media element that plays the /tts
   MP3 (the page's ogAudio() element, handed to start()
   by playBlob) — born as the visible wave meter (the
   bars were its face; Ticket E5 removed the face, see
   below) and kept as machinery: Ticket E4's mouth reads
   this analyser's level. It is live ONLY while that
   element is playing OG's voice — start() is called by
   playBlob at playback start, stop() on ended / error /
   play() rejection / stopSpeaking(). It is NOT a state
   decoration: no audio through the analyser, no level.

   Ticket E5 (Brent, 2026-10-21 17:10): "the vice bar
   that pops up underneath what he is doing needs to go
   away" — the VISIBLE meter (the #ogWaveMeter gold-bars
   box this file used to build into the top avatar bar
   and show while OG speaks) is REMOVED, root and
   branch: no box, no canvas, no bar drawing. What stays
   is the invisible half: the Web Audio analyser graph
   on OG's real voice element, because Ticket E4's
   mouth (og_mouth.js) reads its level — the signal
   source is machinery, not presentation. start()/stop()
   now only arm/disarm that graph; level()/active()
   keep their exact contract (0 / false unless speech
   is live through the graph).

   Wiring notes (the iOS-safe shape):
   - The AudioContext + MediaElementSource are created
     ONCE, lazily, on the first start() — that call rides
     a user-gesture-fed playback, so the context is
     allowed to run; resume() is attempted every start.
   - createMediaElementSource may be called only once
     per element for the page's life; the source is
     cached. The element is routed source -> analyser ->
     destination, so playback still reaches the speakers
     exactly as before.
   - If Web Audio is unavailable or the wiring throws,
     the meter stays dark and playback is untouched —
     the meter must never break OG's voice.

   Defensive by contract (the og_stoplisten.js shape):
   everything feature-detected, every entry point
   try/caught, this file never throws. */
(function () {
  'use strict';

  var actx = null;       // AudioContext (lazy, once)
  var analyser = null;   // AnalyserNode (lazy, once)
  var sourceEl = null;   // element the source was built from
  var running = false;

  function ensureGraph(el) {
    if (!el) return false;
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return false;
      if (!actx) {
        actx = new AC();
        analyser = actx.createAnalyser();
        analyser.fftSize = 64;
        analyser.smoothingTimeConstant = 0.72;
      }
      if (sourceEl !== el) {
        var src = actx.createMediaElementSource(el);
        src.connect(analyser);
        analyser.connect(actx.destination);
        sourceEl = el;
      }
      if (actx.resume) { var p = actx.resume(); if (p && p.catch) p.catch(function () {}); }
      return true;
    } catch (e) { return false; }
  }

  function start(el) {
    try {
      if (!ensureGraph(el)) return;
      running = true;
    } catch (e) { /* the graph feeds the mouth only; never fatal */ }
  }

  function stop() {
    try {
      running = false;
    } catch (e) { /* never throws */ }
  }

  /* Fix B (2026-10-23): gesture priming. The graph used
     to be born inside playBlob — after the async /tts
     fetch, outside any user gesture — so on iOS WebKit
     the context started suspended and an out-of-gesture
     resume() could not run it: the voice element, routed
     through that graph, and the mouth's analyser both
     sat behind a context that never ran (the lab never
     saw it: Chromium forgives an out-of-gesture resume
     once the page has had any tap). prime() is the same
     ensureGraph, called from the page's gesture funnel
     (unlockAudio) so the context is created and resumed
     while a real tap is live; start() later only arms
     it. If Web Audio is unavailable prime is a no-op
     and playback stays exactly as it was. */
  function prime(el) {
    try { ensureGraph(el); } catch (e) { /* never throws */ }
  }

  /* Ticket E4: the mouth driver (og_mouth.js) reads the
     SAME analyser — a time-domain RMS of the real TTS
     signal, so the mouth tracks the actual speech
     envelope. Zero whenever the meter is not running:
     no audio through the graph, no level, mouth shut. */
  var tdata = null;
  function level() {
    try {
      if (!analyser || !running) return 0;
      if (!tdata || tdata.length !== analyser.fftSize) {
        tdata = new Uint8Array(analyser.fftSize);
      }
      analyser.getByteTimeDomainData(tdata);
      var s = 0;
      for (var i = 0; i < tdata.length; i++) {
        var v = (tdata[i] - 128) / 128;
        s += v * v;
      }
      return Math.sqrt(s / tdata.length);
    } catch (e) { return 0; }
  }

  window.ogWaveMeter = { start: start, stop: stop,
    prime: prime,
    level: level, active: function () { return running; } };
})();

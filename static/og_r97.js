/* Round 97: OG's voice or nothing + the real wave meter
   (Brent, 2026-10-12: "It's a different voice, not OG's" —
   the chat page's legacy device-voice speaker is DELETED
   from index_epic.html in this round; OG now speaks ONLY
   through POST /tts in his server voice, and a failed
   /tts means silence, never a substitute voice. Brent
   then approved the real wave meter 05:23 PDT: "Approve
   — add the real wave meter.")

   WHAT THIS ASSET IS: a live waveform meter that moves
   with OG's REAL audio while he speaks. A Web Audio
   analyser is wired to the very media element that plays
   the /tts MP3 (the page's ogAudio() element, handed to
   start() by playBlob), and gold bars are drawn from the
   analyser's live frequency data. It is shown ONLY while
   that element is playing OG's voice — start() is called
   by playBlob at playback start, stop() on ended / error
   / play() rejection / stopSpeaking(). It is NOT a state
   decoration: no audio through the analyser, no bars.

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

  var BARS = 24;
  var box = null;        // meter container div
  var canvas = null;     // bar canvas inside box
  var ctx2d = null;
  var actx = null;       // AudioContext (lazy, once)
  var analyser = null;   // AnalyserNode (lazy, once)
  var sourceEl = null;   // element the source was built from
  var raf = 0;           // requestAnimationFrame id while drawing
  var running = false;

  function ensureBox() {
    if (box) return box;
    try {
      var style = document.createElement('style');
      style.id = 'og-r97-css';
      style.textContent =
        '#ogWaveMeter{display:none;justify-content:center;padding:2px 0 0}' +
        '#ogWaveMeter.on{display:flex}' +
        '#ogWaveMeter canvas{display:block}';
      document.head.appendChild(style);
      box = document.createElement('div');
      box.id = 'ogWaveMeter';
      box.setAttribute('aria-hidden', 'true');
      canvas = document.createElement('canvas');
      canvas.width = 168;
      canvas.height = 28;
      box.appendChild(canvas);
      ctx2d = canvas.getContext ? canvas.getContext('2d') : null;
      var host = document.getElementById('ogTopAvatarBar');
      if (host) host.appendChild(box);
      else if (document.body) document.body.appendChild(box);
    } catch (e) { box = null; }
    return box;
  }

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

  function draw() {
    if (!running) return;
    try {
      if (analyser && ctx2d && canvas) {
        var data = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(data);
        var w = canvas.width, h = canvas.height;
        ctx2d.clearRect(0, 0, w, h);
        ctx2d.fillStyle = '#ffc107';
        var bw = w / BARS;
        for (var i = 0; i < BARS; i++) {
          var v = data[Math.floor(i * data.length / BARS)] / 255;
          var bh = Math.max(2, v * h);
          ctx2d.fillRect(i * bw + 1, (h - bh) / 2, bw - 2, bh);
        }
      }
    } catch (e) { /* a bad frame never stops the meter or the voice */ }
    raf = requestAnimationFrame(draw);
  }

  function start(el) {
    try {
      if (!ensureBox()) return;
      if (!ensureGraph(el)) return;
      box.classList.add('on');
      if (!running) { running = true; raf = requestAnimationFrame(draw); }
    } catch (e) { /* the meter is decoration on real audio; never fatal */ }
  }

  function stop() {
    try {
      running = false;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      if (box) box.classList.remove('on');
      if (ctx2d && canvas) ctx2d.clearRect(0, 0, canvas.width, canvas.height);
    } catch (e) { /* never throws */ }
  }

  window.ogWaveMeter = { start: start, stop: stop };
})();

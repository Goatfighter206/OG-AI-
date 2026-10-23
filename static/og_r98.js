/* Round 98: the VOICE FOLDER (web) — a picker for how
 * OG sounds (Brent, 2026-10-12: "we should have a
 * couple different voice styles to use" + "it should
 * go in a folder, called voice that you can click on
 * to see all the voices to choose from").
 *
 * THE STORE. The pick lives server-side, per account
 * (og_voice_style.py): GET /voice?avatar=og answers
 * the effective voice + every style row; POST /voice
 * stores a pick and answers the same payload. NO
 * record = the avatar's own voice (web stage = OG =
 * onyx, the Round 37 default). This asset keeps the
 * last server answer and paints ONLY from it (the
 * Round 84/90 discipline).
 *
 * THE SPEAKING SEAM. Every web speaking path funnels
 * through speakReply -> fetchTTSPromise -> POST /tts
 * (Round 97's audit). This asset wraps the page's
 * fetchTTSPromise: while the stored pick is the
 * default (or no answer has landed), the ORIGINAL
 * page function runs untouched — Round 97's exact
 * behavior, byte for byte. With a non-default pick
 * stored, the wrap issues the identical request with
 * the pick as the body's voice — the one measurable
 * difference a style makes, and the only one. A pick
 * takes effect on the next reply (the wrap reads the
 * cached answer at call time, and a folder tap
 * refreshes the cache from the POST's answer).
 * Round 97's law stands: /tts only, failure = silence;
 * nothing here adds a speaker — the device voice
 * stays DELETED.
 *
 * THE FOLDER. A menu row labeled exactly "Voice" in
 * the Account card (beside the Shit-talking row)
 * opens a dedicated Voice page on the OG70 scaffold
 * (canonical ✕ top-right): every style a row, the
 * current one marked, a tap selects via the server
 * and the page repaints from the server's answer.
 *
 * Loaded via the og_tokens.js ride-along loader like
 * Rounds 62/63/65/66/84/85/90/97 — the page is at its
 * push ceiling. Defensive by contract: feature-
 * detected, try/caught, never throws.
 *
 * Ticket R (2026-10-23): the folder also carries the
 * MEANER PACK — forty accented styles under the six
 * classics. Pack rows arrive with a "group" field
 * (one per accent family); the folder paints a gold
 * header when the group changes, so the families
 * read as families on the phone. Rows without a
 * group (the classics) paint exactly as Round 98
 * left them. The speaking seam is unchanged: the
 * pick id goes to /tts as the body's voice, and the
 * server resolves it.
 *
 * Ticket R real-voice rebuild (2026-10-23, later the
 * same day): the pack's first build resolved each id
 * to an OpenAI base voice + accent instructions, and
 * Brent's ear failed it — the accents weren't real.
 * The pack now resolves server-side to REAL
 * ElevenLabs library voices under the SAME forty
 * ids, so a stored pick just starts sounding right.
 * The switch is server-side (the pack-only flag);
 * while it is off, the /voice payload marks pack
 * rows "available": false and this folder paints
 * that honestly — dimmed, tagged "Not switched on",
 * and neither a tap nor a Test fires a request that
 * cannot speak. The classics never carry the mark.
 *
 * Ticket R2 (Brent, 2026-10-23): "There should be a
 * test button to hear the voice right next to the
 * voices." Every row — classics and pack alike —
 * carries a ▶ Test button beside it. A test speaks
 * ONE standard line (SAMPLE_LINE, the same words
 * for every voice, so voices compare fairly) through
 * the REAL /tts seam for THAT row's voice id
 * (ttsWithPick — the identical request a chat reply
 * with that pick makes), so the preview is the
 * actual sound the user will get, never a canned
 * recording that can drift from the live voice. A
 * test never selects: it does not call pick(), and
 * its click is stopped before the row's own select
 * handler can see it. A test is a real /tts call, so
 * it meters exactly like a spoken reply — same door,
 * same cap, no side channel.
 */
(function () {
  'use strict';

  var AVATAR = 'og';               // the web stage is OG
  var DEFAULT_VOICE = 'onyx';      // Round 37: OG's own voice
  var payload = null;              // last /voice answer (server truth)
  // Ticket R2: the one sample line every voice's Test
  // button speaks — identical words for all 46 voices,
  // so the comparison is voice-only.
  var SAMPLE_LINE = 'Yo, this is OG — say the word and I\'m on it, G.';

  var css = ''
    + '.og98-body{padding:4px 2px 14px;color:#ececec}'
    + '.og98-note{color:#999;font-size:.88em;padding:6px 0;margin:0;'
    + 'line-height:1.5}'
    + '.og98-status{color:#ffc107;font-size:.88em;padding:2px 0 6px;'
    + 'margin:0;min-height:1.2em}'
    + '.og98-row{display:flex;align-items:flex-start;gap:10px;'
    + 'width:100%;text-align:left;'
    + 'background:transparent;border:none;border-top:1px solid #232323;'
    + 'padding:12px 0;font-family:inherit;color:#ececec}'
    + '.og98-row:hover{background:rgba(255,193,7,.06)}'
    + '.og98-row.off{opacity:.55}'
    + '.og98-main{flex:1;text-align:left;cursor:pointer;min-width:0}'
    + '.og98-test{flex:none;background:transparent;'
    + 'border:1px solid rgba(255,193,7,.65);color:#ffc107;'
    + 'border-radius:999px;padding:6px 12px;font-family:inherit;'
    + 'font-weight:700;font-size:.85em;cursor:pointer;margin-top:2px}'
    + '.og98-test:disabled{opacity:.5;cursor:default}'
    + '.og98-name{font-size:1.02em;font-weight:700}'
    + '.og98-tag{display:inline-block;margin-left:8px;padding:2px 9px;'
    + 'border-radius:999px;font-size:.78em;font-weight:700;'
    + 'vertical-align:2px}'
    + '.og98-tag.cur{border:1px solid rgba(255,193,7,.65);color:#ffc107}'
    + '.og98-tag.def{border:1px solid #3a3a3a;color:#999}'
    + '.og98-group{color:#ffc107;font-weight:700;font-size:.98em;'
    + 'padding:18px 0 2px;border-top:1px solid #232323;margin-top:4px}';
  try {
    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
  } catch (e) { /* styles are an enhancement; the folder still works */ }

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function api(path, opts) {
    // credentials: the /voice routes are auth-gated — every
    // call carries the session cookie explicitly, never
    // rides on a default.
    var o = opts || {};
    if (!o.credentials) o.credentials = 'same-origin';
    return fetch(path, o).then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, status: r.status, d: d };
      });
    });
  }
  function post(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  }

  function currentVoice() {
    return (payload && payload.voice) || DEFAULT_VOICE;
  }
  function currentName() {
    if (!payload || !payload.styles) return '';
    for (var i = 0; i < payload.styles.length; i++) {
      if (payload.styles[i].current) return payload.styles[i].name;
    }
    return '';
  }

  /* ------------- the poller's discipline -------------
     /voice is auth-gated. The background poll fires ONLY
     when the page itself says we're in the app — the wall
     (ageGate) dismissed by enterApp after /auth/me
     confirmed the session, and the account section shown.
     Firing earlier earns an honest 401: at load (before
     checkMe answers), signed out, and in the seconds
     while a fresh sign-in is still landing (field
     evidence 2026-10-12: four GET /voice 401s in the six
     seconds after the owner's sign-in). A 401 PARKS the
     poller until the page next crosses signed-out ->
     signed-in, so a flaky moment costs one answer, not a
     drumbeat. The folder's own loads (a tap = user
     intent) always run, and any success un-parks. */
  var pollParked = false;
  var wasInApp = false;
  function pageInApp() {
    var g = $('ageGate');
    var sec = $('menuAccountSec');
    return !!(g && g.hidden && sec && !sec.hidden);
  }

  function refresh() {
    if (!pageInApp() || pollParked) return Promise.resolve();
    return api('/voice?avatar=' + AVATAR).then(function (res) {
      if (res.ok && res.d && res.d.ok) {
        payload = res.d;
        paintRowState();
      } else if (res.status === 401) {
        pollParked = true;
      }
    }).catch(function () { /* keep the last server state */ });
  }

  /* ---------------- the speaking seam ---------------- */
  var origFetchTTS = null;
  function ttsWithPick(text, voice) {
    var base = '';
    try { base = API_URL; } catch (e) { base = ''; }
    if (!base) base = window.location.origin;
    return fetch(base + '/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, voice: voice })
    }).then(function (r) {
      if (!r.ok) throw new Error('tts ' + r.status);
      return r.blob();
    });
  }
  function wrapSpeak() {
    if (origFetchTTS || typeof window.fetchTTSPromise !== 'function') {
      return !!origFetchTTS;
    }
    origFetchTTS = window.fetchTTSPromise;
    window.fetchTTSPromise = function (text) {
      var v = currentVoice();
      if (!payload || v === DEFAULT_VOICE) return origFetchTTS(text);
      return ttsWithPick(text, v);
    };
    return true;
  }

  /* ---------------- the Voice folder page ---------------- */
  function openView(opts) {
    if (window.OG70 && window.OG70.openView) {
      window.OG70.openView(opts);
      return;
    }
    var n = 0;
    var iv = setInterval(function () {
      n += 1;
      if (window.OG70 && window.OG70.openView) {
        clearInterval(iv);
        window.OG70.openView(opts);
      } else if (n > 30) {
        clearInterval(iv);
      }
    }, 100);
  }

  function buildVoicePage(body, view) {
    var wrap = el('div', 'og98-body');
    body.appendChild(wrap);
    wrap.appendChild(el('p', 'og98-note',
      'Pick how OG sounds when he talks. The words never '
      + 'change — just the voice. Your pick is saved to '
      + 'your account and follows you to the app.'));
    var status = el('p', 'og98-status', '');
    wrap.appendChild(status);
    var listBox = el('div');
    wrap.appendChild(listBox);
    listBox.appendChild(el('p', 'og98-note', 'Loading…'));

    var alive = true;
    view.track(function () { alive = false; stopTest(); });

    // Ticket R2: preview playback state — one test at
    // a time; starting a new one (or repainting, or
    // leaving the folder) stops the old one cold.
    var testAudio = null;
    var testBtnEl = null;
    function stopTest() {
      if (testAudio) { try { testAudio.pause(); } catch (e) {} testAudio = null; }
      if (testBtnEl) { testBtnEl.disabled = false; testBtnEl = null; }
    }
    // The preview: the standard line through the real
    // /tts seam for the row's own voice id — the same
    // request a chat reply with that pick makes. It
    // never touches the selection.
    function testVoice(s, btn) {
      // Real-voice rebuild: a pack row the server
      // marked unavailable cannot speak — say so on
      // the status line instead of firing a /tts call
      // that can only fail.
      if (s.available === false) {
        status.textContent = 'That voice isn\'t '
          + 'switched on yet.';
        return;
      }
      stopTest();
      testBtnEl = btn;
      btn.disabled = true;
      status.textContent = 'Playing ' + s.name + '…';
      // Every async step re-checks it is still the live
      // test: a pick (which repaints and stops tests) or
      // a newer test supersedes this one, and a stale
      // callback must never clobber the folder's status
      // line or start playing over the new state.
      ttsWithPick(SAMPLE_LINE, s.voice).then(function (blob) {
        if (testBtnEl !== btn) return;
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        testAudio = audio;
        audio.onended = function () {
          if (testBtnEl !== btn) return;
          stopTest();
          status.textContent = '';
          try { URL.revokeObjectURL(url); } catch (e) {}
        };
        audio.onerror = function () {
          if (testBtnEl !== btn) return;
          stopTest();
          status.textContent = 'Couldn\'t play that test — try again.';
        };
        var p = audio.play();
        if (p && p.catch) p.catch(function () {
          if (testBtnEl !== btn) return;
          stopTest();
          status.textContent = 'Couldn\'t play that test — try again.';
        });
      }).catch(function () {
        if (testBtnEl !== btn) return;
        stopTest();
        status.textContent = 'Couldn\'t play that test — OG couldn\'t be reached.';
      });
    }

    function paintStyles(d) {
      stopTest();
      listBox.innerHTML = '';
      if (!d || !d.styles) {
        listBox.appendChild(el('p', 'og98-note',
          'The voice list didn\'t load — close this and '
          + 'open it again.'));
        return;
      }
      var prevGroup = null;
      d.styles.forEach(function (s) {
        if (s.group && s.group !== prevGroup) {
          listBox.appendChild(el('div', 'og98-group', s.group));
        }
        if (s.group) prevGroup = s.group;
        var row = el('div', 'og98-row', '');
        // Real-voice rebuild: the server marks a pack
        // row "available": false while the pack's
        // ElevenLabs lane is not switched on. Paint
        // that honestly — dimmed, tagged — and let a
        // tap explain instead of storing a pick that
        // cannot speak. (A stored pick that is
        // unavailable still shows ✓ Current AND the
        // tag: it is the user's pick, it just can't
        // speak right now.)
        var off = s.available === false;
        if (off) row.className = 'og98-row off';
        // The row's main area is the select target —
        // tapping it picks, exactly as the whole row
        // did before R2.
        var main = el('div', 'og98-main', '');
        var nameLine = el('div', 'og98-name', s.name);
        if (s.current) {
          nameLine.appendChild(el('span', 'og98-tag cur', '✓ Current'));
        } else if (s.is_default) {
          nameLine.appendChild(el('span', 'og98-tag def', 'Default'));
        }
        if (off) {
          nameLine.appendChild(el('span', 'og98-tag def',
            'Not switched on'));
        }
        main.appendChild(nameLine);
        main.appendChild(el('p', 'og98-note', s.note));
        main.addEventListener('click', function () {
          if (off) {
            status.textContent = 'That voice isn\'t '
              + 'switched on yet.';
            return;
          }
          pick(s.voice);
        });
        row.appendChild(main);
        // Ticket R2: the Test button sits right next
        // to the voice. Its click is stopped dead so
        // it can never fall through to selecting.
        var testBtn = el('button', 'og98-test', '▶ Test');
        testBtn.type = 'button';
        testBtn.setAttribute('aria-label', 'Hear ' + s.name);
        testBtn.addEventListener('click', function (ev) {
          if (ev && ev.stopPropagation) ev.stopPropagation();
          testVoice(s, testBtn);
        });
        row.appendChild(testBtn);
        listBox.appendChild(row);
      });
    }

    function load() {
      api('/voice?avatar=' + AVATAR).then(function (res) {
        if (!alive) return;
        if (res.ok && res.d && res.d.ok) {
          payload = res.d;
          pollParked = false;
          paintRowState();
          paintStyles(payload);
        } else {
          paintStyles(payload);
          if (!payload) {
            status.textContent = 'Couldn\'t reach OG — '
              + 'the voices didn\'t load.';
          }
        }
      }).catch(function () {
        if (!alive) return;
        paintStyles(payload);
        if (!payload) {
          status.textContent = 'Couldn\'t reach OG — '
            + 'the voices didn\'t load.';
        }
      });
    }

    function pick(voice) {
      if (payload && payload.voice === voice) {
        status.textContent = 'That\'s the voice OG is '
          + 'already using.';
        return;
      }
      status.textContent = 'Saving…';
      post('/voice', { voice: voice, avatar: AVATAR })
        .then(function (res) {
          if (!alive) return;
          if (res.ok && res.d && res.d.ok) {
            payload = res.d;
            pollParked = false;
            paintRowState();
            paintStyles(payload);
            status.textContent = 'OG\'s voice is now '
              + currentName() + ' — his next reply uses it.';
          } else {
            status.textContent = 'That pick didn\'t save '
              + '— OG couldn\'t be reached. Your voice '
              + 'didn\'t change.';
            load();
          }
        }).catch(function () {
          if (!alive) return;
          status.textContent = 'That pick didn\'t save '
            + '— OG couldn\'t be reached. Your voice '
            + 'didn\'t change.';
          load();
        });
    }

    if (payload) paintStyles(payload);
    load();
  }

  function openVoice() {
    openView({ title: 'Voice', wide: true, onOpen: buildVoicePage });
  }

  /* ---------------- the menu row ---------------- */
  var row = null;
  var stateEl = null;
  function paintRowState() {
    if (!stateEl) return;
    var n = currentName();
    stateEl.textContent = n || '…';
  }
  function buildRow() {
    var card = $('menuAccountCard');
    if (!card || row) return !!row;
    row = el('button', 'og-menu-item', '');
    row.type = 'button';
    row.id = 'og98VoiceRow';
    var ico = el('span', 'og-mi', '');
    ico.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
    row.appendChild(ico);
    row.appendChild(el('span', '', 'Voice'));
    stateEl = el('span', 'og-row-state', '…');
    row.appendChild(stateEl);
    row.addEventListener('click', openVoice);
    card.appendChild(row);
    paintRowState();
    return true;
  }

  /* ------------- boot: wrap + row -------------
     No unconditional first read: at script load the
     page's own /auth/me verdict hasn't landed yet, and a
     /voice fire here is a guaranteed 401 (see the
     poller's discipline above). The slow cadence below
     picks the pick up the moment the page is in-app. */
  var fastTries = 0;
  var fastIv = setInterval(function () {
    fastTries += 1;
    var wrapped = false;
    var built = false;
    try { wrapped = wrapSpeak(); } catch (e) { /* retry next tick */ }
    try { built = buildRow(); } catch (e) { /* retry next tick */ }
    if ((wrapped && built) || fastTries > 300) {
      clearInterval(fastIv);
      // Slow cadence (the Round 84 discipline): keep the
      // cached pick + the row truthful — a pick made in
      // the app or another tab lands here too.
      setInterval(function () {
        try {
          var inApp = pageInApp();
          if (inApp && !wasInApp) pollParked = false;
          wasInApp = inApp;
          if (inApp) refresh();
        } catch (e) { /* never throws */ }
      }, 6000);
    }
  }, 200);
})();

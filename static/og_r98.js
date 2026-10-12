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
 */
(function () {
  'use strict';

  var AVATAR = 'og';               // the web stage is OG
  var DEFAULT_VOICE = 'onyx';      // Round 37: OG's own voice
  var payload = null;              // last /voice answer (server truth)

  var css = ''
    + '.og98-body{padding:4px 2px 14px;color:#ececec}'
    + '.og98-note{color:#999;font-size:.88em;padding:6px 0;margin:0;'
    + 'line-height:1.5}'
    + '.og98-status{color:#ffc107;font-size:.88em;padding:2px 0 6px;'
    + 'margin:0;min-height:1.2em}'
    + '.og98-row{display:block;width:100%;text-align:left;'
    + 'background:transparent;border:none;border-top:1px solid #232323;'
    + 'padding:12px 0;cursor:pointer;font-family:inherit;color:#ececec}'
    + '.og98-row:hover{background:rgba(255,193,7,.06)}'
    + '.og98-name{font-size:1.02em;font-weight:700}'
    + '.og98-tag{display:inline-block;margin-left:8px;padding:2px 9px;'
    + 'border-radius:999px;font-size:.78em;font-weight:700;'
    + 'vertical-align:2px}'
    + '.og98-tag.cur{border:1px solid rgba(255,193,7,.65);color:#ffc107}'
    + '.og98-tag.def{border:1px solid #3a3a3a;color:#999}';
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
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
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

  function refresh() {
    return api('/voice?avatar=' + AVATAR).then(function (res) {
      if (res.ok && res.d && res.d.ok) {
        payload = res.d;
        paintRowState();
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
    view.track(function () { alive = false; });

    function paintStyles(d) {
      listBox.innerHTML = '';
      if (!d || !d.styles) {
        listBox.appendChild(el('p', 'og98-note',
          'The voice list didn\'t load — close this and '
          + 'open it again.'));
        return;
      }
      d.styles.forEach(function (s) {
        var row = el('button', 'og98-row', '');
        row.type = 'button';
        var nameLine = el('div', 'og98-name', s.name);
        if (s.current) {
          nameLine.appendChild(el('span', 'og98-tag cur', '✓ Current'));
        } else if (s.is_default) {
          nameLine.appendChild(el('span', 'og98-tag def', 'Default'));
        }
        row.appendChild(nameLine);
        row.appendChild(el('p', 'og98-note', s.note));
        row.addEventListener('click', function () {
          pick(s.voice);
        });
        listBox.appendChild(row);
      });
    }

    function load() {
      api('/voice?avatar=' + AVATAR).then(function (res) {
        if (!alive) return;
        if (res.ok && res.d && res.d.ok) {
          payload = res.d;
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

  /* ---------------- boot: wrap + row + first read ---------------- */
  refresh();
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
          var acctSec = $('menuAccountSec');
          if (acctSec && !acctSec.hidden) refresh();
        } catch (e) { /* never throws */ }
      }, 6000);
    }
  }, 200);
})();

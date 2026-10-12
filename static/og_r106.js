/* Round 106: the locker save becomes an ICON (Brent,
   2026-10-12: "the save to my locker should be a icon
   right next to the pen at the top right corner" + "And
   only the icon" — OG W, the website).
   The pen is the page's own #newChatBtn (the pencil in
   the dark circle at the topbar's right end, inline in
   index_epic.html). Two save surfaces live in that same
   inline page, so they are re-shaped from here instead —
   the page file is at its push ceiling and only ever
   restamps:
   1. THE TEMP-UPLOAD SAVE. The page's og-storage-js still
      builds its gold "Save to my locker" pill after the
      file chip; this asset retires the pill's paint (CSS
      below) and carries its exact job on a new icon in
      the topbar, immediately left of the pen: same
      /storage/status gate (locker enabled + quota > 0),
      same chip-visibility rule, same POST
      /storage/save-temp, same spoken answer, same chip
      dismissal on success. The pill's own wiring keeps
      running underneath (the menu's locker row rides the
      same refresh) — only its button is never painted.
   2. THE IN-CHAT VIDEO/SONG SAVES. The page builds a
      "Save to my locker" text button inside each finished
      video/song card in the thread, with its own click
      wiring (POST /video/save/<id>, /song/save/<id>) and
      its own note line. The moment such a button lands,
      this asset swaps its words for the folder glyph and
      moves it to the card's top-right corner — the
      page's handler, endpoint and note line are
      untouched. The saved state follows the card's own
      note: when it lands on the locked-in line, the
      glyph fills gold and the label becomes "Saved".
   The generator cards (podcast / motion / voice lab)
   carry their own icon in their own assets; the chat
   claim ("save that to my locker") is untouched.
   Loaded via the og_tokens.js ride-along loader like
   Rounds 62/63/65/66/84/85/90/97/98 — before
   og_stoplisten.js, which stays LAST. Defensive by
   contract: feature-detected, try/caught, never throws. */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  var FOLDER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';

  var css = document.createElement('style');
  css.id = 'og-r106-css';
  css.textContent =
    /* the page's own pill retires — same button, never painted */
    '#lockerSaveBtn{display:none!important}' +
    /* the topbar icon, immediately left of the pen:
       topbar pad (10px + safe-area) 14px; pen is 42px;
       14 + 42 + 10 gap = right:66px */
    '#ogLockerTopBtn{position:absolute;' +
    'top:calc(10px + env(safe-area-inset-top));right:66px}' +
    '#ogLockerTopBtn[hidden]{display:none!important}' +
    /* the in-chat video/song save, top-right of its card */
    '.og-video-done,.og-song-done{position:relative}' +
    '.og-video-done.og106-has-save .message-text,' +
    '.og-song-done.og106-has-save .message-text{padding-right:52px}' +
    'button.og106-save-ico{position:absolute;top:8px;right:8px;' +
    'width:42px;height:42px;border-radius:50%;background:#1b1b1b;' +
    'border:1px solid #333;color:#e8e8e8;display:grid;' +
    'place-items:center;cursor:pointer;padding:0;z-index:2}' +
    'button.og106-save-ico svg{width:21px;height:21px}' +
    'button.og106-save-ico:hover{border-color:rgba(255,193,7,.7);' +
    'color:#ffc107}' +
    'button.og106-save-ico.is-saved{background:linear-gradient(' +
    '135deg,#ffc107,#ff8f00);color:#151515;border-color:transparent}' +
    'button.og106-save-ico.is-saved svg path{fill:currentColor}';
  document.head.appendChild(css);

  function sayAi(text) {
    if (text && typeof addMessage === 'function') addMessage('ai', text);
  }
  function apiBase() {
    return (typeof API_URL !== 'undefined' && API_URL)
      ? API_URL : window.location.origin;
  }

  /* --- 1. The temp-upload save: the topbar icon --------------------- */
  var topBtn = null, lockerOn = false;
  function paintTop() {
    if (!topBtn) return;
    var chip = $('fileChip');
    topBtn.hidden = !(lockerOn && chip && !chip.hidden);
  }
  function refreshTop() {
    fetch(apiBase() + '/storage/status')
      .then(function (r) { return r.json(); })
      .then(function (st) {
        lockerOn = !!(st && st.enabled && st.quota > 0);
        paintTop();
      })
      .catch(function () { lockerOn = false; paintTop(); });
  }
  function buildTop() {
    var bar = document.querySelector('.og-topbar');
    var pen = $('newChatBtn');
    if (!bar || !pen) return false;
    if (topBtn) return true;
    topBtn = document.createElement('button');
    topBtn.type = 'button';
    topBtn.id = 'ogLockerTopBtn';
    topBtn.className = 'og-circle-btn';
    topBtn.innerHTML = FOLDER;
    topBtn.setAttribute('aria-label', 'Save to my locker');
    topBtn.setAttribute('title', 'Save to my locker');
    topBtn.hidden = true;
    bar.appendChild(topBtn);
    topBtn.addEventListener('click', function () {
      topBtn.hidden = true;
      fetch(apiBase() + '/storage/save-temp', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          sayAi(data && data.response);
          if (data && data.ok) {
            var chip = $('fileChip');
            if (chip) chip.hidden = true;
            if (typeof setMood === 'function') setMood('win');
          }
        })
        .catch(function () {
          sayAi('Yo, saving to the locker glitched — slide it again.');
        })
        .then(refreshTop);
    });
    var chip = $('fileChip');
    if (chip && window.MutationObserver) {
      new MutationObserver(refreshTop)
        .observe(chip, { attributes: true, attributeFilter: ['hidden'] });
    }
    refreshTop();
    return true;
  }

  /* --- 2. The in-chat video/song saves ------------------------------- */
  function transformSave(btn) {
    if (!btn || btn.getAttribute('data-og106')) return;
    btn.setAttribute('data-og106', '1');
    btn.innerHTML = FOLDER;
    btn.classList.add('og106-save-ico');
    btn.setAttribute('aria-label', 'Save to my locker');
    btn.setAttribute('title', 'Save to my locker');
    var card = btn.closest ? btn.closest('.og-video-done, .og-song-done')
      : null;
    if (card) card.classList.add('og106-has-save');
    var note = btn.parentNode
      ? btn.parentNode.querySelector('.og-video-save-note') : null;
    if (note && window.MutationObserver) {
      new MutationObserver(function () {
        if ((note.textContent || '')
            .indexOf('Locked in your locker') !== -1) {
          btn.classList.add('is-saved');
          btn.setAttribute('aria-label', 'Saved');
          btn.setAttribute('title', 'Saved');
        }
      }).observe(note, { childList: true, characterData: true,
        subtree: true });
    }
  }
  function scanMessages(root) {
    var list = (root || document)
      .querySelectorAll('.og-video-save, .og-song-save');
    for (var i = 0; i < list.length; i++) transformSave(list[i]);
  }
  function watchMessages() {
    var area = $('messagesArea');
    if (!area) return false;
    scanMessages(area);
    if (window.MutationObserver) {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.matches && n.matches('.og-video-save, .og-song-save')) {
              transformSave(n);
            }
            if (n.querySelectorAll) scanMessages(n);
          }
        }
      }).observe(area, { childList: true });
    }
    return true;
  }

  /* --- boot: retry until the page's own pieces exist ----------------- */
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    var a = false, b = false;
    try { a = buildTop(); } catch (e) { /* retry next tick */ }
    try { b = watchMessages(); } catch (e) { /* retry next tick */ }
    if ((a && b) || tries > 300) clearInterval(iv);
  }, 200);
})();

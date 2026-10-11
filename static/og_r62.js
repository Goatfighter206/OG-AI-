/* Round 62 — message actions + hands-free (web).
   One asset, zero page edits beyond the script tag: all CSS is
   injected from here because the page sits at its push ceiling.

   1. MESSAGE ACTIONS: long-press / right-click / the hover
      dots button on any chat message opens an action sheet:
      reaction row, Reply (quote into the composer), Copy,
      Select, Share, and "OG respond" (draft -> approval card
      -> /respond/send). Reactions are durable per user via
      GET/POST /reactions (og_reactions.py) and render under
      their message, toggling live.
   2. VOICE APPROVALS: while Talk mode is ON and an approval
      card is pending, speech is checked against a strict
      vocabulary (approve/approved, deny/denied/decline; a
      leading "yes" counts only with the approve words, a
      leading "no" only with the deny words). A match clicks
      the SAME button a tap would. Anything else clicks
      nothing and OG says he didn't catch it. No card pending
      (or Talk off) -> the original Talk handler runs as-is.
   3. VOICE LOGIN: a mic button on the sign-in form dictates
      email, then password, submits once through the form's
      own login POST, and clears the password field. The
      password transcript is written ONLY into that field's
      value — it is never logged, never put in chat, never
      sent anywhere else (this file contains no logging of
      it at all). After a successful sign-in a save card
      offers Save / Don't save / Don't ask again; the choice
      persists in localStorage only. The server never stores
      a password for this — there is nothing to store it in.
*/
(function () {
  'use strict';

  var EMOJIS = ['❤️', '😂', '🔥', '👍', '💯'];
  var reactions = {};        // message_key -> [emoji, ...] (mine)
  var sheetFor = null;       // {el, key, text} the sheet acts on
  var respondPending = null; // {draft, quote, card}

  // ------------------------------------------------------------------ CSS
  var css = ''
    + '.r62-dots{position:absolute;top:2px;right:2px;width:26px;height:26px;'
    + 'border-radius:50%;border:1px solid #444;background:rgba(20,20,20,.85);'
    + 'color:#ffc107;font-size:15px;line-height:1;cursor:pointer;opacity:0;'
    + 'transition:opacity .15s;padding:0}'
    + '.message:hover .r62-dots,.r62-dots:focus{opacity:1}'
    + '@media (hover:none){.r62-dots{opacity:.65}}'
    + '.message{position:relative}'
    + '.r62-reactions{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}'
    + '.r62-chip{border:1px solid #ffc107;border-radius:999px;background:'
    + 'rgba(255,193,7,.12);color:#fff;font-size:13px;padding:1px 7px;'
    + 'cursor:pointer}'
    + '.r62-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.55);'
    + 'z-index:1200;display:flex;align-items:flex-end;justify-content:center}'
    + '.r62-sheet{background:#141414;border:1px solid #333;border-bottom:none;'
    + 'border-radius:16px 16px 0 0;padding:14px 16px calc(14px + env('
    + 'safe-area-inset-bottom));width:min(520px,100%);color:#f2f2f2}'
    + '.r62-sheet-emojis{display:flex;justify-content:space-around;'
    + 'padding:4px 0 10px}'
    + '.r62-sheet-emojis button{font-size:26px;background:none;border:none;'
    + 'cursor:pointer;padding:4px}'
    + '.r62-sheet-act{display:block;width:100%;text-align:left;background:'
    + '#1d1d1d;border:1px solid #2c2c2c;color:#f2f2f2;border-radius:10px;'
    + 'padding:11px 12px;margin-top:8px;font:inherit;cursor:pointer}'
    + '.r62-respond-card{margin:10px 12px}'
    + '.r62-quote{color:#bbb;font-size:.9em;margin:6px 0;white-space:pre-wrap}'
    + '.r62-draft{white-space:pre-wrap;margin:6px 0}'
    + '.r62-voice-login{margin-top:10px;width:100%;background:#1d1d1d;'
    + 'border:1px solid #ffc107;color:#ffc107;border-radius:10px;'
    + 'padding:10px 12px;font:inherit;cursor:pointer}'
    + '.r62-note{color:#bbb;font-size:.9em;margin:8px 0 0;line-height:1.4}'
    + '.r62-savecard{position:fixed;inset:0;background:rgba(0,0,0,.6);'
    + 'z-index:1300;display:flex;align-items:center;justify-content:center}'
    + '.r62-savecard-in{background:#141414;border:1px solid #333;'
    + 'border-radius:14px;padding:18px;width:min(420px,92vw);color:#f2f2f2}'
    + '.r62-savecard-in button{display:block;width:100%;margin-top:8px;'
    + 'padding:11px 12px;border-radius:10px;border:1px solid #2c2c2c;'
    + 'background:#1d1d1d;color:#f2f2f2;font:inherit;cursor:pointer}'
    + '.r62-savecard-in button.r62-save{background:#ffc107;color:#111;'
    + 'border-color:#ffc107;font-weight:bold}';
  var style = document.createElement('style');
  style.id = 'og-r62-css';
  style.textContent = css;
  document.head.appendChild(style);

  function $(id) { return document.getElementById(id); }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) { return r.json(); });
  }
  function sayAi(text) {
    if (text && typeof addMessage === 'function') addMessage('ai', text);
  }
  function hashText(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }
  function copyText(text, done) {
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (e) { }
      ta.remove();
      if (done) done();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done || function () { })
        .catch(fallback);
    } else fallback();
  }

  // ------------------------------------------------------- message tagging
  function tagMessage(el) {
    if (!el || el.getAttribute('data-r62key')) return;
    var textEl = el.querySelector('.message-text');
    var text = textEl ? textEl.textContent : (el.textContent || '');
    var area = $('messagesArea');
    var same = 0;
    if (area) {
      var prior = area.querySelectorAll('.message');
      for (var i = 0; i < prior.length; i++) {
        if (prior[i] === el) break;
        var pt = prior[i].querySelector('.message-text');
        if (pt && hashText(pt.textContent || '') === hashText(text)) same++;
      }
    }
    var key = 'm' + hashText(text || '') + '-' + same;
    el.setAttribute('data-r62key', key);
    var content = el.querySelector('.message-content') || el;
    var dots = document.createElement('button');
    dots.type = 'button';
    dots.className = 'r62-dots';
    dots.textContent = '⋯';
    dots.title = 'Message actions';
    dots.setAttribute('aria-label', 'Message actions');
    dots.addEventListener('click', function (e) {
      e.stopPropagation();
      openSheet(el);
    });
    content.appendChild(dots);
    var row = document.createElement('div');
    row.className = 'r62-reactions';
    el.appendChild(row);
    renderReactions(el);
  }

  function renderReactions(el) {
    var key = el.getAttribute('data-r62key');
    var row = el.querySelector('.r62-reactions');
    if (!row) return;
    row.innerHTML = '';
    var list = reactions[key] || [];
    list.forEach(function (emoji) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'r62-chip';
      chip.textContent = emoji;
      chip.title = 'Tap to remove your reaction';
      chip.addEventListener('click', function () { toggleReaction(key, emoji); });
      row.appendChild(chip);
    });
  }

  function renderAllReactions() {
    var area = $('messagesArea');
    if (!area) return;
    area.querySelectorAll('.message').forEach(function (el) {
      tagMessage(el);
      renderReactions(el);
    });
  }

  function toggleReaction(key, emoji) {
    api('/reactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_key: key, emoji: emoji })
    }).then(function (res) {
      if (res && res.ok) {
        if (res.entry && res.entry.length) reactions[key] = res.entry;
        else delete reactions[key];
        renderAllReactions();
      }
    }).catch(function () { });
  }

  function loadReactions() {
    api('/reactions').then(function (res) {
      if (res && res.reactions) {
        reactions = res.reactions;
        renderAllReactions();
      }
    }).catch(function () { });
  }

  // ----------------------------------------------------------- action sheet
  function closeSheet() {
    var bd = document.querySelector('.r62-backdrop');
    if (bd) bd.remove();
    sheetFor = null;
  }

  function openSheet(el) {
    closeSheet();
    tagMessage(el);
    var textEl = el.querySelector('.message-text');
    var text = textEl ? textEl.textContent : '';
    sheetFor = { el: el, key: el.getAttribute('data-r62key'), text: text };
    var bd = document.createElement('div');
    bd.className = 'r62-backdrop';
    var sheet = document.createElement('div');
    sheet.className = 'r62-sheet';
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-label', 'Message actions');
    var row = document.createElement('div');
    row.className = 'r62-sheet-emojis';
    EMOJIS.forEach(function (emoji) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = emoji;
      b.setAttribute('aria-label', 'React ' + emoji);
      b.addEventListener('click', function () {
        var key = sheetFor && sheetFor.key;
        closeSheet();
        if (key) toggleReaction(key, emoji);
      });
      row.appendChild(b);
    });
    sheet.appendChild(row);
    function act(label, fn) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'r62-sheet-act';
      b.textContent = label;
      b.addEventListener('click', function () {
        var ctx = sheetFor;
        closeSheet();
        if (ctx) fn(ctx);
      });
      sheet.appendChild(b);
    }
    act('↩️ Reply', function (ctx) {
      var input = $('messageInput');
      if (!input) return;
      var quote = '💬 "' + ctx.text + '"\n';
      input.value = quote + (input.value || '');
      input.focus();
    });
    act('📋 Copy', function (ctx) { copyText(ctx.text); });
    act('🔤 Select text', function (ctx) {
      var t = ctx.el.querySelector('.message-text');
      if (!t) return;
      t.style.userSelect = 'text';
      try {
        var range = document.createRange();
        range.selectNodeContents(t);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) { }
    });
    act('📤 Share', function (ctx) {
      if (navigator.share) {
        navigator.share({ text: ctx.text }).catch(function () { });
      } else {
        copyText(ctx.text, function () {
          sayAi('📋 Copied — this browser has no share sheet, so it is on your clipboard.');
        });
      }
    });
    act('🤖 OG respond', function (ctx) { requestDraft(ctx); });
    bd.appendChild(sheet);
    bd.addEventListener('click', function (e) {
      if (e.target === bd) closeSheet();
    });
    document.body.appendChild(bd);
  }

  // ------------------------------------------------------------ OG respond
  function requestDraft(ctx) {
    sayAi('✍️ Cooking up my reply to that one…');
    api('/respond/draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: ctx.text })
    }).then(function (res) {
      if (res && res.ok && res.draft) showRespondCard(res.draft, ctx.text);
      else sayAi('⚠️ That draft didn\'t come through — try again in a sec.');
    }).catch(function () {
      sayAi('⚠️ That draft didn\'t come through — connection hiccup.');
    });
  }

  function showRespondCard(draft, quote) {
    if (respondPending && respondPending.card) respondPending.card.remove();
    var card = document.createElement('div');
    card.className = 'og-approve-card r62-respond-card';
    card.id = 'r62RespondCard';
    var head = document.createElement('div');
    head.className = 'og-approve-head';
    head.textContent = '🤖 OG respond — approve my reply?';
    var q = document.createElement('div');
    q.className = 'r62-quote';
    q.textContent = '"' + quote + '"';
    var d = document.createElement('div');
    d.className = 'r62-draft';
    d.textContent = draft;
    var actions = document.createElement('div');
    actions.className = 'og-approve-actions';
    var yes = document.createElement('button');
    yes.type = 'button';
    yes.id = 'r62RespondYesBtn';
    yes.textContent = '✓ Approve';
    var no = document.createElement('button');
    no.type = 'button';
    no.id = 'r62RespondNoBtn';
    no.textContent = '✕ Decline';
    actions.appendChild(yes);
    actions.appendChild(no);
    card.appendChild(head);
    card.appendChild(q);
    card.appendChild(d);
    card.appendChild(actions);
    var area = $('messagesArea');
    if (area) area.appendChild(card);
    else document.body.appendChild(card);
    if (typeof ogFollowBottom === 'function') ogFollowBottom(true);
    respondPending = { draft: draft, quote: quote, card: card };
    yes.addEventListener('click', function () { decideRespond(true); });
    no.addEventListener('click', function () { decideRespond(false); });
  }

  function decideRespond(approve) {
    var pending = respondPending;
    if (!pending) return;
    respondPending = null;
    if (pending.card) pending.card.remove();
    if (!approve) {
      sayAi('✕ Scrapped that reply — it never went in the thread.');
      return;
    }
    api('/respond/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft: pending.draft, quote: pending.quote })
    }).then(function (res) {
      if (res && res.ok) sayAi(pending.draft);
      else sayAi('⚠️ That reply didn\'t save — try again in a sec.');
    }).catch(function () {
      sayAi('⚠️ That reply didn\'t save — connection hiccup.');
    });
  }

  // --------------------------------------------------------- voice approvals
  function visible(el) {
    return !!el && !el.hidden
      && window.getComputedStyle(el).display !== 'none';
  }

  function pendingCard() {
    var pairs = [];
    if (respondPending && respondPending.card) {
      pairs.push({ card: respondPending.card, yes: $('r62RespondYesBtn'), no: $('r62RespondNoBtn') });
    }
    pairs.push({ card: $('approveCard'), yes: $('approveYesBtn'), no: $('approveNoBtn') });
    pairs.push({ card: $('videoCard'), yes: $('videoYesBtn'), no: $('videoNoBtn') });
    pairs.push({ card: $('songCard'), yes: $('songYesBtn'), no: $('songNoBtn') });
    for (var i = 0; i < pairs.length; i++) {
      if (pairs[i].yes && pairs[i].no && visible(pairs[i].card)) return pairs[i];
    }
    return null;
  }

  // Strict vocabulary: exact phrase after trim/lowercase/strip
  // trailing punctuation. "yes" may lead ONLY an approve word;
  // "no" may lead ONLY a deny word.
  function voiceVerdict(raw) {
    var t = String(raw || '').trim().toLowerCase().replace(/[.!?]+$/, '');
    var approve = ['approve', 'approved', 'yes approve', 'yes approved'];
    var deny = ['deny', 'denied', 'decline',
      'no deny', 'no denied', 'no decline'];
    if (approve.indexOf(t) !== -1) return 'approve';
    if (deny.indexOf(t) !== -1) return 'deny';
    return null;
  }
  window.ogR62VoiceVerdict = voiceVerdict;

  function wrapTalkRecognition() {
    var rec = null;
    try { rec = recognition; } catch (e) { rec = null; }
    if (!rec || rec.__r62Wrapped) return !!rec;
    var original = rec.onresult;
    rec.__r62Wrapped = true;
    rec.onresult = function (event) {
      var on = false;
      try { on = !!voiceChatOn; } catch (e) { on = false; }
      var pending = on ? pendingCard() : null;
      if (pending) {
        var transcript = '';
        try { transcript = event.results[0][0].transcript; } catch (e) { }
        var verdict = voiceVerdict(transcript);
        if (verdict === 'approve') { pending.yes.click(); return; }
        if (verdict === 'deny') { pending.no.click(); return; }
        sayAi('🎤 Didn\'t catch that — say "approve" or "decline", or tap the button.');
        return;
      }
      if (typeof original === 'function') original(event);
    };
    return true;
  }

  // -------------------------------------------------------------- voice login
  function buildVoiceLogin() {
    var form = $('authSigninForm');
    var emailEl = $('authSigninEmail');
    var pwEl = $('authSigninPassword');
    if (!form || !emailEl || !pwEl || $('r62VoiceLoginBtn')) return;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'r62VoiceLoginBtn';
    btn.className = 'r62-voice-login';
    btn.textContent = '🎤 Voice login';
    var note = document.createElement('p');
    note.className = 'r62-note';
    note.id = 'r62VoiceLoginNote';
    note.hidden = true;
    var remember = form.querySelector('.og-auth-remember');
    if (remember) {
      form.insertBefore(btn, remember);
      form.insertBefore(note, remember);
    } else {
      form.appendChild(btn);
      form.appendChild(note);
    }
    if (!SR) {
      btn.addEventListener('click', function () {
        note.hidden = false;
        note.textContent = 'Voice login isn\'t available in this browser — type it instead.';
      });
      return;
    }
    var busy = false;
    var stage = null; // 'email' -> 'password' -> 'submitted'
    function setNote(text) {
      note.hidden = !text;
      note.textContent = text || '';
    }
    function capture() {
      var rec = new SR();
      rec.continuous = false;
      rec.interimResults = false;
      rec.lang = 'en-US';
      rec.maxAlternatives = 1;
      rec.onresult = function (event) {
        var transcript = '';
        try { transcript = event.results[0][0].transcript; } catch (e) { }
        if (stage === 'email') {
          emailEl.value = String(transcript || '').toLowerCase()
            .replace(/\s+at\s+/g, '@')
            .replace(/\s+dot\s+/g, '.')
            .replace(/\s+/g, '');
          stage = 'password';
          setNote('Email in. Now say your password — it goes straight into the password box and nowhere else.');
          setTimeout(capture, 250);
        } else if (stage === 'password') {
          // SECURITY: the transcript is written ONLY into the
          // password field, submitted once through the form's
          // own login POST, and the field is cleared right
          // after. It is never logged or sent anywhere else.
          pwEl.value = String(transcript || '').replace(/\s+/g, '');
          stage = 'submitted';
          setNote('Signing you in…');
          if (form.requestSubmit) form.requestSubmit();
          else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
          pwEl.value = '';
          watchForSaveCard();
        }
      };
      rec.onerror = function () {
        if (stage === 'email' || stage === 'password') {
          setNote('Didn\'t hear that — tap 🎤 Voice login and try again.');
          busy = false;
          stage = null;
        }
      };
      try { rec.start(); } catch (e) { busy = false; }
    }
    btn.addEventListener('click', function () {
      if (busy) return;
      busy = true;
      stage = 'email';
      setNote('Listening — say your email address.');
      capture();
      setTimeout(function () { if (stage !== 'submitted') busy = false; }, 30000);
    });

    function watchForSaveCard() {
      var tries = 0;
      var timer = setInterval(function () {
        tries++;
        if (tries > 10) { clearInterval(timer); return; }
        fetch('/auth/me').then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.signed_in) {
              clearInterval(timer);
              maybeShowSaveCard();
            }
          }).catch(function () { });
      }, 700);
    }

    function maybeShowSaveCard() {
      var choice = null;
      try { choice = localStorage.getItem('og_r62_pw_choice'); } catch (e) { }
      if (choice === 'never' || choice === 'save') return;
      if (document.querySelector('.r62-savecard')) return;
      var wrap = document.createElement('div');
      wrap.className = 'r62-savecard';
      var box = document.createElement('div');
      box.className = 'r62-savecard-in';
      var h = document.createElement('div');
      h.textContent = '🔐 Save this login on this device?';
      box.appendChild(h);
      var p = document.createElement('p');
      p.className = 'r62-note';
      p.textContent = 'OG never stores your password. "Save" keeps you remembered here and lets your browser\'s own password manager hold it.';
      box.appendChild(p);
      function outcome(label, cls, fn) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = label;
        if (cls) b.className = cls;
        b.addEventListener('click', function () { fn(); wrap.remove(); });
        box.appendChild(b);
      }
      outcome('Save', 'r62-save', function () {
        try { localStorage.setItem('og_r62_pw_choice', 'save'); } catch (e) { }
        var rem = $('authRemember');
        if (rem) rem.checked = true;
      });
      outcome('Don\'t save', '', function () { });
      outcome('Don\'t ask again', '', function () {
        try { localStorage.setItem('og_r62_pw_choice', 'never'); } catch (e) { }
      });
      wrap.appendChild(box);
      document.body.appendChild(wrap);
    }
  }

  // ------------------------------------------------------------------- init
  function init() {
    var area = $('messagesArea');
    if (area) {
      renderAllReactions();
      loadReactions();
      var observer = new MutationObserver(function (muts) {
        muts.forEach(function (m) {
          m.addedNodes.forEach(function (n) {
            if (n.nodeType === 1 && n.classList
                && n.classList.contains('message')) tagMessage(n);
          });
        });
      });
      observer.observe(area, { childList: true });

      var pressTimer = null;
      var pressTarget = null;
      area.addEventListener('pointerdown', function (e) {
        var msg = e.target.closest ? e.target.closest('.message') : null;
        if (!msg || e.target.closest('button, a, .r62-chip')) return;
        pressTarget = msg;
        pressTimer = setTimeout(function () {
          if (pressTarget) openSheet(pressTarget);
          pressTarget = null;
        }, 550);
      });
      ['pointerup', 'pointercancel', 'pointermove'].forEach(function (ev) {
        area.addEventListener(ev, function () {
          if (pressTimer) clearTimeout(pressTimer);
          pressTimer = null;
          if (ev !== 'pointermove') pressTarget = null;
        }, { passive: true });
      });
      area.addEventListener('contextmenu', function (e) {
        var msg = e.target.closest ? e.target.closest('.message') : null;
        if (!msg) return;
        e.preventDefault();
        openSheet(msg);
      });
    }
    buildVoiceLogin();
    if (!wrapTalkRecognition()) {
      var tries = 0;
      var timer = setInterval(function () {
        tries++;
        if (wrapTalkRecognition() || tries > 20) clearInterval(timer);
      }, 300);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

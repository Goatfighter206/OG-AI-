/* Round 65: Languages (web).
 *
 * One asset, zero page edits beyond the loader stub in
 * static/og_tokens.js — the page is at its push ceiling (the
 * R62/R63 pattern). A "Languages" section is injected into the
 * side menu right after the Account card (the R63 placement):
 * one tappable row per language from GET /language — English
 * and Spanish pinned on top, the rest under them — with the
 * current language marked. A tap POSTs {"code": ...} to
 * /language; the server stores the caller's durable pref and
 * OG's next reply is in that language. The section shows only
 * while the Account section is shown (signed in), matching
 * R63's visibility sync.
 */
(function () {
  'use strict';

  var css = ''
    + '.og65-lang{display:flex;align-items:center;gap:13px;width:100%;'
    + 'padding:13px 16px;border:none;background:transparent;color:#ececec;'
    + 'font-size:1rem;font-family:inherit;text-align:left;cursor:pointer;'
    + 'min-height:50px}'
    + '.og-menu-card .og65-lang + .og65-lang{border-top:1px solid #232323}'
    + '.og65-lang:hover{background:#1f1f1f}'
    + '.og65-state{margin-left:auto;color:#ffc107;font-weight:700;'
    + 'font-size:.85em}'
    + '.og65-note{padding:10px 16px;color:#999;font-size:.88em;margin:0}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) { return r.json(); });
  }

  var sec = null;       // the nav card
  var head = null;      // the h3 section header
  var current = 'en';
  var langs = [];

  function render() {
    if (!sec) return;
    sec.innerHTML = '';
    langs.forEach(function (lang, i) {
      var b = el('button', 'og65-lang', lang.name);
      b.type = 'button';
      b.setAttribute('data-code', lang.code);
      if (lang.code === current) {
        var s = el('span', 'og65-state', 'Current');
        b.appendChild(s);
      }
      b.addEventListener('click', function () { choose(lang.code); });
      sec.appendChild(b);
      if (i === 1 && langs.length > 2) {
        sec.appendChild(el('p', 'og65-note', 'More languages'));
      }
    });
  }

  function refresh() {
    api('/language').then(function (d) {
      if (d && d.languages) {
        langs = d.languages;
        current = d.current || 'en';
        render();
      }
    }).catch(function () { /* section stays as-is */ });
  }

  function choose(code) {
    api('/language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code })
    }).then(function (d) {
      if (d && d.ok) {
        current = d.current || code;
        render();
      }
    }).catch(function () { /* pref unchanged */ });
  }

  function buildSection() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    head = el('h3', 'og-menu-sec', 'Languages');
    sec = el('nav', 'og-menu-card', '');
    sec.id = 'ogLangCard';
    card.parentNode.insertBefore(head, card.nextSibling);
    card.parentNode.insertBefore(sec, head.nextSibling);
    sec.hidden = true;
    head.hidden = true;
    refresh();
    return true;
  }

  // Show/hide with the Account section (signed in), like R63.
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    if (buildSection()) {
      var acctSec = $('menuAccountSec');
      var signedIn = acctSec && !acctSec.hidden;
      sec.hidden = !signedIn;
      if (head) head.hidden = !signedIn;
      if (signedIn && tries % 4 === 0) refresh();
    }
    if (tries > 600) clearInterval(iv);
  }, 1500);
})();

/* Round 65: Languages (web). Round 70 reshape (Brent's
 * folder rule): the languages live behind ONE "Languages"
 * row — not spread out as a row per language in the menu.
 *
 * One asset, zero page edits beyond the loader stub in
 * static/og_tokens.js (the page is at its push ceiling).
 * A single tappable "Languages" row sits in the side menu
 * right after the Account card; its right-hand state shows
 * the current language. The tap opens the shared OG70
 * full view: one row per language from GET /language —
 * English and Spanish pinned on top by the server, the
 * rest under a "More languages" divider — with the current
 * language marked "Current". A tap POSTs {"code": ...} to
 * /language; the server stores the caller's durable pref
 * and OG's next reply is in that language. The row shows
 * only while the Account section is shown (signed in),
 * matching R63's visibility sync.
 */
(function () {
  'use strict';

  var css = ''
    + '.og65-lang{display:flex;align-items:center;gap:13px;width:100%;'
    + 'padding:13px 16px;border:none;background:transparent;color:#ececec;'
    + 'font-size:1rem;font-family:inherit;text-align:left;cursor:pointer;'
    + 'min-height:50px}'
    + '.og65-langrow .og65-lang + .og65-lang{border-top:1px solid #232323}'
    + '.og65-lang:hover{background:#1f1f1f}'
    + '.og65-state{margin-left:auto;color:#ffc107;font-weight:700;'
    + 'font-size:.85em}'
    + '.og65-note{padding:10px 16px;color:#999;font-size:.88em;margin:0}'
    + '.og65-card{background:#171717;border:1px solid #262626;'
    + 'border-radius:20px;overflow:hidden;'
    + 'box-shadow:0 8px 22px rgba(0,0,0,.35)}';
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

  /* OG70 loads through the same stub chain; resolve it at
   * tap time so dynamic-script order can never break us. */
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

  var sec = null;       // the nav card holding the one row
  var stateEl = null;   // the row's current-language label
  var current = 'en';
  var langs = [];

  function currentName() {
    for (var i = 0; i < langs.length; i++) {
      if (langs[i].code === current) return langs[i].name;
    }
    return current;
  }

  function paintRowState() {
    if (stateEl) stateEl.textContent = currentName();
  }

  function renderList(body) {
    body.innerHTML = '';
    var card = el('div', 'og65-card og65-langrow');
    langs.forEach(function (lang, i) {
      var b = el('button', 'og65-lang', lang.name);
      b.type = 'button';
      b.setAttribute('data-code', lang.code);
      if (lang.code === current) {
        b.appendChild(el('span', 'og65-state', 'Current'));
      }
      b.addEventListener('click', function () {
        choose(lang.code, body);
      });
      card.appendChild(b);
      if (i === 1 && langs.length > 2) {
        card.appendChild(el('p', 'og65-note', 'More languages'));
      }
    });
    body.appendChild(card);
    body.appendChild(el('p', 'og65-note',
      'OG answers in the language you pick — the pick stays '
      + 'on your account.'));
  }

  function refresh() {
    api('/language').then(function (d) {
      if (d && d.languages) {
        langs = d.languages;
        current = d.current || 'en';
        paintRowState();
      }
    }).catch(function () { /* row stays as-is */ });
  }

  function choose(code, body) {
    api('/language', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code })
    }).then(function (d) {
      if (d && d.ok) {
        current = d.current || code;
        if (d.languages) langs = d.languages;
        paintRowState();
        if (body) renderList(body);
      }
    }).catch(function () { /* pref unchanged */ });
  }

  function openLanguages() {
    openView({
      title: 'Languages',
      onOpen: function (body) {
        if (langs.length) {
          renderList(body);
        } else {
          body.appendChild(el('p', 'og65-note', 'Loading…'));
          api('/language').then(function (d) {
            if (d && d.languages) {
              langs = d.languages;
              current = d.current || 'en';
              paintRowState();
            }
            renderList(body);
          }).catch(function () { renderList(body); });
        }
      }
    });
  }

  function buildRow() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    sec = el('nav', 'og-menu-card', '');
    sec.id = 'ogLangCard';
    var b = el('button', 'og-menu-item', '');
    b.type = 'button';
    b.id = 'ogLangRow';
    var ico = el('span', 'og-mi', '');
    ico.innerHTML = '<svg><use href="#i-globe"/></svg>';
    b.appendChild(ico);
    b.appendChild(el('span', '', 'Languages'));
    stateEl = el('span', 'og-row-state', '');
    b.appendChild(stateEl);
    b.addEventListener('click', openLanguages);
    sec.appendChild(b);
    card.parentNode.insertBefore(sec, card.nextSibling);
    sec.hidden = true;
    refresh();
    return true;
  }

  // Show/hide with the Account section (signed in), like R63.
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    if (buildRow()) {
      var acctSec = $('menuAccountSec');
      var signedIn = acctSec && !acctSec.hidden;
      sec.hidden = !signedIn;
      if (signedIn && tries % 4 === 0) refresh();
    }
    if (tries > 600) clearInterval(iv);
  }, 1500);
})();

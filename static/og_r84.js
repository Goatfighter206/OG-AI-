/* Round 84: the shit-talking switch row in the menu's
 * Account card. One per-account switch (GET/POST
 * /shittalk — the same one switch the chat flip and the
 * server read): ON = full OG (swearing, roasts,
 * comebacks), OFF = same help, clean and straight.
 * The row paints ONLY the server's answer — a tap POSTs
 * the flip and paints what comes back; a failed POST
 * re-reads the server state and paints that (the Round
 * 73 web-toggle discipline). Loaded via the og_tokens.js
 * ride-along loader like Rounds 62/63/65/66 — the page is
 * at its push ceiling. */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var row = null;
  var stateEl = null;
  var on = true;

  function paint(v) {
    on = !!v;
    if (stateEl) stateEl.textContent = on ? 'On' : 'Off';
  }

  function refresh() {
    api('/shittalk').then(function (res) {
      if (res && typeof res.on === 'boolean') paint(res.on);
    }).catch(function () { /* keep the last server state */ });
  }

  function build() {
    var card = $('menuAccountCard');
    if (!card || row) return !!row;
    row = el('button', 'og-menu-item og-nochev', '');
    row.type = 'button';
    row.id = 'og84ShittalkRow';
    var ico = el('span', 'og-mi', '\u2726');
    row.appendChild(ico);
    row.appendChild(el('span', '', 'Shit-talking: '));
    stateEl = el('span', 'og-row-state', 'On');
    row.appendChild(stateEl);
    row.addEventListener('click', function () {
      post('/shittalk', { on: !on }).then(function (res) {
        if (res && typeof res.on === 'boolean') paint(res.on);
        else refresh();
      }).catch(function () { refresh(); });
    });
    card.appendChild(row);
    refresh();
    return true;
  }

  // The Account card itself is hidden while signed out, so
  // the row inherits that; the poll keeps the painted state
  // truthful after a chat flip (the Round 63 cadence).
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    if (build()) {
      var acctSec = $('menuAccountSec');
      var signedIn = acctSec && !acctSec.hidden;
      if (signedIn && tries % 4 === 0) refresh();
    }
    if (tries > 600) clearInterval(iv);
  }, 1500);
})();

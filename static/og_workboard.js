/* Ticket N2 — the menu work board (Brent, 2026-10-21:
   "make the menu what program is what all he is working
   on — what's all in his inquiry or job that you gave
   him").

   The ☰ menu's "OG's work" card paints GET /workboard —
   the caller's OWN grouped work state, composed on the
   server from og_progress's one honest read of the
   owning stores (og_workboard.py). This file invents
   nothing: it fetches on menu open (and every 30 s while
   the menu stays open) and paints exactly what the
   server sent — labeled rows under Working on now /
   Waiting on you / In the queue / Just finished. An
   empty board paints the honest empty line; a failed
   fetch paints a couldn't-load line, never fake rows.

   Defensive by contract (the og_r97 shape): feature-
   detected, try/caught, never throws. */
(function () {
  'use strict';

  var body = null;
  var timer = null;
  var loading = false;

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function rowEl(row) {
    var wrap = el('div', 'og-work-row');
    var top = el('div', 'og-work-row-top');
    top.appendChild(el('span', 'og-work-ico', row.icon || '•'));
    top.appendChild(el('span', 'og-work-title', row.title || 'Untitled'));
    if (row.state_label) {
      top.appendChild(el('span', 'og-work-state', row.state_label));
    }
    wrap.appendChild(top);
    if (row.sub) wrap.appendChild(el('div', 'og-work-sub', row.sub));
    if (row.pct != null && isFinite(row.pct)) {
      var bar = el('div', 'og-work-bar');
      var fill = el('span', 'og-work-bar-fill');
      fill.style.width = Math.max(0, Math.min(100, row.pct)) + '%';
      bar.appendChild(fill);
      wrap.appendChild(bar);
    }
    return wrap;
  }

  function groupEl(label, rows, more) {
    var frag = document.createDocumentFragment();
    frag.appendChild(el('div', 'og-work-group', label));
    rows.forEach(function (r) { frag.appendChild(rowEl(r)); });
    if (more > 0) {
      frag.appendChild(el('div', 'og-work-more',
        '+' + more + ' more'));
    }
    return frag;
  }

  function paint(data) {
    if (!body) return;
    body.textContent = '';
    if (!data || data.ok !== true) {
      body.appendChild(el('div', 'og-work-empty',
        "Couldn't load OG's board just now."));
      return;
    }
    if (!data.signed_in) {
      body.appendChild(el('div', 'og-work-empty',
        "Sign in and OG's work for you shows up here."));
      return;
    }
    var g = data.groups || {};
    var counts = data.counts || {};
    var any = false;
    function addGroup(label, key) {
      var rows = g[key] || [];
      if (!rows.length) return;
      any = true;
      var total = counts[key] || rows.length;
      body.appendChild(groupEl(label, rows, total - rows.length));
    }
    addGroup('Working on now', 'working');
    addGroup('Waiting on you', 'waiting');
    addGroup('In the queue', 'queue');
    addGroup('Just finished', 'finished');
    if (data.owner_note && data.owner_note.title) {
      any = true;
      body.appendChild(groupEl('Standing orders', [{
        icon: '🌙', title: data.owner_note.title,
        state_label: '', pct: null,
        sub: data.owner_note.sub || ''
      }], 0));
    }
    if (!any) {
      body.appendChild(el('div', 'og-work-empty',
        "Nothing on right now — jobs you give OG land " +
        'here while he works them.'));
    }
  }

  function refresh() {
    if (loading || !body) return;
    loading = true;
    try {
      fetch('/workboard', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) { loading = false; paint(d); })
        .catch(function () {
          loading = false;
          paint({ ok: false });
        });
    } catch (e) {
      loading = false;
      paint({ ok: false });
    }
  }

  function menuOpen() {
    var m = $('sideMenu');
    return !!(m && m.classList.contains('open'));
  }

  function start() {
    body = $('menuWorkBody');
    if (!body) return;
    refresh();
    var m = $('sideMenu');
    if (m && window.MutationObserver) {
      new MutationObserver(function () {
        if (menuOpen()) {
          refresh();
          if (!timer) {
            timer = setInterval(function () {
              if (menuOpen()) refresh();
              else { clearInterval(timer); timer = null; }
            }, 30000);
          }
        }
      }).observe(m, { attributes: true,
                      attributeFilter: ['class'] });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();

/* OG dashboards — the shared full-screen surface scaffold
   + the Manager Dashboard renderer (OG W dashboards wave,
   2026-10-16; Manager governed by the Round 267 SPEC).

   ONE scaffold, four surfaces:
     swipe LEFT  -> 'crypto'  (Crypto Hub, og_markets.js)
     avatar tap  -> 'profile' (Profile Sheet, this file)
     swipe RIGHT -> 'fin'     (Financial Smart Agent,
                               og_markets.js)
     Progress    -> 'manager' (Crew Dashboard, this file)
   The Manager's home is the Progress panel ITSELF:
   og_shell.js mounts this renderer into the panel via
   OGDS.mount (Brent, 2026-10-16 16:09 — the panel's
   only content is the live Manager; the old progress
   lists and the bottom #ogManagerBtn button are
   retired). The Profile Sheet's door is the avatar tap
   alone (Brent, 2026-10-16 10:13: the sheet is not
   the Manager).

   The contract other renderers code against:
     window.OGDS = {
       open(name), close(),
       mount(name, el),   // render into a host element
                          // (the Progress panel); resolves
                          // to the cleanup fn or null
       registerSurface(name, rendererFn),
       state   // the last /dashboards/state payload
     }
   A renderer is fn(bodyEl): it renders into the surface
   body and MAY return a cleanup function (or a promise of
   one); the scaffold runs it when the surface closes, so
   page-side timers die with the surface — the R267 rule:
   auto-refresh lives only while the surface is open.

   Every surface is ONE lazily built full-screen overlay
   (class og-dash, black-and-gold) with the canonical
   42x42 X top-right (R87 law). Closing returns to the
   exact prior surface: the scaffold never touches chat,
   seat, menu, or profile state.

   Entry gates: open() fetches /dashboards/state ONCE per
   open and refuses a surface the payload marks disabled.
   The avatar rebind arms only after a state read says the
   profile sheet exists — while it is off, the avatar tap
   keeps its prior behavior (the profile) exactly, per
   drill (h).
*/
(function () {
'use strict';

var surfaces = {};   // name -> {renderer, el, bodyEl, cleanup}
var openName = null;
var profileArmed = false;

/* fetch with a hard timeout (Drill 5 fix, 2026-10-16):
   a fetch whose promise never settles — a dead pooled
   connection on a moving phone — otherwise leaves the
   surface on its "Loading…" paint forever, because a
   hung fetch never rejects and the .catch never runs.
   The abort turns the hang into the ordinary failure
   path every caller already paints honestly. */
function fetchTO(url, ms) {
  if (typeof AbortController === 'undefined') {
    return fetch(url);
  }
  var ctl = new AbortController();
  var t = setTimeout(function () { ctl.abort(); }, ms);
  return fetch(url, { signal: ctl.signal })
    .then(function (r) { clearTimeout(t); return r; },
          function (e) { clearTimeout(t); throw e; });
}

function fetchState() {
  return fetchTO('/dashboards/state', 10000)
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (d && typeof d === 'object') OGDS.state = d;
      return d;
    })
    .catch(function () { return null; });
}

function ensureSurface(name) {
  var rec = surfaces[name];
  if (!rec) rec = surfaces[name] = { renderer: null };
  if (rec.el) return rec;
  var el = document.createElement('section');
  el.className = 'og-dash';
  el.id = 'ogDash-' + name;
  el.hidden = true;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  var x = document.createElement('button');
  x.className = 'og-dash-x';
  x.type = 'button';
  x.setAttribute('aria-label', 'Close dashboard');
  x.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
  x.addEventListener('click', function () { OGDS.close(); });
  var body = document.createElement('div');
  body.className = 'og-dash-body';
  el.appendChild(x);
  el.appendChild(body);
  document.body.appendChild(el);
  rec.el = el;
  rec.bodyEl = body;
  rec.cleanup = null;
  return rec;
}

function runCleanup(rec) {
  if (rec && typeof rec.cleanup === 'function') {
    try { rec.cleanup(); } catch (e) { }
  }
  if (rec) rec.cleanup = null;
}

/* Ticket L2 (2026-10-22, Brent's phone drill): a
   full-screen surface is a PAGE, not a layer stacked over
   a live one. The landed scaffold left the whole app
   running underneath the opaque overlay — chat visible,
   hero alive, the gold rain canvas animating at full
   rate under the surface — and the surface scroller
   chained its scroll to that page (overscroll-behavior
   auto). On the phone's compositor that stack is what
   smeared: cards ghosted over each other mid-scroll and
   the page read as a dimmed overlay on chat. So while a
   full-screen surface is open, the stack is REMOVED, not
   compensated: page mode (the html.og-dash-page rules in
   og_dashboards.css) takes every other top-level layer
   out of the render tree, and the rain engine stops
   (OGRAIN.stop — it restarts on close). The profile
   SHEET is exempt by design: a sheet is a sheet (its
   scrim look is the approved avatar-tap surface), so a
   surface whose element carries .og-dash--sheet never
   engages page mode. mount() is untouched — the Manager
   lives inside the Progress panel, not the overlay. */
function syncPageMode() {
  var on = false;
  if (openName) {
    var rec = surfaces[openName];
    if (rec && rec.el &&
        !rec.el.classList.contains('og-dash--sheet')) {
      on = true;
    }
  }
  document.documentElement.classList.toggle(
    'og-dash-page', on);
  if (window.OGRAIN) {
    if (on && OGRAIN.stop) OGRAIN.stop();
    if (!on && OGRAIN.start) OGRAIN.start();
  }
}

function open(name) {
  var rec = surfaces[name];
  if (!rec || typeof rec.renderer !== 'function') {
    return Promise.resolve(false);
  }
  return fetchState().then(function (st) {
    if (!st || !st[name]) return false;
    if (openName && openName !== name) OGDS.close();
    var r = ensureSurface(name);
    r.el.hidden = false;
    openName = name;
    OGDS.current = name;
    var ret = null;
    try { ret = r.renderer(r.bodyEl); } catch (e) { ret = null; }
    if (ret && typeof ret.then === 'function') {
      ret.then(function (cleanup) {
        if (typeof cleanup === 'function') r.cleanup = cleanup;
      });
    } else if (typeof ret === 'function') {
      r.cleanup = ret;
    }
    syncPageMode();
    return true;
  });
}

function close() {
  if (!openName) return;
  var rec = surfaces[openName];
  runCleanup(rec);
  if (rec && rec.el) rec.el.hidden = true;
  openName = null;
  OGDS.current = null;
  syncPageMode();
}

/* mount(name, el): render a surface into a HOST element
   instead of the full-screen overlay — the Manager's
   home inside the Progress panel (Brent, 2026-10-16
   16:09). Same state gate as open(). Resolves to the
   renderer's cleanup function (or null when the surface
   is unavailable / the renderer returned none); the
   HOST owns the cleanup and must call it when it stops
   showing the surface, so the renderer's timers die
   with the panel exactly as they die with the overlay. */
function mount(name, el) {
  var rec = surfaces[name];
  if (!rec || typeof rec.renderer !== 'function' || !el) {
    return Promise.resolve(null);
  }
  return fetchState().then(function (st) {
    if (!st || !st[name]) return null;
    var ret = null;
    try { ret = rec.renderer(el); } catch (e) { ret = null; }
    if (ret && typeof ret.then === 'function') {
      return ret.then(function (cleanup) {
        return typeof cleanup === 'function' ? cleanup : null;
      });
    }
    return typeof ret === 'function' ? ret : null;
  });
}

function registerSurface(name, rendererFn) {
  var rec = surfaces[name];
  if (!rec) rec = surfaces[name] = { renderer: null };
  rec.renderer = rendererFn;
}

var OGDS = {
  open: open,
  close: close,
  mount: mount,
  registerSurface: registerSurface,
  state: null,
  current: null
};
window.OGDS = OGDS;

/* --- Entry (a): the avatar rebind ---------------------------------------
   The page's inline og-profile-js binds '.og-top-ava' and
   '#emptyState .og-ava-wrap' clicks to openProfile(). This
   scaffold does NOT touch those listeners, the overlay, or
   window.ogOpenProfile. Instead, ONE capture-phase listener
   on document — capture on an ancestor runs BEFORE the
   target's own handlers — intercepts the tap only while a
   /dashboards/state read has armed the profile sheet, and
   stops the event so the profile handler never sees it.
   While the sheet is off (or state is unread), nothing is
   intercepted and the tap behaves exactly as before; it
   never falls back to opening the manager.
   Doors the profile keeps: its own X (profileCloseBtn),
   its scrim (profileScrim), its share button, the
   programmatic window.ogOpenProfile door, and both avatar
   taps whenever the sheet is disabled. The menu-head
   avatar (.og-menu-ava) has NO click binding anywhere in
   the page (verified by audit) — it opens nothing, before
   or after this rebind. */
document.addEventListener('click', function (e) {
  if (!profileArmed) return;
  var t = e.target;
  if (!t || !t.closest) return;
  var hit = t.closest('.og-top-ava') ||
    (t.closest('.og-ava-wrap') && t.closest('#emptyState'));
  if (!hit) return;
  e.preventDefault();
  e.stopPropagation();
  e.stopImmediatePropagation();
  open('profile');
}, true);

fetchState().then(function (st) {
  profileArmed = !!(st && st.profile);
});

/* --- Entry (b): RETIRED 2026-10-16 (Brent, 16:09) -------------------------
   The #ogManagerBtn button at the bottom of the Progress
   panel is gone: the Manager Dashboard now lives INSIDE
   the Progress panel as its only content. og_shell.js
   mounts the 'manager' renderer into the panel through
   OGDS.mount when the Progress seat opens and runs its
   cleanup when the seat closes. No click path opens the
   manager overlay any more. */

/* --- Entry (c): swipes on the main chat surface ----------------------------
   Horizontal swipe >= 70px; a vertical-dominant gesture is
   ignored. Ignored while any dashboard / menu / profile /
   approvals / seat surface is open, while the age gate is
   up, and when the gesture starts on an input/textarea. */
function overlayOpen() {
  if (openName) return true;
  var prof = document.getElementById('ogProfile');
  if (prof && !prof.hidden) return true;
  var menu = document.getElementById('sideMenu');
  if (menu && menu.classList.contains('open')) return true;
  var appr = document.getElementById('ogApprovalsPanel');
  if (appr && !appr.hidden) return true;
  var seat = document.getElementById('ogSeatScreen');
  if (seat && !seat.hidden) return true;
  var gate = document.getElementById('ageGate');
  if (gate && !gate.hidden) return true;
  return false;
}

var touchStart = null;
document.addEventListener('touchstart', function (e) {
  if (e.touches && e.touches.length === 1) {
    touchStart = { x: e.touches[0].clientX,
                   y: e.touches[0].clientY,
                   target: e.target };
  } else {
    touchStart = null;
  }
}, { passive: true });

document.addEventListener('touchend', function (e) {
  var start = touchStart;
  touchStart = null;
  if (!start || !e.changedTouches || !e.changedTouches.length) {
    return;
  }
  var dx = e.changedTouches[0].clientX - start.x;
  var dy = e.changedTouches[0].clientY - start.y;
  if (Math.abs(dx) < 70) return;
  if (Math.abs(dx) <= Math.abs(dy)) return;
  var t = start.target;
  if (t && t.closest &&
      t.closest('input, textarea, select, [contenteditable="true"]')) {
    return;
  }
  if (overlayOpen()) return;
  if (dx < 0) open('crypto');
  else open('fin');
}, { passive: true });

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && openName) close();
});

/* === The Manager Dashboard renderer (Round 267) ==========================
   No header banner, no fake-task button of any kind — the
   page opens straight onto its content under a slim
   control row: Refresh (a real re-read), Export (the
   caller's own records), and a page-side auto-refresh
   toggle (default OFF; 30s while open only, dead on
   close via the scaffold cleanup contract). */
function h(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function fmtElapsed(s) {
  if (s == null || isNaN(s)) return '—';
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

function fmtClock(ts) {
  var n = Number(ts || 0);
  if (!n) return '';
  var d = new Date(n * 1000);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined,
    { month: 'short', day: 'numeric', hour: 'numeric',
      minute: '2-digit' });
}

function sectionShell(parent, title) {
  var sec = h('section', 'og-dash-sec');
  sec.appendChild(h('h3', 'og-dash-sec-title', title));
  parent.appendChild(sec);
  return sec;
}

function lineInto(sec, text) {
  sec.appendChild(h('p', 'og-dash-line', text));
}

function renderManager(bodyEl) {
  bodyEl.innerHTML = '';
  var closed = false;
  var timer = null;

  var controls = h('div', 'og-dash-controls');
  var refreshBtn = h('button', 'og-dash-btn', 'Refresh');
  refreshBtn.type = 'button';
  var exportLink = h('a', 'og-dash-btn og-dash-export', 'Export Data');
  exportLink.href = '/crewdash/export';
  var autoLabel = h('label', 'og-dash-auto');
  var autoBox = document.createElement('input');
  autoBox.type = 'checkbox';
  autoBox.checked = false;
  autoLabel.appendChild(autoBox);
  autoLabel.appendChild(h('span', null, 'Auto-refresh'));
  controls.appendChild(refreshBtn);
  controls.appendChild(exportLink);
  controls.appendChild(autoLabel);
  bodyEl.appendChild(controls);

  var content = h('div', 'og-dash-content');
  bodyEl.appendChild(content);

  function stopTimer() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  autoBox.addEventListener('change', function () {
    stopTimer();
    if (autoBox.checked && !closed) {
      timer = setInterval(load, 30000);
    }
  });
  refreshBtn.addEventListener('click', function () { load(); });

  function load() {
    if (closed) return;
    fetchTO('/crewdash/data', 15000)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (!closed) paint(data); })
      .catch(function () { if (!closed) paint(null); });
  }

  function paint(data) {
    content.innerHTML = '';
    if (!data || data.ok !== true) {
      lineInto(content, 'Dashboard data unavailable — try Refresh.');
      return;
    }
    paintOverview(data.overview || {});
    paintPerformance(data.performance || {});
    paintHelpers(data.helpers || {});
    paintRecent(data.recent || {});
    paintComms(data.communication || {});
    paintDistribution(data.distribution || {});
  }

  function figure(grid, label, value, sub) {
    var f = h('div', 'og-dash-fig');
    f.appendChild(h('div', 'og-dash-fig-label', label));
    f.appendChild(h('div', 'og-dash-fig-value', value));
    if (sub) f.appendChild(h('div', 'og-dash-fig-sub', sub));
    grid.appendChild(f);
  }

  function paintOverview(ov) {
    var sec = sectionShell(content, 'System Overview');
    if (ov.unavailable) {
      lineInto(sec, ov.line || 'Crew status unavailable');
      return;
    }
    var grid = h('div', 'og-dash-figs');
    sec.appendChild(grid);
    figure(grid, 'Crew Status', ov.crew_status || '—',
      ov.crew_on ? 'Crew: on' : 'Crew: off');
    figure(grid, 'Active Helpers', String(ov.active_helpers),
      (ov.active_seats || []).join(', '));
    figure(grid, 'Total Tasks', String(ov.total_tasks));
    if (ov.success_rate != null) {
      figure(grid, 'Success Rate', ov.success_rate + '%',
        ov.done + ' of ' + ov.finished + ' finished');
    } else {
      figure(grid, 'Success Rate', ov.success_line || '—');
    }
    if (ov.avg_response_s != null) {
      var sub = 'over ' + ov.avg_timed + ' timed job' +
        (ov.avg_timed === 1 ? '' : 's');
      if (ov.avg_untimed) sub += ' · ' + ov.avg_untimed +
        ' untimed excluded';
      figure(grid, 'Avg Response Time',
        fmtElapsed(ov.avg_response_s), sub);
    } else {
      figure(grid, 'Avg Response Time', ov.avg_line || '—');
    }
  }

  function paintPerformance(pf) {
    var sec = sectionShell(content, 'Performance');
    if (pf.unavailable) {
      lineInto(sec, pf.line || 'Performance unavailable');
      return;
    }
    if (!pf.chartable) {
      lineInto(sec, pf.empty_line ||
        'Not enough finished work yet to chart.');
      return;
    }
    var max = 1;
    (pf.days || []).forEach(function (d) {
      if (d.count > max) max = d.count;
    });
    var chart = h('div', 'og-dash-chart');
    (pf.days || []).forEach(function (d) {
      var col = h('div', 'og-dash-bar-col');
      col.appendChild(h('div', 'og-dash-bar-count',
        String(d.count)));
      var bar = h('div', 'og-dash-bar');
      bar.style.height =
        Math.max(4, Math.round(100 * d.count / max)) + '%';
      col.appendChild(bar);
      col.appendChild(h('div', 'og-dash-bar-day', d.label));
      chart.appendChild(col);
    });
    sec.appendChild(chart);
    lineInto(sec, 'Finished per day, trailing 14 days · ' +
      pf.total_completed + ' total');
  }

  function paintHelpers(hp) {
    var sec = sectionShell(content, 'Helper Status');
    if (hp.unavailable) {
      lineInto(sec, hp.line || 'Helper list unavailable');
    }
    (hp.rows || []).forEach(function (row) {
      var r = h('div', 'og-helper-row');
      r.appendChild(h('span', 'og-helper-name', row.name));
      r.appendChild(h('span',
        'og-helper-state is-' + String(row.state || '')
          .toLowerCase(), row.state));
      if (row.load) {
        r.appendChild(h('span', 'og-helper-load', row.load));
      }
      r.appendChild(h('span', 'og-helper-cap',
        row.capabilities != null
          ? String(row.capabilities) : '—'));
      sec.appendChild(r);
    });
  }

  function paintRecent(rc) {
    var sec = sectionShell(content, 'Recent Tasks');
    if (rc.unavailable) {
      lineInto(sec, rc.line || 'Recent tasks unavailable');
      return;
    }
    if (!(rc.rows || []).length) {
      lineInto(sec, 'No crew jobs on record yet.');
      return;
    }
    rc.rows.forEach(function (row) {
      var r = h('div', 'og-dash-task');
      var top = h('div', 'og-dash-task-top');
      top.appendChild(h('span', 'og-dash-task-title',
        row.title));
      top.appendChild(h('span', 'og-dash-chip',
        row.state_label || row.state));
      r.appendChild(top);
      var meta = row.role_label || '';
      if (row.elapsed_s != null) {
        meta += (meta ? ' · ' : '') + fmtElapsed(row.elapsed_s);
      } else {
        meta += (meta ? ' · ' : '') + '—';
      }
      r.appendChild(h('div', 'og-dash-task-meta', meta));
      sec.appendChild(r);
    });
  }

  function paintComms(cm) {
    var sec = sectionShell(content, 'Communication Hub');
    if (cm.unavailable) {
      lineInto(sec, cm.line || 'Communication record unavailable');
      return;
    }
    if (!(cm.rows || []).length) {
      lineInto(sec, cm.empty_line || 'No deliveries yet.');
      return;
    }
    cm.rows.forEach(function (row) {
      var r = h('div', 'og-dash-comm');
      var head = (row.seat || 'OG') +
        (row.ts ? ' · ' + fmtClock(row.ts) : '');
      r.appendChild(h('div', 'og-dash-comm-head', head));
      if (row.title) {
        r.appendChild(h('div', 'og-dash-comm-title', row.title));
      }
      if (row.text) {
        r.appendChild(h('div', 'og-dash-comm-text', row.text));
      }
      sec.appendChild(r);
    });
  }

  function paintDistribution(ds) {
    var sec = sectionShell(content, 'Task Distribution');
    if (ds.unavailable) {
      lineInto(sec, ds.line || 'Task distribution unavailable');
      return;
    }
    (ds.rows || []).forEach(function (row) {
      var r = h('div', 'og-helper-row');
      r.appendChild(h('span', 'og-helper-name', row.label));
      r.appendChild(h('span', 'og-helper-load',
        'running ' + row.running + ' · done ' + row.done +
        ' · failed ' + row.failed));
      sec.appendChild(r);
    });
    lineInto(sec, 'Total Completed: ' + ds.total_completed);
  }

  load();
  return function cleanup() {
    closed = true;
    stopTimer();
  };
}

registerSurface('manager', renderManager);

/* === The Profile Sheet renderer ==========================================
   A NEW, separate surface ('profile'), Brent 2026-10-16
   10:04/10:13: tapping OG's avatar opens this sheet alone;
   the Manager keeps only its Progress-panel button. The
   sheet is a window, not a control: approvals are viewed,
   never decided here, and Identity is view-only with no
   edit controls of any kind. Status text comes from the
   payload only; a failed read shows the failure line. */
function renderProfileSheet(bodyEl) {
  bodyEl.innerHTML = '';
  var closed = false;
  var rootEl = null;
  if (bodyEl.closest) rootEl = bodyEl.closest('.og-dash');
  if (!rootEl && bodyEl.parentElement) rootEl = bodyEl.parentElement;
  if (rootEl && rootEl.classList) {
    rootEl.classList.add('og-dash--sheet');
  }

  var avaSrc = null;
  var topImg = document.querySelector('.og-top-ava img');
  if (topImg) avaSrc = topImg.getAttribute('src') || topImg.src || null;

  var sheet = h('div', 'og-sheet');
  bodyEl.appendChild(sheet);

  var header = h('div', 'og-sheet-header');
  if (avaSrc) {
    var ava = h('img', 'og-sheet-avatar');
    ava.src = avaSrc;
    ava.alt = '';
    header.appendChild(ava);
  }
  var headText = h('div', 'og-sheet-head-text');
  headText.appendChild(h('div', 'og-sheet-name', 'OG'));
  var statusEl = h('div', 'og-sheet-status', 'Loading\u2026');
  headText.appendChild(statusEl);
  var statusDetail = h('div', 'og-sheet-status-detail', '');
  statusDetail.hidden = true;
  headText.appendChild(statusDetail);
  header.appendChild(headText);
  sheet.appendChild(header);

  var controls = h('div', 'og-sheet-controls');
  var refreshBtn = h('button', 'og-sheet-btn', 'Refresh');
  refreshBtn.type = 'button';
  controls.appendChild(refreshBtn);
  sheet.appendChild(controls);
  refreshBtn.addEventListener('click', function () { load(); });

  var tabDefs = [
    { id: 'today', label: 'Today',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>' },
    { id: 'approvals', label: 'Approvals',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><polyline points="9 12 11 14 15 10"/></svg>' },
    { id: 'browser', label: 'Browser',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="3" y1="12" x2="21" y2="12"/><path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z"/></svg>' },
    { id: 'scheduled', label: 'Scheduled',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></svg>' },
    { id: 'identity', label: 'Identity',
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.5-6 8-6s8 2 8 6"/></svg>' }
  ];
  var tabRow = h('div', 'og-sheet-tabs');
  sheet.appendChild(tabRow);
  var panelsEl = h('div', 'og-sheet-content');
  sheet.appendChild(panelsEl);
  var tabBtns = {};
  var panels = {};
  tabDefs.forEach(function (def) {
    var btn = h('button', 'og-sheet-tab');
    btn.type = 'button';
    btn.setAttribute('data-tab', def.id);
    var iconSpan = h('span', 'og-sheet-tab-icon');
    iconSpan.innerHTML = def.icon;
    btn.appendChild(iconSpan);
    btn.appendChild(h('span', 'og-sheet-tab-label', def.label));
    btn.addEventListener('click', function () { showTab(def.id); });
    tabRow.appendChild(btn);
    tabBtns[def.id] = btn;
    var panel = h('div', 'og-sheet-panel');
    panel.hidden = true;
    panelsEl.appendChild(panel);
    panels[def.id] = panel;
  });

  function showTab(id) {
    tabDefs.forEach(function (def) {
      var on = def.id === id;
      panels[def.id].hidden = !on;
      if (on) tabBtns[def.id].classList.add('is-active');
      else tabBtns[def.id].classList.remove('is-active');
    });
  }
  showTab('today');

  function unavailableLine(panel, section) {
    var text = (section && section.empty_line) || 'Unavailable';
    panel.appendChild(h('p', 'og-sheet-unavailable', text));
  }

  function load() {
    if (closed) return;
    fetchTO('/crewdash/sheet?tzoff=' + (-new Date().getTimezoneOffset()), 15000)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (!closed) paint(data); })
      .catch(function () { if (!closed) paint(null); });
  }

  function paint(data) {
    if (!data || data.ok !== true) {
      statusEl.textContent = 'Status unavailable';
      statusDetail.hidden = true;
      statusDetail.textContent = '';
      paintToday(null);
      paintApprovals(null);
      paintBrowser(null);
      paintScheduled(null);
      paintIdentity(null);
      return;
    }
    if (data.status && data.status.text) {
      statusEl.textContent = data.status.text;
    } else {
      statusEl.textContent = 'Status unavailable';
    }
    if (data.status && data.status.detail) {
      statusDetail.textContent = data.status.detail;
      statusDetail.hidden = false;
    } else {
      statusDetail.textContent = '';
      statusDetail.hidden = true;
    }
    paintToday(data.today || null);
    paintApprovals(data.approvals || null);
    paintBrowser(data.browser || null);
    paintScheduled(data.scheduled || null);
    paintIdentity(data.identity || null);
  }

  function paintToday(section) {
    var panel = panels.today;
    panel.innerHTML = '';
    if (!section || section.unavailable) {
      unavailableLine(panel, section);
      return;
    }
    var rows = section.rows || [];
    if (!rows.length) {
      panel.appendChild(h('p', 'og-sheet-empty',
        section.empty_line || 'Nothing yet today.'));
      return;
    }
    rows.forEach(function (row) {
      var r = h('div', 'og-sheet-row');
      var when = fmtClock(row.ts);
      if (when) r.appendChild(h('span', 'og-sheet-time', when));
      r.appendChild(h('span', 'og-sheet-text', row.text || ''));
      panel.appendChild(r);
    });
  }

  function paintApprovals(section) {
    var panel = panels.approvals;
    panel.innerHTML = '';
    if (!section || section.unavailable) {
      unavailableLine(panel, section);
      return;
    }
    var pending = section.pending || [];
    var history = section.history || [];
    if (!pending.length && !history.length) {
      panel.appendChild(h('p', 'og-sheet-empty',
        section.empty_line || 'Nothing waiting on you.'));
      return;
    }
    panel.appendChild(h('div', 'og-sheet-group-title',
      'Waiting on you'));
    if (!pending.length) {
      panel.appendChild(h('p', 'og-sheet-empty',
        'Nothing waiting on you.'));
    }
    pending.forEach(function (row) {
      var card = h('div', 'og-sheet-card');
      if (row.title) {
        card.appendChild(h('div', 'og-sheet-card-title', row.title));
      }
      if (row.line) {
        card.appendChild(h('div', 'og-sheet-card-line', row.line));
      }
      var meta = '';
      if (row.ts && Number(row.ts) > 0 && fmtClock(row.ts)) {
        meta = 'Waiting since ' + fmtClock(row.ts);
      }
      if (row.expires && fmtClock(row.expires)) {
        meta += (meta ? ' \u00b7 ' : '') + 'expires ' +
          fmtClock(row.expires);
      }
      if (meta) {
        card.appendChild(h('div', 'og-sheet-card-meta', meta));
      }
      panel.appendChild(card);
    });
    panel.appendChild(h('div', 'og-sheet-group-title', 'History'));
    if (!history.length) {
      panel.appendChild(h('p', 'og-sheet-empty', 'No history yet.'));
    }
    history.forEach(function (row) {
      var card = h('div', 'og-sheet-card');
      if (row.title) {
        card.appendChild(h('div', 'og-sheet-card-title', row.title));
      }
      if (row.line) {
        card.appendChild(h('div', 'og-sheet-card-line', row.line));
      }
      if (row.ts && fmtClock(row.ts)) {
        card.appendChild(h('div', 'og-sheet-card-meta',
          fmtClock(row.ts)));
      }
      panel.appendChild(card);
    });
  }

  function paintBrowser(section) {
    var panel = panels.browser;
    panel.innerHTML = '';
    if (!section || section.unavailable) {
      unavailableLine(panel, section);
      return;
    }
    var session = section.session || null;
    var pending = section.pending || null;
    if (!session && !pending) {
      panel.appendChild(h('p', 'og-sheet-empty',
        section.empty_line || 'No browser task right now.'));
      return;
    }
    if (session) {
      var card = h('div', 'og-sheet-card');
      if (session.site) {
        card.appendChild(h('div', 'og-sheet-card-title',
          session.site));
      }
      var line = session.title || session.goal || session.url || '';
      if (line) {
        card.appendChild(h('div', 'og-sheet-card-line', line));
      }
      var meta = '';
      if (session.started && fmtClock(session.started)) {
        meta = 'started ' + fmtClock(session.started);
      }
      var controlText = (session.control === 'user'
          || session.control === 'visitor')
        ? "You're in control" : 'OG is driving';
      meta += (meta ? ' \u00b7 ' : '') + controlText;
      card.appendChild(h('div', 'og-sheet-card-meta', meta));
      if (session.last_action) {
        /* the builder emits last_action as epoch SECONDS
           (og_crewdash._f) — paint it as a clock time,
           never the raw number (Drill 5 clientfix). */
        var la = fmtClock(session.last_action);
        card.appendChild(h('div', 'og-sheet-card-line',
          la ? 'Last action ' + la
             : String(session.last_action)));
      }
      panel.appendChild(card);
    }
    if (pending) {
      var pcard = h('div', 'og-sheet-card');
      var ptext = 'Waiting for your OK';
      var pgoal = pending.goal || pending.site || pending.url || '';
      if (pgoal) ptext += ' \u2014 ' + pgoal;
      pcard.appendChild(h('div', 'og-sheet-card-title', ptext));
      if (pending.site && pending.goal) {
        pcard.appendChild(h('div', 'og-sheet-card-line',
          pending.site));
      }
      panel.appendChild(pcard);
    }
  }

  function paintScheduled(section) {
    var panel = panels.scheduled;
    panel.innerHTML = '';
    if (!section || section.unavailable) {
      unavailableLine(panel, section);
      return;
    }
    var rows = section.rows || [];
    if (!rows.length) {
      panel.appendChild(h('p', 'og-sheet-empty',
        section.empty_line || 'Nothing scheduled.'));
      return;
    }
    rows.forEach(function (row) {
      var r = h('div', 'og-sheet-card');
      if (row.label) {
        r.appendChild(h('div', 'og-sheet-card-title', row.label));
      }
      if (row.detail) {
        r.appendChild(h('div', 'og-sheet-card-line', row.detail));
      }
      if (row.status_label) {
        r.appendChild(h('div', 'og-sheet-card-meta',
          row.status_label));
      }
      panel.appendChild(r);
    });
  }

  function paintIdentity(section) {
    var panel = panels.identity;
    panel.innerHTML = '';
    if (!section) {
      panel.appendChild(h('p', 'og-sheet-unavailable',
        'Unavailable'));
      return;
    }
    var persona = section.persona || {};
    var pcard = h('div', 'og-sheet-card');
    if (avaSrc) {
      var pava = h('img', 'og-sheet-identity-avatar');
      pava.src = avaSrc;
      pava.alt = '';
      pcard.appendChild(pava);
    }
    pcard.appendChild(h('div', 'og-sheet-card-title',
      persona.name || 'OG'));
    if (persona.line) {
      pcard.appendChild(h('div', 'og-sheet-card-line',
        persona.line));
    }
    panel.appendChild(pcard);
    var memory = section.memory || {};
    var mcard = h('div', 'og-sheet-card');
    mcard.appendChild(h('div', 'og-sheet-card-title', 'Memory'));
    if (memory.available) {
      var mline = String(memory.entries != null
        ? memory.entries : 0) + ' entries';
      mcard.appendChild(h('div', 'og-sheet-card-line', mline));
      if (memory.updated && fmtClock(memory.updated)) {
        mcard.appendChild(h('div', 'og-sheet-card-meta',
          'updated ' + fmtClock(memory.updated)));
      }
    } else if (memory.line) {
      mcard.appendChild(h('div', 'og-sheet-card-line',
        memory.line));
    }
    panel.appendChild(mcard);
    if (section.account) {
      var aline = section.account.email || '';
      if (section.account.created &&
          fmtClock(section.account.created)) {
        aline += (aline ? ' \u00b7 ' : '') + 'member since ' +
          fmtClock(section.account.created);
      }
      if (aline) {
        panel.appendChild(h('p', 'og-sheet-account', aline));
      }
    }
  }

  load();
  return function cleanup() {
    closed = true;
    if (rootEl && rootEl.classList) {
      rootEl.classList.remove('og-dash--sheet');
    }
  };
}

registerSurface('profile', renderProfileSheet);
})();

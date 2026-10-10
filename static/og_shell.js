/* Round 52 — OG chat shell behavior (Brent's design, 2026-10-10).
   The composer node is relocated into the floating dock (every id and
   listener survives the move — wiring is id-based), the five seats ride
   the surfaces that already exist (Library, the Add sheet, the side
   menu), and the Approvals seat opens a panel over the SAME browser
   pending the in-chat approval card shows, decided through the SAME
   endpoints (/browser/approve|decline|allow). Nothing here starts a
   session, sends a message, or moves money. */
(function () {
function $(id) { return document.getElementById(id); }

/* --- 0. Shell markup (built here: the page is at its push ceiling) ----- */
var shellRoot = document.createElement('div');
shellRoot.innerHTML =
'<div id="ogShellDock">' +
'<div id="ogShellComposerSlot"></div>' +
'<nav id="ogSeatBar" aria-label="OG sections">' +
'<button class="og-seat is-on" id="ogSeatChat" type="button"><svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="og-seat-label">Chat</span></button>' +
'<button class="og-seat" id="ogSeatLibrary" type="button"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg><span class="og-seat-label">Library</span></button>' +
'<button class="og-seat" id="ogSeatActions" type="button"><svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg><span class="og-seat-label">All actions</span></button>' +
'<button class="og-seat" id="ogSeatApprovals" type="button"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><span class="og-seat-label">Approvals</span><span id="ogSeatApprDot" class="og-seat-dot" hidden></span></button>' +
'<button class="og-seat" id="ogSeatMenu" type="button"><svg viewBox="0 0 24 24"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg><span class="og-seat-label">Menu</span></button>' +
'</nav>' +
'</div>' +
'<div id="ogApprovalsScrim" class="og-appr-scrim" hidden></div>' +
'<section id="ogApprovalsPanel" class="og-appr-panel" hidden role="dialog" aria-modal="true" aria-label="Approvals">' +
'<div class="og-appr-head"><span class="og-appr-title">Approvals</span><button id="ogApprCloseBtn" class="og-circle-btn" type="button" aria-label="Close approvals"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button></div>' +
'<div class="og-appr-scroll">' +
'<div id="ogApprCard" class="og-appr-card" hidden>' +
'<div class="og-approve-head">⚠️ <span>OG is waiting on you</span></div>' +
'<p id="ogApprDesc" class="og-approve-desc"></p>' +
'<blockquote id="ogApprText" class="og-approve-text" hidden></blockquote>' +
'<p id="ogApprExp" class="og-approve-exp"></p>' +
'<div class="og-approve-btns">' +
'<button id="ogApprYesBtn" type="button">✓ Approve</button>' +
'<button id="ogApprNoBtn" type="button">✕ Decline</button>' +
'<button id="ogApprAlwaysBtn" type="button" hidden>Always allow this site</button>' +
'</div>' +
'<p id="ogApprOutcome" class="og-approve-outcome" hidden></p>' +
'</div>' +
'<p id="ogApprEmpty" class="og-appr-empty">Nothing waiting on you — when OG needs a yes or no, it parks here.</p>' +
'<h3 class="og-appr-sec" id="ogApprRecentSec" hidden>Recent</h3>' +
'<div id="ogApprList"></div>' +
'<h3 class="og-appr-sec" id="ogApprFixSec" hidden>Fixes waiting</h3>' +
'<div id="ogApprFixList"></div>' +
'</div>' +
'</section>';
document.body.appendChild(shellRoot);

/* --- 1. Composer into the dock --------------------------------------- */
var area = document.querySelector('.input-area'), slot = $('ogShellComposerSlot');
if (area && slot && area.parentNode !== slot) slot.appendChild(area);

/* --- 2. Shell hides while the auth wall is up ------------------------- */
var gate = $('ageGate');
function syncGate() {
  document.body.classList.toggle('og-shell-off', !!(gate && !gate.hidden));
}
if (gate && window.MutationObserver) {
  new MutationObserver(syncGate).observe(gate, { attributes: true, attributeFilter: ['hidden'] });
}
syncGate();

/* --- 3. Seats ---------------------------------------------------------- */
var seats = {
  chat: $('ogSeatChat'), library: $('ogSeatLibrary'),
  actions: $('ogSeatActions'), approvals: $('ogSeatApprovals'),
  menu: $('ogSeatMenu')
};
var apprDot = $('ogSeatApprDot');

function clickIf(id) { var el = $(id); if (el) el.click(); }
function isHidden(el) { return !el || el.hidden; }

function closeApprovals() {
  var p = $('ogApprovalsPanel'), s = $('ogApprovalsScrim');
  if (p) p.hidden = true;
  if (s) s.hidden = true;
}
function closeAllViews() {
  var menu = $('sideMenu');
  if (menu && menu.classList.contains('open')) clickIf('menuClose');
  if (!isHidden($('ogProfile'))) clickIf('profileCloseBtn');
  if (!isHidden($('ogNotifyPanel'))) clickIf('notifyCloseBtn');
  if (!isHidden($('ogLibrary'))) clickIf('libraryCloseBtn');
  if (!isHidden($('ogConnections'))) clickIf('connectionsCloseBtn');
  if ($('nsetCloseBtn')) clickIf('nsetCloseBtn');
  var sheet = $('plusSheet');
  if (sheet && !sheet.hidden) clickIf('plusBtn');
  closeApprovals();
}

function currentSeat() {
  if (!isHidden($('ogApprovalsPanel'))) return 'approvals';
  var menu = $('sideMenu');
  if (menu && menu.classList.contains('open')) return 'menu';
  if (!isHidden($('ogLibrary'))) return 'library';
  var sheet = $('plusSheet');
  if (sheet && !sheet.hidden) return 'actions';
  return 'chat';
}
function paintSeats() {
  var cur = currentSeat();
  Object.keys(seats).forEach(function (k) {
    if (seats[k]) seats[k].classList.toggle('is-on', k === cur);
  });
}

if (seats.chat) seats.chat.addEventListener('click', function () { closeAllViews(); paintSeats(); });
if (seats.library) seats.library.addEventListener('click', function () {
  if (typeof window.ogOpenLibrary === 'function') window.ogOpenLibrary();
  else clickIf('menuLibraryBtn');
  paintSeats();
});
if (seats.actions) seats.actions.addEventListener('click', function () { clickIf('plusBtn'); paintSeats(); });
if (seats.menu) seats.menu.addEventListener('click', function () {
  var menu = $('sideMenu');
  if (menu && menu.classList.contains('open')) clickIf('menuClose');
  else clickIf('menuBtn');
  paintSeats();
});
if (seats.approvals) seats.approvals.addEventListener('click', function () { openApprovals(); });
var apprClose = $('ogApprCloseBtn');
if (apprClose) apprClose.addEventListener('click', function () { closeApprovals(); paintSeats(); });
var apprScrim = $('ogApprovalsScrim');
if (apprScrim) apprScrim.addEventListener('click', function () { closeApprovals(); paintSeats(); });

/* --- 4. Approvals panel ------------------------------------------------- */
var pending = null, apprBusy = false, recentsCount = 0, outcomeUntil = 0;

/* Round 53: the seat dot also lights for an open fix proposal. */
var openFixCount = 0;
function updateDot() {
  if (apprDot) apprDot.hidden = !(pending || openFixCount > 0);
}

function openApprovals() {
  var p = $('ogApprovalsPanel'), s = $('ogApprovalsScrim');
  if (s) s.hidden = false;
  if (p) p.hidden = false;
  refreshPending();
  refreshRecents();
  refreshFixes();
  paintSeats();
}
window.ogOpenApprovals = openApprovals;

function fmtTime(ts) {
  var n = Number(ts || 0);
  if (!n) return '';
  var d = new Date(n > 1e12 ? n : n * 1000);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function renderPending() {
  var card = $('ogApprCard'), desc = $('ogApprDesc'), text = $('ogApprText'),
      exp = $('ogApprExp'), always = $('ogApprAlwaysBtn'), out = $('ogApprOutcome'),
      empty = $('ogApprEmpty');
  updateDot();
  if (!card) return;
  if (Date.now() < outcomeUntil) { card.hidden = false; return; }
  if (!pending) {
    card.hidden = true;
    if (empty) empty.hidden = recentsCount > 0 || openFixCount > 0;
    return;
  }
  if (empty) empty.hidden = true;
  card.hidden = false;
  var kindLabel = pending.kind === 'post' ? 'post this' : 'do this';
  if (desc) desc.textContent = 'OG is ready to ' + kindLabel + ': ' + (pending.desc || 'a browser action') + (pending.site ? ' on ' + pending.site : '') + '. Nothing happens unless you approve.';
  if (text) {
    if (pending.text) { text.hidden = false; text.textContent = '"' + pending.text + '"'; }
    else text.hidden = true;
  }
  if (exp) exp.textContent = pending.minutes_left != null ? '⏳ Expires in ~' + pending.minutes_left + ' min. You can also answer on the card in chat — whichever you use first counts.' : '';
  if (always) always.hidden = !pending.allow_site;
  if (out && !apprBusy) out.hidden = true;
}

function showApprOutcome(msg) {
  var out = $('ogApprOutcome'), card = $('ogApprCard');
  if (!out) return;
  if (card) card.hidden = false;
  out.hidden = false;
  out.textContent = msg;
  outcomeUntil = Date.now() + 30000;
}

function decide(path) {
  if (apprBusy) return;
  apprBusy = true;
  ['ogApprYesBtn', 'ogApprNoBtn', 'ogApprAlwaysBtn'].forEach(function (id) {
    var b = $(id); if (b) b.disabled = true;
  });
  fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (res && res.ok) showApprOutcome('✅ ' + (res.title || 'Done') + (res.body ? '\n' + String(res.body).replace(/^\[OG BROWSER\]\s*/, '') : ''));
      else showApprOutcome('⚠️ That didn\'t go through — ' + ((res && res.error) || 'the action may have expired or already been answered in chat.'));
    })
    .catch(function () { showApprOutcome('⚠️ That didn\'t go through — connection hiccup. The action is still parked if it reappears above.'); })
    .then(function () {
      apprBusy = false;
      ['ogApprYesBtn', 'ogApprNoBtn', 'ogApprAlwaysBtn'].forEach(function (id) {
        var b = $(id); if (b) b.disabled = false;
      });
      refreshPending();
      refreshRecents();
    });
}
var yesB = $('ogApprYesBtn'), noB = $('ogApprNoBtn'), alB = $('ogApprAlwaysBtn');
if (yesB) yesB.addEventListener('click', function () { decide('/browser/approve'); });
if (noB) noB.addEventListener('click', function () { decide('/browser/decline'); });
if (alB) alB.addEventListener('click', function () { decide('/browser/allow'); });

function refreshPending() {
  fetch('/browser/status')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (st) {
      pending = (st && st.enabled && st.pending_action) ? st.pending_action : null;
      renderPending();
    })
    .catch(function () { pending = null; renderPending(); });
}

function refreshRecents() {
  var list = $('ogApprList'), sec = $('ogApprRecentSec');
  if (!list) return;
  fetch('/notifications')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      var items = ((d && d.items) || []).filter(function (it) { return it && it.kind === 'approval_needed'; }).slice(0, 5);
      recentsCount = items.length;
      list.innerHTML = '';
      if (sec) sec.hidden = items.length === 0;
      items.forEach(function (it) {
        var row = document.createElement('div');
        row.className = 'og-appr-item' + (it.read ? '' : ' unread');
        var ico = document.createElement('span');
        ico.className = 'og-appr-ico'; ico.textContent = '✋';
        var main = document.createElement('div');
        main.className = 'og-appr-main';
        var t = document.createElement('div');
        t.className = 'og-appr-item-title'; t.textContent = it.title || 'Approval needed';
        var b = document.createElement('div');
        b.className = 'og-appr-item-body'; b.textContent = it.body || '';
        var w = document.createElement('div');
        w.className = 'og-appr-item-time'; w.textContent = fmtTime(it.ts);
        main.appendChild(t); main.appendChild(b); main.appendChild(w);
        row.appendChild(ico); row.appendChild(main);
        list.appendChild(row);
      });
      renderPending();
    })
    .catch(function () { });
}

/* --- 5. Fixes waiting (Round 53 fix-approval pipeline) ------------------
   OG prepares a fix for a problem he ran into; NOTHING runs until the
   owner approves here (or in chat). The list is the caller's own
   proposals from GET /approvals/proposals; the buttons POST the act
   route with the proposal id + the decision. */
function refreshFixes() {
  var list = $('ogApprFixList'), sec = $('ogApprFixSec');
  if (!list) return;
  fetch('/approvals/proposals')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      var items = (d && d.proposals) || [];
      openFixCount = (d && typeof d.open_count === 'number')
        ? d.open_count
        : items.filter(function (p) { return p && p.status === 'open'; }).length;
      list.innerHTML = '';
      if (sec) sec.hidden = items.length === 0;
      items.forEach(function (p) {
        if (!p) return;
        var row = document.createElement('div');
        row.className = 'og-appr-item';
        var main = document.createElement('div');
        main.className = 'og-appr-main';
        var t = document.createElement('div');
        t.className = 'og-appr-item-title';
        t.textContent = '🔧 ' + (p.title || 'Fix waiting');
        var b = document.createElement('div');
        b.className = 'og-appr-item-body';
        b.textContent = p.summary || '';
        main.appendChild(t); main.appendChild(b);
        if (p.status === 'open') {
          var btns = document.createElement('div');
          btns.className = 'og-appr-fix-btns';
          var yes = document.createElement('button');
          yes.type = 'button'; yes.className = 'og-appr-fix-yes';
          yes.textContent = '✓ Approve fix';
          var no = document.createElement('button');
          no.type = 'button'; no.className = 'og-appr-fix-no';
          no.textContent = '✕ Decline';
          yes.addEventListener('click', function () { decideFix(p.id, 'approve', yes, no); });
          no.addEventListener('click', function () { decideFix(p.id, 'decline', yes, no); });
          btns.appendChild(yes); btns.appendChild(no);
          main.appendChild(btns);
        } else {
          var w = document.createElement('div');
          w.className = 'og-appr-item-time';
          w.textContent = p.status === 'executed'
            ? (p.outcome === 'fixed' ? 'Fixed ✓ — OG re-checked and it is gone' : 'Fix didn\'t take — a fresh approval is needed to try again')
            : (p.status === 'handed_off' ? 'Steps handed over — in your hands now' : 'Declined');
          main.appendChild(w);
        }
        row.appendChild(main);
        list.appendChild(row);
      });
      updateDot();
      renderPending();
    })
    .catch(function () { });
}

function decideFix(id, decision, yesBtn, noBtn) {
  if (yesBtn) yesBtn.disabled = true;
  if (noBtn) noBtn.disabled = true;
  fetch('/approvals/proposals/act', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: id, decision: decision })
  })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      if (res && res.title) showApprOutcome((res.ok ? '✅ ' : '⚠️ ') + res.title + (res.body ? '\n' + res.body : ''));
      else showApprOutcome('⚠️ That didn\'t go through — ' + ((res && res.error) || 'try again in a moment.'));
    })
    .catch(function () { showApprOutcome('⚠️ That didn\'t go through — connection hiccup. The fix is still parked if it reappears above.'); })
    .then(function () { refreshFixes(); refreshPending(); });
}

refreshPending();
refreshFixes();
setInterval(refreshPending, 4000);
setInterval(refreshFixes, 4000);
setInterval(paintSeats, 900);
paintSeats();
})();

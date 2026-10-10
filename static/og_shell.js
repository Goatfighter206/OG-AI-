/* OG chat shell behavior (Round 52 design; Round 59 seats, 2026-10-10).
   The composer node is relocated into the floating dock (every id and
   listener survives the move — wiring is id-based). Round 59: the
   seat pill is FOUR seats the owner named — Chat · Goals · Ideas ·
   Feed. Goals/Ideas/Feed render in a content-area seat screen pinned
   BETWEEN the top cluster and the dock (z 7400 < dock 7600), so the
   composer dock + seat pill NEVER disappear while a seat is active;
   the old full-screen Library overlay (z 9003) is no longer a seat.
   The Approvals panel (Round 52/53) is unchanged and opens from
   Ideas, from Feed approval rows, and from the in-chat card, decided
   through the SAME endpoints. Notifications carry a Round 59
   "target" route descriptor; window.ogRouteTarget consumes it (the
   bell panel in og_notify.js looks it up lazily at click time).
   Nothing here starts a session, sends a message, or moves money. */
(function () {
function $(id) { return document.getElementById(id); }

/* --- 0. Shell markup (built here: the page is at its push ceiling) ----- */
var shellRoot = document.createElement('div');
shellRoot.innerHTML =
'<div id="ogShellDock">' +
'<div id="ogShellComposerSlot"></div>' +
'<nav id="ogSeatBar" aria-label="OG sections">' +
'<button class="og-seat is-on" id="ogSeatChat" type="button"><svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="og-seat-label">Chat</span></button>' +
'<button class="og-seat" id="ogSeatGoals" type="button"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg><span class="og-seat-label">Goals</span></button>' +
'<button class="og-seat" id="ogSeatIdeas" type="button"><svg viewBox="0 0 24 24"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.8.7 1 1.5 1 2.5h6c0-1 .2-1.8 1-2.5A6 6 0 0 0 12 3z"/></svg><span class="og-seat-label">Ideas</span><span id="ogSeatIdeasDot" class="og-seat-dot" hidden></span></button>' +
'<button class="og-seat" id="ogSeatFeed" type="button"><svg viewBox="0 0 24 24"><path d="M4 5h16"/><path d="M4 12h16"/><path d="M4 19h10"/></svg><span class="og-seat-label">Feed</span><span id="ogSeatFeedDot" class="og-seat-dot" hidden></span></button>' +
'</nav>' +
'</div>' +
'<section id="ogSeatScreen" hidden aria-label="OG section">' +
'<div class="og-seat-screen-head"><span id="ogSeatScreenTitle" class="og-seat-screen-title">Feed</span><button id="ogSeatScreenClose" class="og-circle-btn" type="button" aria-label="Back to chat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button></div>' +
'<p id="ogSeatNotice" class="og-seat-notice" hidden></p>' +
'<div id="ogGoalsView" class="og-seat-view" hidden>' +
'<p id="ogGoalsSummary" class="og-seat-summary">Loading…</p>' +
'<h3 class="og-appr-sec">Watching for you</h3>' +
'<div id="ogGoalsWatchList"></div>' +
'<h3 class="og-appr-sec" id="ogGoalsPriceSec" hidden>Price watches</h3>' +
'<div id="ogGoalsPriceList"></div>' +
'<p id="ogGoalsEmpty" class="og-appr-empty" hidden>OG isn\'t watching anything yet — ask in chat, like "watch for a red kayak under $200".</p>' +
'</div>' +
'<div id="ogIdeasView" class="og-seat-view" hidden>' +
'<div id="ogIdeasAppr" class="og-appr-card" hidden>' +
'<div class="og-approve-head">⚠️ <span>OG is waiting on you</span></div>' +
'<p id="ogIdeasApprDesc" class="og-approve-desc"></p>' +
'<div class="og-approve-btns"><button id="ogIdeasApprBtn" type="button">Open approvals</button></div>' +
'</div>' +
'<h3 class="og-appr-sec" id="ogIdeasFixSec" hidden>Fixes waiting</h3>' +
'<div id="ogIdeasFixList"></div>' +
'<h3 class="og-appr-sec" id="ogIdeasOfferSec" hidden>OG\'s system check</h3>' +
'<div id="ogIdeasOfferList"></div>' +
'<p id="ogIdeasEmpty" class="og-appr-empty" hidden>Nothing waiting on you — when OG has a fix ready or a system upgrade to offer, it lands here.</p>' +
'</div>' +
'<div id="ogFeedView" class="og-seat-view" hidden>' +
'<div id="ogFeedTabs" class="og-feed-tabs">' +
'<button class="og-feed-tab is-on" id="ogFeedTabAll" type="button">All</button>' +
'<button class="og-feed-tab" id="ogFeedTabVideo" type="button">Videos</button>' +
'<button class="og-feed-tab" id="ogFeedTabSong" type="button">Songs</button>' +
'<button class="og-feed-tab" id="ogFeedTabImage" type="button">Images</button>' +
'<button class="og-feed-tab" id="ogFeedTabFile" type="button">Files</button>' +
'<button class="og-feed-tab" id="ogFeedTabProject" type="button">Projects</button>' +
'</div>' +
'<p id="ogFeedStorage" class="og-seat-summary" hidden></p>' +
'<div id="ogFeedList"></div>' +
'<p id="ogFeedEmpty" class="og-appr-empty" hidden>Nothing here yet — what OG makes for you, and what he notices, lands here.</p>' +
'</div>' +
'</section>' +
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
  chat: $('ogSeatChat'), goals: $('ogSeatGoals'),
  ideas: $('ogSeatIdeas'), feed: $('ogSeatFeed')
};
var SEAT_TITLES = { goals: '🎯 Goals — what OG is working on', ideas: '💡 Ideas — OG\'s proposals', feed: '📰 Feed' };
var activeSeat = 'chat';
var ideasDot = $('ogSeatIdeasDot'), feedDot = $('ogSeatFeedDot');

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

/* Seat notices: one line inside the seat screen. Identical
   (seat, text) posts within 5 s post ONCE — the owner's video
   showed "Nothing waiting on you." twice on repeated taps. The log
   is kept on window for the DOM tests + debugging. */
var lastNotice = { key: '', at: 0 };
window.ogSeatNoticeLog = [];
function seatNotice(seat, text) {
  var key = seat + '|' + text, now = Date.now();
  if (lastNotice.key === key && now - lastNotice.at < 5000) return false;
  lastNotice = { key: key, at: now };
  window.ogSeatNoticeLog.push({ seat: seat, text: text, at: now });
  if (window.ogSeatNoticeLog.length > 20) window.ogSeatNoticeLog.shift();
  var n = $('ogSeatNotice');
  if (n) {
    n.hidden = false;
    n.textContent = text;
    n.classList.remove('og-seat-notice-pulse');
    void n.offsetWidth;
    n.classList.add('og-seat-notice-pulse');
  }
  return true;
}
function hideNotice() { var n = $('ogSeatNotice'); if (n) n.hidden = true; }

function currentSeat() {
  if (!isHidden($('ogApprovalsPanel'))) return 'ideas';
  return activeSeat;
}
function paintSeats() {
  var cur = currentSeat();
  Object.keys(seats).forEach(function (k) {
    if (seats[k]) seats[k].classList.toggle('is-on', k === cur);
  });
}

function gotoSeat(name) {
  activeSeat = name;
  closeAllViews();
  hideNotice();
  var scr = $('ogSeatScreen');
  if (name === 'chat') {
    if (scr) scr.hidden = true;
  } else {
    if (scr) scr.hidden = false;
    var t = $('ogSeatScreenTitle');
    if (t) t.textContent = SEAT_TITLES[name] || name;
    var gv = $('ogGoalsView'), iv = $('ogIdeasView'), fv = $('ogFeedView');
    if (gv) gv.hidden = name !== 'goals';
    if (iv) iv.hidden = name !== 'ideas';
    if (fv) fv.hidden = name !== 'feed';
    if (name === 'goals') refreshGoals();
    if (name === 'ideas') { renderIdeas(); refreshFixes(); refreshOffers(); }
    if (name === 'feed') refreshFeed();
  }
  paintSeats();
}

if (seats.chat) seats.chat.addEventListener('click', function () { gotoSeat('chat'); });
if (seats.goals) seats.goals.addEventListener('click', function () { gotoSeat('goals'); });
if (seats.ideas) seats.ideas.addEventListener('click', function () { gotoSeat('ideas'); });
if (seats.feed) seats.feed.addEventListener('click', function () { gotoSeat('feed'); });
var scrClose = $('ogSeatScreenClose');
if (scrClose) scrClose.addEventListener('click', function () { gotoSeat('chat'); });
var apprClose = $('ogApprCloseBtn');
if (apprClose) apprClose.addEventListener('click', function () { closeApprovals(); paintSeats(); });
var apprScrim = $('ogApprovalsScrim');
if (apprScrim) apprScrim.addEventListener('click', function () { closeApprovals(); paintSeats(); });

/* The composer's + belongs to Chat: tapping it while a seat screen
   is up returns to Chat first (the page's own handler then opens the
   Add sheet exactly as before). Capture phase so the seat closes
   before the sheet paints. */
var plusBtnEl = $('plusBtn');
if (plusBtnEl) plusBtnEl.addEventListener('click', function () {
  if (activeSeat !== 'chat') gotoSeat('chat');
}, true);

/* --- 3b. Notification tap-through router (Round 59 Item A) -------------
   Consumes the target descriptor stamped by og_notify.record():
   {"view": "approvals"|"browser"|"library"|"watch"|"system", ...}.
   Missing/malformed target = no route (the tap just marks read). */
var flashLibId = null;
function routeTarget(t) {
  if (!t || typeof t !== 'object' || !t.view) return false;
  if (t.view === 'approvals') { openApprovals(); return true; }
  if (t.view === 'browser') {
    gotoSeat('chat');
    var p = $('browserPanel');
    if (p && !p.hidden) { if (p.scrollIntoView) p.scrollIntoView(); }
    else seatNotice('chat', 'That sign-in\'s browser session is over — ask OG to open the site again.');
    return true;
  }
  if (t.view === 'library') {
    flashLibId = t.id || null;
    gotoSeat('feed');
    setFeedTab(t.kind || 'all');
    return true;
  }
  if (t.view === 'watch') { gotoSeat('goals'); return true; }
  if (t.view === 'system') { gotoSeat('ideas'); return true; }
  return false;
}
window.ogRouteTarget = routeTarget;

/* --- 4. Approvals panel ------------------------------------------------- */
var pending = null, apprBusy = false, recentsCount = 0, outcomeUntil = 0;
var feedUnread = 0;

/* The Ideas dot lights for a parked approval OR an open fix proposal. */
var openFixCount = 0, lastProposals = [];
function updateDot() {
  if (ideasDot) ideasDot.hidden = !(pending || openFixCount > 0);
}
function paintFeedDot() {
  if (feedDot) feedDot.hidden = !(feedUnread > 0);
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
function relTime(ts) {
  var n = Number(ts || 0);
  if (!n) return '';
  var s = Math.max(0, (Date.now() / 1000) - (n > 1e12 ? n / 1000 : n));
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return fmtTime(ts);
}
function getJSON(url) {
  return fetch(url)
    .then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; });
}

function renderPending() {
  var card = $('ogApprCard'), desc = $('ogApprDesc'), text = $('ogApprText'),
      exp = $('ogApprExp'), always = $('ogApprAlwaysBtn'), out = $('ogApprOutcome'),
      empty = $('ogApprEmpty');
  updateDot();
  renderIdeasAppr();
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
  fetch('/notifications')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (d && typeof d.unread === 'number') { feedUnread = d.unread; paintFeedDot(); }
      if (!list) return;
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
   route with the proposal id + the decision. Round 59: the same list
   also renders in the Ideas seat (same act route, same buttons). */
function fixRow(p, viaIdeas) {
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
    yes.addEventListener('click', function () { decideFix(p.id, 'approve', yes, no, viaIdeas); });
    no.addEventListener('click', function () { decideFix(p.id, 'decline', yes, no, viaIdeas); });
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
  return row;
}

function renderFixLists() {
  var list = $('ogApprFixList'), sec = $('ogApprFixSec');
  if (list) {
    list.innerHTML = '';
    if (sec) sec.hidden = lastProposals.length === 0;
    lastProposals.forEach(function (p) { if (p) list.appendChild(fixRow(p, false)); });
  }
  var ilist = $('ogIdeasFixList'), isec = $('ogIdeasFixSec');
  if (ilist) {
    ilist.innerHTML = '';
    if (isec) isec.hidden = lastProposals.length === 0;
    lastProposals.forEach(function (p) { if (p) ilist.appendChild(fixRow(p, true)); });
  }
  renderIdeasEmpty();
}

function refreshFixes() {
  fetch('/approvals/proposals')
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      lastProposals = (d && d.proposals) || [];
      openFixCount = (d && typeof d.open_count === 'number')
        ? d.open_count
        : lastProposals.filter(function (p) { return p && p.status === 'open'; }).length;
      renderFixLists();
      updateDot();
      renderPending();
    })
    .catch(function () { });
}

function decideFix(id, decision, yesBtn, noBtn, viaIdeas) {
  if (yesBtn) yesBtn.disabled = true;
  if (noBtn) noBtn.disabled = true;
  fetch('/approvals/proposals/act', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: id, decision: decision })
  })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      var msg;
      if (res && res.title) msg = (res.ok ? '✅ ' : '⚠️ ') + res.title + (res.body ? '\n' + res.body : '');
      else msg = '⚠️ That didn\'t go through — ' + ((res && res.error) || 'try again in a moment.');
      if (viaIdeas) seatNotice('ideas', msg);
      else showApprOutcome(msg);
    })
    .catch(function () {
      var msg = '⚠️ That didn\'t go through — connection hiccup. The fix is still parked if it reappears above.';
      if (viaIdeas) seatNotice('ideas', msg);
      else showApprOutcome(msg);
    })
    .then(function () { refreshFixes(); refreshPending(); });
}

/* --- 6. Goals seat: everything OG is working on ------------------------
   Reads the routes that already exist (GET /watch/status for the
   Facebook-style watches) plus Round 59's additive GET
   /monitor/status for price watches — no new stores. */
function seatRow(icon, title, sub) {
  var row = document.createElement('div');
  row.className = 'og-appr-item';
  var ico = document.createElement('span');
  ico.className = 'og-appr-ico'; ico.textContent = icon;
  var main = document.createElement('div');
  main.className = 'og-appr-main';
  var t = document.createElement('div');
  t.className = 'og-appr-item-title'; t.textContent = title || '';
  var b = document.createElement('div');
  b.className = 'og-appr-item-body'; b.textContent = sub || '';
  main.appendChild(t); main.appendChild(b);
  row.appendChild(ico); row.appendChild(main);
  return row;
}
var WATCH_STATUS = {
  active: 'watching', waiting_login: 'waiting on your sign-in',
  paused_checkpoint: 'paused at a security check'
};
function refreshGoals() {
  var sum = $('ogGoalsSummary'), wl = $('ogGoalsWatchList'),
      pl = $('ogGoalsPriceList'), psec = $('ogGoalsPriceSec'),
      empty = $('ogGoalsEmpty');
  if (!wl) return;
  Promise.all([getJSON('/watch/status'), getJSON('/monitor/status')])
    .then(function (res) {
      var wst = res[0] || {}, mst = res[1] || {};
      var watches = wst.watches || [], prices = mst.watches || [];
      wl.innerHTML = '';
      watches.forEach(function (w) {
        var bits = [];
        if (w.place) bits.push(w.place);
        if (w.max_price) bits.push('up to $' + w.max_price);
        bits.push(WATCH_STATUS[w.status] || w.status || 'watching');
        if (w.last_check) bits.push('last checked ' + relTime(w.last_check));
        wl.appendChild(seatRow('👀', w.item || 'Watch', bits.join(' · ')));
      });
      if (psec) psec.hidden = prices.length === 0;
      if (pl) {
        pl.innerHTML = '';
        prices.forEach(function (w) {
          var bits = [];
          if (w.terms) bits.push(w.terms);
          if (w.base != null) bits.push('from ' + w.base);
          bits.push(w.status === 'fired'
            ? 'fired' + (w.fired_price != null ? ' at ' + w.fired_price : '')
            : 'watching');
          pl.appendChild(seatRow('💲', (w.symbol || 'Symbol') + (w.kind ? ' (' + w.kind + ')' : ''), bits.join(' · ')));
        });
      }
      var pendingN = (wst.pending_alerts || 0) + (mst.pending_alerts || 0);
      var running = watches.length + prices.length;
      if (sum) sum.textContent = running === 0
        ? 'Nothing running right now.'
        : running + (running === 1 ? ' thing' : ' things') + ' running' +
          (pendingN > 0 ? ' · ' + pendingN + ' alert' + (pendingN === 1 ? '' : 's') + ' waiting for you in chat' : '');
      if (empty) empty.hidden = running !== 0;
    })
    .catch(function () { if (sum) sum.textContent = 'Couldn\'t load right now — try again in a moment.'; });
}

/* --- 7. Ideas seat: OG's proposals --------------------------------------
   Open fix proposals (same data as the panel's Fixes section) + the
   latest self-check's upgrade offers (operator only, GET
   /system/offers). Approving an upgrade stays the Round 50 chat
   sentence — the exact sentence is shown with each offer. */
var offers = [], offersSummary = null;
function renderIdeasAppr() {
  var blk = $('ogIdeasAppr'), desc = $('ogIdeasApprDesc');
  if (!blk) return;
  blk.hidden = !pending;
  if (pending && desc) {
    var kindLabel = pending.kind === 'post' ? 'post this' : 'do this';
    desc.textContent = 'OG is ready to ' + kindLabel + ': ' + (pending.desc || 'a browser action') + (pending.site ? ' on ' + pending.site : '') + '. Nothing happens unless you approve.';
  }
}
var ideasApprBtn = $('ogIdeasApprBtn');
if (ideasApprBtn) ideasApprBtn.addEventListener('click', function () { openApprovals(); });

function renderIdeasEmpty() {
  var empty = $('ogIdeasEmpty');
  if (!empty) return;
  var nothing = !pending && openFixCount === 0 && offers.length === 0;
  empty.hidden = !nothing;
  if (nothing && activeSeat === 'ideas' && !isHidden($('ogSeatScreen'))) {
    seatNotice('ideas', 'Nothing waiting on you.');
  }
}
function renderIdeas() {
  renderIdeasAppr();
  renderFixLists();
  var list = $('ogIdeasOfferList'), sec = $('ogIdeasOfferSec');
  if (list) {
    list.innerHTML = '';
    if (sec) sec.hidden = offers.length === 0 && !offersSummary;
    offers.forEach(function (o) {
      if (!o) return;
      var sub = (o.what || '') +
        '\nTo approve it, say in chat: "approve the upgrade for ' + (o.name || '') + '"' +
        ((o.owner_steps && o.owner_steps.length) ? '\nIt needs steps in the dashboard — OG will walk you through them; approving never charges anything by itself.' : '');
      list.appendChild(seatRow('⬆️', o.name || 'Upgrade', sub));
    });
    if (offersSummary && offers.length === 0) {
      list.appendChild(seatRow('🩺', 'Latest system check', offersSummary));
    }
  }
  renderIdeasEmpty();
}
function refreshOffers() {
  getJSON('/system/offers').then(function (d) {
    offers = (d && d.offers) || [];
    offersSummary = (d && d.operator) ? (d.summary || null) : null;
    renderIdeas();
  });
}

/* --- 8. Feed seat: OG's stream ------------------------------------------
   Library items (GET /library — its tabs ride at the top) merged
   with notifications (GET /notifications), newest first. Library
   rows keep their action (Play/Open/Download); notification rows
   route per Item A. Kind tabs filter the library portion;
   notifications ride the All tab. */
var FEED_KINDS = ['all', 'video', 'song', 'image', 'file', 'project'];
var feedTab = 'all', feedRows = [], feedLibIds = {};
var LIB_ICONS = { video: '🎬', song: '🎵', image: '🖼', file: '📄', project: '🧩' };
var KIND_LABELS = { video: 'Video', song: 'Song', image: 'Image', file: 'File', project: 'Project' };
var NOTIF_ICONS = {
  watch_match: '👀', price_alert: '💲', video_done: '🎬', song_done: '🎵',
  storage_warning: '🗄', notice: '🔔', approval_needed: '✋',
  signin_needed: '🔑', system_check: '🩺', fix_needed: '🔧'
};
function setFeedTab(kind) {
  feedTab = FEED_KINDS.indexOf(kind) !== -1 ? kind : 'all';
  FEED_KINDS.forEach(function (k) {
    var b = $('ogFeedTab' + k.charAt(0).toUpperCase() + k.slice(1));
    if (b) b.classList.toggle('is-on', k === feedTab);
  });
  renderFeed();
}
FEED_KINDS.forEach(function (k) {
  var b = $('ogFeedTab' + k.charAt(0).toUpperCase() + k.slice(1));
  if (b) b.addEventListener('click', function () { setFeedTab(k); });
});

function markNotifRead(it, row) {
  if (it.read) return;
  it.read = true;
  if (row) row.classList.remove('unread');
  feedUnread = Math.max(0, feedUnread - 1);
  paintFeedDot();
  fetch('/notifications/read', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: it.id })
  }).catch(function () { });
}

function renderFeed() {
  var list = $('ogFeedList'), empty = $('ogFeedEmpty');
  if (!list) return false;
  list.innerHTML = '';
  var shown = 0, flashed = false;
  feedRows.forEach(function (r) {
    if (feedTab !== 'all' && r.src !== 'lib') return;
    if (feedTab !== 'all' && r.kind !== feedTab) return;
    shown++;
    var row = document.createElement('div');
    row.className = 'og-appr-item og-feed-row' + (r.src === 'ntf' && !r.read ? ' unread' : '');
    var ico = document.createElement('span');
    ico.className = 'og-appr-ico';
    ico.textContent = r.src === 'lib' ? (LIB_ICONS[r.kind] || '📄') : (NOTIF_ICONS[r.kind] || '🔔');
    var main = document.createElement('div');
    main.className = 'og-appr-main';
    var t = document.createElement('div');
    t.className = 'og-appr-item-title'; t.textContent = r.title || '';
    var b = document.createElement('div');
    b.className = 'og-appr-item-body'; b.textContent = r.sub || '';
    var w = document.createElement('div');
    w.className = 'og-appr-item-time'; w.textContent = relTime(r.ts);
    main.appendChild(t); main.appendChild(b); main.appendChild(w);
    row.appendChild(ico); row.appendChild(main);
    if (r.src === 'lib' && r.action_url && r.status !== 'working') {
      var a = document.createElement('a');
      a.className = 'og-feed-action';
      a.href = r.action_url;
      a.textContent = r.action || 'Open';
      row.appendChild(a);
    }
    if (r.src === 'ntf') {
      row.classList.add('og-feed-tappable');
      row.addEventListener('click', function () {
        markNotifRead(r.raw, row);
        if (r.target) routeTarget(r.target);
      });
    }
    if (flashLibId && r.src === 'lib' && r.id === flashLibId) {
      row.classList.add('og-feed-flash');
      flashed = true;
      if (row.scrollIntoView) row.scrollIntoView();
    }
    list.appendChild(row);
  });
  if (empty) empty.hidden = shown !== 0;
  return flashed;
}

function refreshFeed() {
  Promise.all([getJSON('/library'), getJSON('/notifications')])
    .then(function (res) {
      var lib = res[0] || {}, ntf = res[1] || {};
      if (typeof ntf.unread === 'number') { feedUnread = ntf.unread; paintFeedDot(); }
      var rows = [];
      feedLibIds = {};
      (lib.items || []).forEach(function (it) {
        if (!it) return;
        feedLibIds[it.id] = true;
        rows.push({
          src: 'lib', id: it.id, kind: it.kind, title: it.title,
          sub: (KIND_LABELS[it.kind] || 'Item') + (it.status === 'working' ? ' · making…' : ''),
          ts: it.created_at || 0, action: it.action,
          action_url: it.action_url, status: it.status
        });
      });
      (ntf.items || []).forEach(function (it) {
        if (!it) return;
        rows.push({
          src: 'ntf', id: it.id, kind: it.kind, title: it.title,
          sub: it.body || '', ts: it.ts || 0, read: !!it.read,
          target: it.target || null, raw: it
        });
      });
      rows.sort(function (a, b2) { return (b2.ts || 0) - (a.ts || 0); });
      feedRows = rows;
      var st = $('ogFeedStorage');
      if (st) {
        st.hidden = !lib.storage_line;
        if (lib.storage_line) st.textContent = lib.storage_line;
      }
      /* The routed-item flash is consumed HERE, on the fresh
         render — a tab-switch render may paint it with stale rows
         first, but the refresh render is the one that must carry
         it (and the one that knows the item is truly gone). */
      var flashed = renderFeed();
      if (flashLibId) {
        if (!flashed) seatNotice('feed', 'That one has rolled off your feed — it may have been cleaned up or expired.');
        flashLibId = null;
      }
    })
    .catch(function () { });
}

/* --- 9. Boot + polls ------------------------------------------------------ */
refreshPending();
refreshFixes();
refreshRecents();
setInterval(refreshPending, 4000);
setInterval(refreshFixes, 4000);
setInterval(refreshRecents, 15000);
setInterval(function () {
  if (isHidden($('ogSeatScreen'))) return;
  if (activeSeat === 'goals') refreshGoals();
  if (activeSeat === 'ideas') { refreshFixes(); refreshOffers(); }
  if (activeSeat === 'feed') refreshFeed();
}, 20000);
setInterval(paintSeats, 900);
paintSeats();
})();

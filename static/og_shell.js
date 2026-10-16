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
'<button class="og-seat" id="ogSeatGoals" type="button"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg><span class="og-seat-label">Progress</span></button>' +
'<button class="og-seat" id="ogSeatIdeas" type="button"><svg viewBox="0 0 24 24"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.8.7 1 1.5 1 2.5h6c0-1 .2-1.8 1-2.5A6 6 0 0 0 12 3z"/></svg><span class="og-seat-label">Ideas</span><span id="ogSeatIdeasDot" class="og-seat-dot" hidden></span></button>' +
'<button class="og-seat" id="ogSeatFeed" type="button"><svg viewBox="0 0 24 24"><path d="M4 5h16"/><path d="M4 12h16"/><path d="M4 19h10"/></svg><span class="og-seat-label">Feed</span><span id="ogSeatFeedDot" class="og-seat-dot" hidden></span></button>' +
'</nav>' +
'</div>' +
'<section id="ogSeatScreen" hidden aria-label="OG section">' +
'<div class="og-seat-screen-head"><span id="ogSeatScreenTitle" class="og-seat-screen-title">Feed</span><button id="ogSeatScreenClose" class="og-circle-btn" type="button" aria-label="Back to chat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button></div>' +
'<p id="ogSeatNotice" class="og-seat-notice" hidden></p>' +
'<div id="ogGoalsView" class="og-seat-view" hidden>' +
'<p id="ogGoalsSummary" class="og-seat-summary">Loading…</p>' +
'<h3 class="og-appr-sec">Being worked on</h3>' +
'<p id="ogProgressSummary" class="og-seat-summary">Loading…</p>' +
'<div id="ogProgressList"></div>' +
'<p id="ogProgressEmpty" class="og-appr-empty" hidden>Nothing is being worked on right now — when OG is making something, browsing, or checking a watch for you, it shows up here while it runs.</p>' +
'<h3 class="og-appr-sec">Watching for you</h3>' +
'<div id="ogGoalsWatchList"></div>' +
'<h3 class="og-appr-sec" id="ogGoalsPriceSec" hidden>Price watches</h3>' +
'<div id="ogGoalsPriceList"></div>' +
'<p id="ogGoalsEmpty" class="og-appr-empty" hidden>OG isn\'t watching anything yet — ask in chat, like "watch for a red kayak under $200".</p>' +
'<button id="ogManagerBtn" class="og-manager-btn" type="button">Manager Dashboard</button>' +
'</div>' +
'<div id="ogIdeasView" class="og-seat-view" hidden>' +
'<div id="ogIdeasAppr" class="og-appr-card" hidden>' +
'<div class="og-approve-head">⚠️ <span>OG is waiting on you</span></div>' +
'<p id="ogIdeasApprDesc" class="og-approve-desc"></p>' +
'<div class="og-approve-btns"><button id="ogIdeasApprBtn" type="button">Open approvals</button></div>' +
'</div>' +
'<h3 class="og-appr-sec" id="ogIdeasGenSec" hidden>Ideas for you</h3>' +
'<div id="ogIdeasGenList"></div>' +
'<h3 class="og-appr-sec" id="ogIdeasFixSec" hidden>Fixes waiting</h3>' +
'<div id="ogIdeasFixList"></div>' +

'<p id="ogIdeasEmpty" class="og-appr-empty" hidden>Nothing waiting on you — when OG has a fix ready or a system upgrade to offer, it lands here.</p>' +
'</div>' +
'<div id="ogFeedView" class="og-seat-view" hidden>' +
'<p id="ogFeedStorage" class="og-seat-summary" hidden></p>' +
'<p id="ogFeedNote" class="og-seat-summary" hidden></p>' +
'<div id="ogFeedClearRow" class="og-feed-clear" hidden><button id="ogFeedClearRead" type="button">Clear read</button><button id="ogFeedClearAll" type="button">Clear all</button></div>' +
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
'</section>' +
'<div id="ogReconnectChip" hidden><span class="og-reconnect-title">Reconnecting…</span><span class="og-reconnect-sub">The server isn\'t answering right now — that\'s not a sign-out. Retrying on its own.</span></div>';
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
  new MutationObserver(function () { syncGate(); onGatePaint(); })
    .observe(gate, { attributes: true, attributeFilter: ['hidden'] });
}
syncGate();

/* --- 2b. Session truth (Round 79): a blink is not a sign-out ----------
   The page's own session check (og-auth-js checkMe) paints
   the wall on ANY failure of its /auth/me fetch: fetch
   resolves on a 502, r.json() throws on the HTML error
   page, and the catch shows the wall — though the session
   (a server-side record; a 502 kills nothing) is alive.
   Brent's 2026-10-11 recording: server blinked, the page
   reloaded, the wall came up with his email + a saved
   password filled in, and his live Remember-me session
   was treated as dead.
   This watcher arbitrates the wall with a status-aware
   probe of the same endpoint. /auth/me answers 200 with
   {"signed_in": <bool>} — a 200 carrying that boolean is
   the ONLY definitive answer. signed_in -> the page's
   own ogAuthCheck() re-runs and enters the app. false ->
   the wall is correct; stand down. Anything else (5xx,
   non-JSON, a rejected fetch) is a blink: while the wall
   is up it is suppressed behind the neutral reconnecting
   chip (a body class only — the gate element, its hidden
   attribute, and the og-shell-off mirror are never
   touched) and the probe retries with backoff until a
   definitive answer lands. A ?reset= link owns the wall
   (password reset): the watcher never engages then.
   enterApp is the page's ONLY gate-hider and it runs
   only on a definitive signed_in, so a hidden gate is
   itself proof of a confirmed session this load. */
var sessionState = (gate && !gate.hidden) ? 'unknown' : 'in';
var sessionProbeBusy = false, reconnectTimer = null, reconnectTries = 0;
var hasResetToken = false;
try { hasResetToken = !!new URLSearchParams(location.search).get('reset'); } catch (e) { }
function classifyMe(status, d) {
  if (status === 200 && d && typeof d.signed_in === 'boolean') {
    return d.signed_in ? 'in' : 'out';
  }
  return 'transient';
}
window.ogClassifyMe = classifyMe;
function stopReconnect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  reconnectTries = 0;
  document.body.classList.remove('og-auth-reconnecting');
  var chip = $('ogReconnectChip');
  if (chip) chip.hidden = true;
}
function startReconnect() {
  document.body.classList.add('og-auth-reconnecting');
  var chip = $('ogReconnectChip');
  if (chip) chip.hidden = false;
  if (reconnectTimer) return;
  var delay = Math.min(2500 + reconnectTries * 750, 10000);
  reconnectTries++;
  reconnectTimer = setTimeout(function () {
    reconnectTimer = null;
    probeSession();
  }, delay);
}
function onSessionTruth(truth) {
  if (truth === 'in') {
    sessionState = 'in';
    stopReconnect();
    if (gate && !gate.hidden && typeof window.ogAuthCheck === 'function') {
      window.ogAuthCheck();
    }
  } else if (truth === 'out') {
    sessionState = 'out';
    stopReconnect();
  } else if (gate && !gate.hidden && !hasResetToken) {
    /* transient: only the wall's paint is in question */
    startReconnect();
  }
}
function probeSession() {
  if (sessionProbeBusy || hasResetToken) return;
  sessionProbeBusy = true;
  fetch('/auth/me').then(function (r) {
    return r.json().then(
      function (d) { return { status: r.status, d: d }; },
      function () { return { status: r.status, d: null }; });
  }).then(function (res) {
    sessionProbeBusy = false;
    onSessionTruth(classifyMe(res.status, res.d));
  }).catch(function () {
    sessionProbeBusy = false;
    onSessionTruth('transient');
  });
}
function onGatePaint() {
  if (!gate) return;
  if (gate.hidden) {
    /* enterApp is the only gate-hider: a session was
       just confirmed — and any reconnect is over. */
    sessionState = 'in';
    stopReconnect();
    return;
  }
  /* The wall just appeared (or re-appeared). Arbitrate
     it only when a confirmed session stands behind this
     page load; a wall after a definitive sign-out this
     load is the page's own truth and stays. */
  if (sessionState === 'in' && !hasResetToken) probeSession();
}
if (gate && !gate.hidden && !hasResetToken) probeSession();

/* --- 3. Seats ---------------------------------------------------------- */
var seats = {
  chat: $('ogSeatChat'), progress: $('ogSeatGoals'),
  ideas: $('ogSeatIdeas'), feed: $('ogSeatFeed')
};
var SEAT_TITLES = { progress: '🎯 Progress — what OG is working on', ideas: '💡 Ideas — OG\'s proposals', feed: '📰 Feed' };
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
  /* Round 70 rename (Brent, 2026-10-11): the Goals seat
     IS Progress now — one seat, merged view. The legacy
     'goals' name still resolves here so stored
     notification targets never dead-end. */
  if (name === 'goals') name = 'progress';
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
    if (gv) gv.hidden = name !== 'progress';
    if (iv) iv.hidden = name !== 'ideas';
    if (fv) fv.hidden = name !== 'feed';
    if (name === 'progress') { refreshGoals(); refreshProgress(); }
    if (name === 'ideas') { renderIdeas(); refreshFixes(); refreshGenIdeas(); }
    if (name === 'feed') refreshFeed();
  }
  paintSeats();
}

if (seats.chat) seats.chat.addEventListener('click', function () { gotoSeat('chat'); });
if (seats.progress) seats.progress.addEventListener('click', function () { gotoSeat('progress'); });
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
  if (t.view === 'watch') { gotoSeat('progress'); return true; }
  if (t.view === 'system') { gotoSeat('ideas'); return true; }
  return false;
}
window.ogRouteTarget = routeTarget;

/* --- 4. Approvals panel ------------------------------------------------- */
var pending = null, apprBusy = false, recentsCount = 0, outcomeUntil = 0;
var feedUnread = 0;

/* The Ideas dot lights for a parked approval OR an open fix proposal. */
var openFixCount = 0, lastProposals = [];
/* Round 83: proposal ids with a decide POST in flight FROM
   THIS TAB. fixRow renders the working state for these ids
   (and for server-reported 'working' proposals), so the
   4-second list refresh can no longer wipe a tap back to a
   pristine card — the dead tap Brent reported. */
var fixWorking = {};
function updateDot() {
  if (ideasDot) ideasDot.hidden = !(pending || openFixCount > 0);
}
function paintFeedDot() {
  /* Round 95: the bell is deleted (Brent: "it's in the
     idea at the bottom") — the Feed seat carries the
     unread COUNT now, the same number from the same
     payloads, on the seat itself. */
  if (feedDot) {
    feedDot.textContent = feedUnread > 9 ? '9+' : String(feedUnread);
    feedDot.hidden = !(feedUnread > 0);
  }
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

/* --- 4b. Approval lock + pill (Round 70 addition) ------------------------
   Brent (2026-10-11): "make the approval card pop up in
   chat and you can't send anything until you answer the
   card; you will see in the bubble underneath 'OG needs
   approval' until you click one."
   The card itself already lives IN the chat stream — the
   page's own #approveCard renders the SAME pending_action
   this panel mirrors, with working Approve / Decline /
   Always-allow buttons (one live card; nothing here
   duplicates it). What this section adds is the lock and
   the pill:
   - While `pending` (the formal pending store's one
     parked approval — browser commits, ordering-flow
     commits, GitHub PR approvals; all park through
     og_browser._set_pending and all render on that card
     with an answer path) is unanswered, the composer is
     locked: input + send + talk disabled, placeholder
     swapped, dock marked. Answering on the card OR in
     this panel clears the pending on the next poll and
     unlocks immediately.
   - DEADLOCK GUARD: the lock keys ONLY on pending_action.
     Conversational questions are not pendings and never
     lock; the video/song spend cards are a separate
     system with their own in-chat cards and never lock.
   - The status pills (#ogTaskPillTop + #ogTaskPillProfile,
     one page updater writing both) read exactly
     "OG needs approval" while locked — asserted here on
     the shell's own polls, restored from the last browser
     status on unlock (the page's updater self-heals on
     its next state change regardless).
   - The page's sendMessage is wrapped (lazily, at first
     lock) so a programmatic send — voice auto-send, an
     Ideas Ask tap — cannot slip past the lock either.
   Round 70 (pill surface): the pill is the live-action +
   notification surface (Brent, 2026-10-11) — needs-user
   states surface there too, OUTRANKING the activity
   states the page's updater paints. The page's updater
   remains the ONE activity updater (Thinking… / Making
   your video / Browsing <host> / Online & Ready); this
   layer only ever writes a needs-user text, in this
   priority: approval > sign-in needed > browser needed,
   and restores the activity text when the last needs-
   user state clears. Both pill instances are always
   written together. Tapping the pill routes to the
   thing it names (see pillTap).
   Round 79: the writes themselves now go through the
   ONE-writer coordinator (§4c) — the page's updater
   supplies the activity text through the pill channels
   and only the coordinator paints, so a needs text and
   an activity text can never paint over each other. */
var composerLocked = false, lastBrowserStatus = null, sendWrapped = false;
var signinNeeded = false, browserNeeded = false, pillNeedsActive = false;
/* Round 321 (V2): the pill names the SITE that needs the
   sign-in — a bare account-flavored claim while the owner
   IS signed in was the lie Brent's video caught. The site
   strings ride alongside the booleans (null = unknown). */
var signinSite = null, watchSite = null, browserSite = null;
function pillSiteName(host) {
  var h = String(host || '').toLowerCase()
    .replace(/^(www\.|m\.|mobile\.)/, '');
  var first = h.split('.')[0];
  if (!first) return null;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

/* --- 4c. The pill's ONE writer (Round 79) ------------------------------
   Two painters used to write the status pills
   (#ogTaskPillTop + #ogTaskPillProfile): the page's
   activity updater (Thinking… / Making your video /
   Browsing <host> / Online & Ready), deduped by a
   closure-private lastText the shell cannot see, and
   this shell's needs layer (assertApprovalPill /
   restorePill below). Neither knew the other's writes:
   the page could repaint OVER a live needs state until
   the shell's next poll re-asserted it, and its dedupe
   guard could then skip the repaint that should have
   replaced the shell's restored text — on Brent's phone
   the two texts smeared across each other ("OG needs
   you to sign in" ghosted over "Online & Ready").
   Now the pills have ONE writer: this coordinator. Each
   pill element gets a textContent channel — the native
   setter is captured first, then an own-property trap
   routes EVERY outside write (the page painter's above
   all) into the coordinator as the latest ACTIVITY text.
   Only the coordinator ever calls the native setter,
   painting needs ?? activity, change-only, both
   instances in the same write: a new state REPLACES the
   text, nothing ever paints over. If a channel cannot
   be installed (non-configurable property), the
   coordinator falls back to the legacy direct writes
   for the elements it could not channel. */
var pillNeeds = null, pillActivityText = null, pillShown = null,
    pillPageSeen = false, pillChannels = [], pillPlain = [];
function pillRawChannel(el) {
  var nativeSet = null, nativeGet = null, o = el, guard = 0;
  while (o && guard++ < 12) {
    var d = null;
    try { d = Object.getOwnPropertyDescriptor(o, 'textContent'); } catch (e) { }
    if (d) {
      if (typeof d.set === 'function') { nativeSet = d.set; nativeGet = d.get || null; }
      break;
    }
    o = Object.getPrototypeOf(o);
  }
  var seed = '';
  try { seed = nativeGet ? String(nativeGet.call(el) || '') : String(el.textContent || ''); }
  catch (e) { }
  var store = { text: seed };
  var ch = {
    seed: seed,
    rawSet: function (t) {
      store.text = t;
      if (nativeSet) { try { nativeSet.call(el, t); } catch (e) { } }
    }
  };
  try {
    Object.defineProperty(el, 'textContent', {
      configurable: true, enumerable: true,
      get: function () { return store.text; },
      set: function (v) { pillActivityWrite(v == null ? '' : String(v)); }
    });
    return ch;
  } catch (e) {
    return null;
  }
}
function pillPaint() {
  var t = pillNeeds != null ? pillNeeds : pillActivityText;
  if (t == null || t === pillShown) return;
  pillShown = t;
  pillChannels.forEach(function (ch) { ch.rawSet(t); });
  pillPlain.forEach(function (el) {
    /* unchanneled fallback: no trap lives here, so this
       direct write cannot re-enter the coordinator */
    try { el.textContent = t; } catch (e) { }
  });
}
function pillActivityWrite(t) {
  pillPageSeen = true;
  if (t === pillActivityText) { pillPaint(); return; }
  pillActivityText = t;
  pillPaint();
}
function pillSetNeeds(t) {
  pillNeeds = t || null;
  pillPaint();
}
['ogTaskPillTop', 'ogTaskPillProfile'].forEach(function (id) {
  var el = $(id);
  if (!el) return;
  var ch = pillRawChannel(el);
  if (ch) {
    pillChannels.push(ch);
    if (pillActivityText == null && ch.seed) pillActivityText = ch.seed;
  } else {
    pillPlain.push(el);
    if (pillActivityText == null) {
      try { if (el.textContent) pillActivityText = String(el.textContent); } catch (e) { }
    }
  }
});
if (pillActivityText == null) pillActivityText = 'Online & Ready';
pillShown = pillActivityText;

function wrapSend() {
  if (sendWrapped || typeof window.sendMessage !== 'function') return;
  var orig = window.sendMessage;
  window.sendMessage = function () {
    if (composerLocked) return;
    return orig.apply(this, arguments);
  };
  sendWrapped = true;
}
function needsPillText() {
  if (pending) return 'OG needs approval';
  if (signinSite) return signinSite + ' needs you to sign in';
  if (signinNeeded) return 'OG needs you to sign in';
  if (watchSite) return watchSite + ' needs you to sign in';
  if (browserSite) return browserSite + ' needs you in the browser';
  if (browserNeeded) return 'OG needs you in the browser';
  return null;
}
function assertApprovalPill() {
  var t = needsPillText();
  if (!t) return;
  pillNeedsActive = true;
  pillSetNeeds(t);
}
function updateNeedsPill() {
  if (needsPillText()) { assertApprovalPill(); return; }
  if (pillNeedsActive) {
    pillNeedsActive = false;
    restorePill();
  }
}
function pillTap() {
  if (pending) {
    gotoSeat('chat');
    var card = $('approveCard');
    if (card && card.scrollIntoView) {
      try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      catch (e) { card.scrollIntoView(); }
    }
    return;
  }
  if (signinNeeded || browserNeeded) {
    gotoSeat('chat');
    var mb = $('menuBrowserBtn');
    if (mb) { mb.click(); return; }
    routeTarget({ view: 'browser' });
    return;
  }
  /* activity / idle: no-op */
}
['ogTaskPillTop', 'ogTaskPillProfile'].forEach(function (id) {
  var p = $(id);
  if (!p) return;
  if (p.style) p.style.cursor = 'pointer';
  p.addEventListener('click', pillTap);
});
function restorePill() {
  /* Round 79: restoring = drop the needs text through the
     ONE writer (§4c). The activity text underneath is the
     page updater's latest paint — its writes are captured
     by the channels even while a needs state covers them,
     so nothing is stale. Only when the page has never
     painted do we seed the activity text from the last
     browser status, exactly as this function used to
     paint it directly. */
  if (!pillPageSeen) {
    var st = lastBrowserStatus, t = 'Online & Ready';
    if (st && st.enabled) {
      if (st.loading_host) t = 'Opening ' + st.loading_host + '…';
      else if (st.active) {
        var host = '';
        if (st.page_url) { try { host = new URL(st.page_url).hostname || ''; } catch (e) { } }
        t = host ? 'Browsing ' + host : 'Browsing';
      }
    }
    pillActivityText = t;
  }
  pillSetNeeds(null);
}
function applyComposerLock() {
  var locked = !!pending;
  if (locked === composerLocked) {
    if (locked) assertApprovalPill();
    return;
  }
  composerLocked = locked;
  var input = $('messageInput'), send = $('sendBtn'), voice = $('voiceBtn');
  if (locked) {
    wrapSend();
    if (input) {
      input.disabled = true;
      if (!input.getAttribute('data-og-ph')) {
        input.setAttribute('data-og-ph', input.placeholder || 'Ask OG…');
      }
      input.placeholder = 'Answer the card above to keep going';
    }
    if (send) send.disabled = true;
    if (voice) voice.disabled = true;
    document.body.classList.add('og-composer-locked');
    assertApprovalPill();
  } else {
    if (input) {
      input.disabled = false;
      input.placeholder = input.getAttribute('data-og-ph') || 'Ask OG…';
    }
    if (send) send.disabled = false;
    if (voice) voice.disabled = false;
    document.body.classList.remove('og-composer-locked');
    /* A lower-priority needs-user state may still be up
       (sign-in, browser) — it owns the pill now; only a
       fully clear board restores the activity text. */
    if (needsPillText()) assertApprovalPill();
    else { pillNeedsActive = false; restorePill(); }
  }
}

function renderPending() {
  var card = $('ogApprCard'), desc = $('ogApprDesc'), text = $('ogApprText'),
      exp = $('ogApprExp'), always = $('ogApprAlwaysBtn'), out = $('ogApprOutcome'),
      empty = $('ogApprEmpty');
  updateDot();
  applyComposerLock();
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
      lastBrowserStatus = st;
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
      /* Pill surface signals (Round 70): an unread
         sign-in-needed notification is a needs-user
         state; watch checkpoints are learned alongside
         (below) — both feed updateNeedsPill. */
      var allItems = (d && d.items) || [];
      signinNeeded = allItems.some(function (it) {
        return it && it.kind === 'signin_needed' && !it.read;
      });
      /* Round 321 (V2): name the site off the record's own
         target (the wall's host, written by og_browser's
         sign-in alert) — the pill never speaks for the
         OG account, only for the site asking. */
      signinSite = null;
      for (var si = 0; si < allItems.length; si++) {
        var sit = allItems[si];
        if (sit && sit.kind === 'signin_needed' && !sit.read
            && sit.target && sit.target.id) {
          signinSite = pillSiteName(sit.target.id);
          if (signinSite) break;
        }
      }
      updateNeedsPill();
      if (!list) return;
      var items = allItems.filter(function (it) { return it && it.kind === 'approval_needed'; }).slice(0, 5);
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
  /* The watch family's needs-user states (a watch stuck
     at a sign-in or an identity checkpoint) ride the same
     15 s cadence — they are standing states, not
     momentary ones. */
  getJSON('/watch/status').then(function (wst) {
    var ws = (wst && wst.watches) || [];
    var wWait = ws.some(function (w) {
      return w && w.status === 'waiting_login';
    });
    var wChk = ws.some(function (w) {
      return w && w.status === 'paused_checkpoint';
    });
    browserNeeded = wWait || wChk;
    /* Round 321 (V2): og_watch's watches are Facebook
       Marketplace watches, so the site a waiting watch
       names is Facebook. A watch waiting on a log-in is
       a SIGN-IN need and names the site as one; a watch
       paused at a checkpoint is a BROWSER need (the
       owner takes over to clear it) and names the site
       as one — the two claims never borrow each other's
       words. (A parked 'stalled' watch no longer counts
       as needing: the watchdog owns it until the owner
       resumes it.) */
    watchSite = wWait ? 'Facebook' : null;
    browserSite = wChk ? 'Facebook' : null;
    updateNeedsPill();
  });
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
  if (p.status === 'working' || fixWorking[p.id] ||
      (p.status === 'executed' && !p.outcome)) {
    /* Round 83: the row ITSELF says it's working — buttons
       gone — from the moment of the tap (fixWorking) and
       for as long as the server reports the run in flight,
       across every 4s re-render, seat switch and page
       reload. 'executed' with no outcome yet is still in
       flight: the stamp-before-run discipline lands
       'executed' BEFORE the run finishes, so until an
       outcome lands the honest label is Working, never
       "didn't take". */
    var ww = document.createElement('div');
    ww.className = 'og-appr-item-time';
    ww.textContent = '⏳ Working on it — OG is running this fix now';
    main.appendChild(ww);
  } else if (p.status === 'open') {
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
      : (p.status === 'handed_off' ? 'Steps handed over — in your hands now'
        : (p.status === 'superseded' ? 'Retired — ' + (p.status_note || 'OG could not ship this one; nothing was run')
          : 'Declined'));
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
  /* Round 83: mark the row working BEFORE the fetch and
     re-render at once — the card itself shows the tap
     landed, and every refresh from here on keeps showing
     it (fixRow) until the server reports the outcome. */
  fixWorking[id] = true;
  renderFixLists();
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
    .then(function () {
      /* The POST settled: this tab's marker retires and the
         SERVER status is the row's single source from here
         ('working' while a deferred code fix runs, then
         the durable outcome). */
      delete fixWorking[id];
      refreshFixes(); refreshPending();
    });
}

/* --- 6. Progress seat (renamed from Goals): everything OG is working on ------------------------
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
        if (w.matches && w.matches.length) {
          var best = w.matches[0];
          bits.push(w.matches.length + (w.matches.length === 1 ? ' match' : ' matches') +
            (best && best.price != null ? ', best $' + best.price : ''));
        }
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
   Open fix proposals (same data as the panel's Fixes section) +
   the Round 70 generated ideas (§7b). Round 70 rule pass
   (Brent, 2026-10-11): "OG should not be finding things to
   upgrade — he should be focusing on user tasks." The
   self-check's upgrade OFFERS are gone from this seat and
   from the server (the offers route is deleted): the
   self-check diagnoses, the owner still gets system
   alerts, but nothing here pitches an upgrade, ever. */
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
  var nothing = !pending && openFixCount === 0
    && genIdeas.length === 0;
  empty.hidden = !nothing;
  if (nothing && activeSeat === 'ideas' && !isHidden($('ogSeatScreen'))) {
    seatNotice('ideas', 'Nothing waiting on you.');
  }
}
function renderIdeas() {
  renderIdeasAppr();
  renderFixLists();
  renderIdeasEmpty();
}

/* --- 7b. Ideas for you (Round 70 addition) ------------------------------
   OG-generated ideas from the caller's own signals (GET
   /ideas — og_ideas.py derives them by rule from the
   caller's watches, notifications, books, and generator
   history; rules only, nothing executes; user-world
   signals ONLY — no system-needs ideas, per Brent's
   2026-10-11 rule).
   Tapping Ask drops the idea's ask into the composer and
   sends it through the page's own send path — the same
   seeded-open pattern as the browser rows. Dismiss parks
   the idea server-side for 30 days. */
var genIdeas = [];
function askInChat(text) {
  gotoSeat('chat');
  var input = $('messageInput');
  if (input) { input.value = text; input.focus(); }
  /* While an approval card is unanswered the composer is
     locked — the ask stays composed, unsent, and the card
     in the stream is the thing to answer first. */
  if (composerLocked) return;
  if (typeof window.sendMessage === 'function') window.sendMessage(true);
}
function renderGenIdeas() {
  var list = $('ogIdeasGenList'), sec = $('ogIdeasGenSec');
  if (!list) return;
  list.innerHTML = '';
  if (sec) sec.hidden = genIdeas.length === 0;
  genIdeas.forEach(function (idea) {
    if (!idea) return;
    var row = seatRow('💡', idea.title || 'Idea', idea.body || '');
    var btns = document.createElement('div');
    btns.className = 'og-idea-btns';
    var ask = document.createElement('button');
    ask.type = 'button'; ask.className = 'og-idea-ask';
    ask.textContent = 'Ask OG';
    ask.addEventListener('click', function () { askInChat(idea.ask || idea.title || ''); });
    var dis = document.createElement('button');
    dis.type = 'button'; dis.className = 'og-idea-dismiss';
    dis.textContent = 'Dismiss';
    dis.addEventListener('click', function () {
      ask.disabled = true; dis.disabled = true;
      fetch('/ideas/dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: idea.id })
      }).then(function () { refreshGenIdeas(); })
        .catch(function () { refreshGenIdeas(); });
    });
    btns.appendChild(ask); btns.appendChild(dis);
    row.querySelector('.og-appr-main').appendChild(btns);
    list.appendChild(row);
  });
  renderIdeasEmpty();
}
function refreshGenIdeas() {
  getJSON('/ideas').then(function (d) {
    genIdeas = (d && d.ideas) || [];
    renderGenIdeas();
  });
}

/* --- 8. Feed seat: OG's stream ------------------------------------------
   Round 70 (addition): the filter chips are GONE (Brent's
   order) and the ordering moved server-side — GET /feed
   merges the caller's library items (/library's sources)
   with their notifications and returns the rows in the
   feed algorithm's order (og_library.feed_rows is the one
   ordering function); this client renders payload order
   verbatim, no client sort, no filtering. Library rows
   keep their action (Play/Open/Download); notification
   rows route per Item A. setFeedTab survives as a
   render-only shim: notification tap-through still calls
   it (with a kind that no longer filters anything) before
   the routed-item flash renders. */
var feedRows = [], feedLibIds = {};
var LIB_ICONS = { video: '🎬', song: '🎵', image: '🖼', file: '📄', project: '🧩', podcast: '🎙️', book: '📖', motion: '🎞️', sfx: '🔊' };
var KIND_LABELS = { video: 'Video', song: 'Song', image: 'Image', file: 'File', project: 'Project', podcast: 'Podcast', book: 'Book', motion: 'Motion', sfx: 'Sound FX' };
var NOTIF_ICONS = {
  watch_match: '👀', price_alert: '💲', video_done: '🎬', song_done: '🎵',
  podcast_done: '🎙️', book_done: '📖', motion_done: '🎞️',
  sfx_done: '🔊', voice_done: '🎙️',
  storage_warning: '🗄', notice: '🔔', approval_needed: '✋',
  signin_needed: '🔑', system_check: '🩺', fix_needed: '🔧'
};
function setFeedTab(kind) {
  /* Chips removed in Round 70 — the kind is accepted (the
     notification router passes one) and ignored; the feed
     shows everything in the server's order. */
  renderFeed();
}

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

/* Round 95: the manual clear (Brent: "there's no clear").
   The Feed seat is where notifications are consumed now
   that the bell is gone: a per-row dismiss ✕ plus Clear
   read / Clear all at the top of the seat. Every clear
   is a server POST — the row is gone server-side or it
   isn't cleared; both list surfaces re-read the store
   after (no client-side fake clearing). */
function clearNotification(body) {
  return fetch('/notifications/clear', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; });
}
function afterClear(d) {
  if (d && typeof d.unread === 'number') {
    feedUnread = d.unread;
    paintFeedDot();
  }
  refreshFeed();
  refreshRecents();
}
function dismissFeedNotif(id) {
  clearNotification({ id: id }).then(afterClear);
}
function clearFeedScope(scope) {
  clearNotification({ scope: scope }).then(afterClear);
}
var clearReadBtn = $('ogFeedClearRead'), clearAllBtn = $('ogFeedClearAll');
if (clearReadBtn) clearReadBtn.addEventListener('click', function () { clearFeedScope('read'); });
if (clearAllBtn) clearAllBtn.addEventListener('click', function () { clearFeedScope('all'); });

function renderFeed() {
  var list = $('ogFeedList'), empty = $('ogFeedEmpty');
  if (!list) return false;
  list.innerHTML = '';
  var shown = 0, flashed = false;
  feedRows.forEach(function (r) {
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
      var dis = document.createElement('button');
      dis.type = 'button';
      dis.className = 'og-feed-dismiss';
      dis.setAttribute('aria-label', 'Dismiss notification');
      dis.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
      dis.addEventListener('click', function (ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        dismissFeedNotif(r.id);
      });
      row.appendChild(dis);
    }
    if (flashLibId && r.src === 'lib' && r.id === flashLibId) {
      row.classList.add('og-feed-flash');
      flashed = true;
      if (row.scrollIntoView) row.scrollIntoView();
    }
    list.appendChild(row);
  });
  if (empty) empty.hidden = shown !== 0;
  var clearRow = $('ogFeedClearRow');
  if (clearRow) {
    clearRow.hidden = !feedRows.some(function (r) {
      return r && r.src === 'ntf';
    });
  }
  return flashed;
}

function refreshFeed() {
  getJSON('/feed')
    .then(function (d) {
      if (!d) {
        /* Round 95: a FAILED load is not an empty feed.
           The old code painted the genuine-empty copy on
           any failure — while the bell, polling on its own
           clock and holding its last list, stayed full.
           Same pile, two failure shapes: that was the
           "bell full, feed empty" mismatch. Now the feed
           keeps its last good rows and the note line says
           plainly that the load failed. */
        var note = $('ogFeedNote');
        if (note) {
          note.hidden = false;
          note.textContent = feedRows.length
            ? 'Couldn\'t refresh just now — showing the last list I got.'
            : 'Couldn\'t load just now — try again in a moment.';
        }
        var emptyEl = $('ogFeedEmpty');
        if (emptyEl) emptyEl.hidden = true;
        return;
      }
      var noteOk = $('ogFeedNote');
      if (noteOk) noteOk.hidden = true;
      /* Round 321 (V4): the server degraded one leg of
         the payload instead of failing the seat whole —
         say so on the same note line, over the rows that
         DID load. */
      if (d.degraded) {
        var noteDeg = $('ogFeedNote');
        if (noteDeg) {
          noteDeg.hidden = false;
          noteDeg.textContent = 'Some of the Feed couldn\'t load just now — showing what I could get.';
        }
      }
      if (typeof d.unread === 'number') { feedUnread = d.unread; paintFeedDot(); }
      feedLibIds = {};
      feedRows = (d.rows || []).map(function (r) {
        if (!r) return null;
        if (r.src === 'lib') feedLibIds[r.id] = true;
        if (r.src === 'ntf') r.raw = r.raw || r;
        return r;
      }).filter(function (r) { return !!r; });
      var st = $('ogFeedStorage');
      if (st) {
        st.hidden = !d.storage_line;
        if (d.storage_line) st.textContent = d.storage_line;
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

/* --- 8b. Progress seat (Round 70 addition) ------------------------------
   Everything being worked on for the signed-in user, from
   the ONE aggregate endpoint GET /progress (og_progress.py
   reads the existing job stores — nothing is recomputed or
   invented here). The live % renders ONLY when the payload
   carries a number, and the server sends a number only for
   a task in an actively-working state with real progress
   counts (Brent, 2026-10-11: "Only tasks that are being
   worked on will have the live %."). Queued / waiting /
   done rows show their state words and no bar. The seat
   polls every 4 s while it is open so a working % moves
   until the item lands as done. */
var PROG_SECTIONS = [
  ['making', 'Making'], ['browser', 'Browser'],
  ['queries', 'Queries running'], ['watching', 'Watching'],
  ['fixes', 'Fixes waiting']
];
function progressRow(r) {
  var row = seatRow(r.icon || '•', r.title || '', '');
  var main = row.querySelector('.og-appr-main');
  var b = document.createElement('div');
  b.className = 'og-appr-item-body';
  var stateTxt = r.state_label || r.state || '';
  if (typeof r.pct === 'number') {
    b.innerHTML = '';
    var s1 = document.createElement('span');
    s1.textContent = stateTxt + ' — ';
    var s2 = document.createElement('span');
    s2.className = 'og-prog-pct';
    s2.textContent = Math.round(r.pct) + '%';
    b.appendChild(s1); b.appendChild(s2);
    if (r.sub) {
      var s3 = document.createElement('span');
      s3.textContent = ' · ' + r.sub;
      b.appendChild(s3);
    }
    var bar = document.createElement('div');
    bar.className = 'og-prog-bar';
    var fill = document.createElement('span');
    fill.style.width = Math.max(0, Math.min(100, r.pct)) + '%';
    bar.appendChild(fill);
    main.appendChild(b); main.appendChild(bar);
  } else {
    b.textContent = stateTxt + (r.sub ? ' · ' + r.sub : '');
    main.appendChild(b);
  }
  return row;
}
function refreshProgress() {
  var sum = $('ogProgressSummary'), list = $('ogProgressList'),
      empty = $('ogProgressEmpty');
  if (!list) return;
  getJSON('/progress').then(function (d) {
    d = d || {};
    var rows = d.rows || [];
    list.innerHTML = '';
    var lastSec = null;
    rows.forEach(function (r) {
      if (!r) return;
      if (r.section !== lastSec) {
        lastSec = r.section;
        var label = lastSec;
        PROG_SECTIONS.forEach(function (p) { if (p[0] === lastSec) label = p[1]; });
        var h = document.createElement('h3');
        h.className = 'og-appr-sec'; h.textContent = label;
        list.appendChild(h);
      }
      list.appendChild(progressRow(r));
    });
    if (d.auto_update) {
      var au = d.auto_update;
      var h2 = document.createElement('h3');
      h2.className = 'og-appr-sec'; h2.textContent = 'OG self-update';
      list.appendChild(h2);
      list.appendChild(seatRow('🌙', au.title || 'Nightly auto-update', au.sub || ''));
    }
    var working = rows.filter(function (r) { return r && r.working; }).length;
    if (sum) sum.textContent = rows.length === 0
      ? 'Nothing in flight.'
      : rows.length + (rows.length === 1 ? ' thing' : ' things') + ' on the board' +
        (working > 0 ? ' · ' + working + ' actively working' : '');
    if (empty) empty.hidden = rows.length !== 0 || !!d.auto_update;
  }).catch(function () {
    if (sum) sum.textContent = 'Couldn\'t load right now — try again in a moment.';
  });
}

/* --- 8c. Keyboard-up rules (Round 106; Brent, 2026-10-12) ------------
   "There extra stuff on the keyboard" + "And the bar
   that's underneath need to stay down there out of site
   when I keyboard up." While the on-screen keyboard is
   up, ONLY the typing bar sits above it: the seat bar
   stays down (out of sight) and the composer's extra
   stack (attach chip, Add sheet, picture preview) and
   the generator cards clear out. og_shell.css carries
   the body.og-kb-up rules; this section only DETECTS.
   Detection is the real iOS signal: Safari does not
   resize the layout viewport for the keyboard — it
   shrinks the VISUAL viewport — so keyboard-up is
   innerHeight - visualViewport.height growing past a
   threshold. A second signal covers layout-resizing
   browsers (Android Chrome): the window's innerHeight
   itself drops well below the tallest height seen
   while no keyboard was up, with a text field focused.
   Keyboard down -> the class comes off and every rule
   lifts: the shell is byte-for-byte the old behavior,
   and hiding the seat bar never changes the active
   seat. This overrides the R59 "bars never disappear"
   rule for the keyboard-up state only (Brent's latest
   word); keyboard-down seat behavior is unchanged. */
var ogKbBaseH = window.innerHeight, ogKbUp = false;
function ogKbEval() {
  var vv = window.visualViewport;
  var overlap = vv ? (window.innerHeight - vv.height) : 0;
  var ae = document.activeElement;
  var typing = !!(ae && (ae.tagName === 'INPUT' ||
    ae.tagName === 'TEXTAREA'));
  var up = overlap > 120 ||
    (typing && window.innerHeight < ogKbBaseH - 150);
  if (!up && window.innerHeight > ogKbBaseH) {
    ogKbBaseH = window.innerHeight;
  }
  if (up !== ogKbUp) {
    ogKbUp = up;
    document.body.classList.toggle('og-kb-up', up);
  }
}
if (window.visualViewport &&
    window.visualViewport.addEventListener) {
  window.visualViewport.addEventListener('resize', ogKbEval);
}
if (window.addEventListener) {
  window.addEventListener('resize', ogKbEval);
}
if (document.addEventListener) {
  document.addEventListener('focusin', function () {
    setTimeout(ogKbEval, 60);
  });
  document.addEventListener('focusout', function () {
    setTimeout(ogKbEval, 60);
  });
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
  if (activeSeat === 'progress') refreshGoals();
  if (activeSeat === 'ideas') { refreshFixes(); refreshGenIdeas(); }
  if (activeSeat === 'feed') refreshFeed();
}, 20000);
setInterval(function () {
  if (isHidden($('ogSeatScreen'))) return;
  if (activeSeat === 'progress') refreshProgress();
}, 4000);
setInterval(function () {
  paintSeats();
  updateNeedsPill();
}, 900);
paintSeats();
})();

/* OG Notification settings (Round 38, Brent 2026-10-10;
   Round 95, Brent 2026-10-12): a dedicated settings page
   for how OG reaches you. Round 38 built it for the
   approval + sign-in alerts — the two moments OG needs
   the visitor (an action parked on their OK; a login
   wall only they can pass):

     alerts       — the master switch (off = no record,
                    no email, no push for these alerts);
     alerts_email — these alerts also go out by email;
     alerts_push  — these alerts also go out as phone push.

   Round 95 (the bell is deleted — "it's in the idea at
   the bottom, so we don't need that at the top"): the
   bell panel died as a surface, and this page is now the
   ONE home for every notification switch — the two
   global switches that used to live in the bell panel
   (Email notifications / Push notifications, the
   /notifications/prefs "email" / "push" keys) moved
   here, and the push SUBSCRIBE flow moved with them
   (it lived in the panel too). No setting was lost in
   the move. The menu row moved from the Tools card to
   the ACCOUNT group, beside the other account rows.
   The switches read/write the existing
   /notifications/prefs contract with partial (merge)
   POSTs. The sheet DOM and the menu row are built HERE,
   not in the page markup, to keep the page under its
   push ceiling. */
(function () {
var $ = function (id) { return document.getElementById(id); };

// --- The sheet (built once, hidden until opened) ----------------
var scrim = document.createElement('div');
scrim.className = 'og-nset-scrim';
scrim.id = 'nsetScrim';
scrim.hidden = true;
var panel = document.createElement('section');
panel.className = 'og-nset';
panel.id = 'ogNotificationSettings';
panel.hidden = true;
panel.setAttribute('role', 'dialog');
panel.setAttribute('aria-modal', 'true');
panel.setAttribute('aria-label', 'Notification settings');
panel.innerHTML =
  '<div class="og-nset-top">'
  + '<button id="nsetCloseBtn" class="og-nset-close" type="button" aria-label="Close">'
  + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>'
  + '</button><h2>Notification settings</h2></div>'
  + '<div class="og-nset-scroll">'
  + '<p class="og-nset-intro">How OG reaches you. Your notifications themselves live in the Feed seat now — this page is only about the copies that leave the app: email and phone push.</p>'
  + '<h3 class="og-nset-sec">All notifications</h3>'
  + '<div class="og-nset-card">'
  + '<label class="og-nset-row"><span>Email notifications</span><input type="checkbox" id="nsetEmail"></label>'
  + '<label class="og-nset-row"><span>Push notifications</span><input type="checkbox" id="nsetPush"></label>'
  + '</div>'
  + '<p class="og-nset-note">Email goes to your OG account email — it needs an account, and it only sends once email is switched on for OG.</p>'
  + '<p class="og-nset-note">On iPhone, push works after you add OG to your Home Screen — that\'s an Apple rule.</p>'
  + '<p class="og-nset-note" id="nsetPushMsg" hidden></p>'
  + '<h3 class="og-nset-sec">Approval &amp; sign-in alerts</h3>'
  + '<div class="og-nset-card">'
  + '<label class="og-nset-row"><span>Approval &amp; sign-in alerts</span><input type="checkbox" id="nsetAlerts"></label>'
  + '<label class="og-nset-row"><span>Email</span><input type="checkbox" id="nsetAlertsEmail"></label>'
  + '<label class="og-nset-row"><span>Phone push</span><input type="checkbox" id="nsetAlertsPush"></label>'
  + '</div>'
  + '<p class="og-nset-note">The alert switches are only for the alerts OG sends when he needs YOU — when an action is waiting on your OK, or a site needs you to sign in. Email for these goes to your OG account email.</p>'
  + '<p class="og-nset-note">Phone push for these alerts only lands after this phone or browser has allowed notifications and subscribed — the Push notifications switch above is that setup. These switches only decide whether the alerts use it.</p>'
  + '<p class="og-nset-error" id="nsetError" hidden>That change didn\'t save — the switch is back where it was. Try again.</p>'
  + '</div>';
document.body.appendChild(scrim);
document.body.appendChild(panel);
var alertsBox = $('nsetAlerts'), alertsEmailBox = $('nsetAlertsEmail'),
alertsPushBox = $('nsetAlertsPush'), emailBox = $('nsetEmail'),
pushBox = $('nsetPush'), pushMsg = $('nsetPushMsg'),
errEl = $('nsetError'), closeBtn = $('nsetCloseBtn');
if (!alertsBox || !alertsEmailBox || !alertsPushBox) return;

// --- Prefs wiring ---------------------------------------------------
// Alert switches default ON; the global email/push
// switches default OFF (the prefs contract's own
// defaults — the same values the bell panel read).
function loadPrefs() {
fetch('/notifications/prefs').then(function (r) { return r.json(); }).then(function (p) {
if (!p) return;
alertsBox.checked = p.alerts !== false;
alertsEmailBox.checked = p.alerts_email !== false;
alertsPushBox.checked = p.alerts_push !== false;
if (emailBox) emailBox.checked = !!p.email;
if (pushBox) pushBox.checked = !!p.push;
}).catch(function () { });
}
function postPrefs(body) {
return fetch('/notifications/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
function write(box, key) {
var want = !!box.checked;
var body = {};
body[key] = want;
postPrefs(body).then(function (r) {
if (!r.ok) throw new Error('save failed');
return r.json();
}).then(function (p) {
if (p && typeof p[key] === 'boolean') box.checked = p[key];
if (errEl) errEl.hidden = true;
}).catch(function () {
box.checked = !want;  // a failed write flips the switch back
if (errEl) errEl.hidden = false;
});
}
alertsBox.addEventListener('change', function () { write(alertsBox, 'alerts'); });
alertsEmailBox.addEventListener('change', function () { write(alertsEmailBox, 'alerts_email'); });
alertsPushBox.addEventListener('change', function () { write(alertsPushBox, 'alerts_push'); });
if (emailBox) emailBox.addEventListener('change', function () { write(emailBox, 'email'); });

// --- Push subscribe flow (moved from the bell panel, Round 95) ------
function showPushMsg(t) {
if (!pushMsg) return;
pushMsg.hidden = !t;
pushMsg.textContent = t || '';
}
function urlB64ToUint8Array(b64) {
var pad = '='.repeat((4 - (b64.length % 4)) % 4);
var raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
var out = new Uint8Array(raw.length);
for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
return out;
}
function enablePush() {
if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
pushBox.checked = false;
showPushMsg('Push is not available in this browser.');
postPrefs({ push: false });
return;
}
navigator.serviceWorker.register('/sw.js').then(function (reg) {
return Notification.requestPermission().then(function (perm) {
if (perm !== 'granted') {
pushBox.checked = false;
showPushMsg('Push is blocked — if you want it, allow notifications for OG in your browser settings.');
postPrefs({ push: false });
return null;
}
return fetch('/notifications/vapid').then(function (r) { return r.json(); }).then(function (v) {
if (!v || !v.public_key) {
pushBox.checked = false;
showPushMsg('Push is not ready on the server yet.');
return null;
}
return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(v.public_key) });
});
});
}).then(function (sub) {
if (!sub) return;
return fetch('/notifications/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subscription: sub.toJSON() }) }).then(function () {
postPrefs({ push: true });
showPushMsg('');
});
}).catch(function () {
pushBox.checked = false;
showPushMsg('Something blocked the push setup — try again.');
postPrefs({ push: false });
});
}
function disablePush() {
postPrefs({ push: false });
if (!('serviceWorker' in navigator)) return;
navigator.serviceWorker.getRegistration('/sw.js').then(function (reg) {
if (!reg) return;
reg.pushManager.getSubscription().then(function (sub) {
if (!sub) return;
var ep = sub.endpoint;
sub.unsubscribe().catch(function () { });
fetch('/notifications/unsubscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: ep }) }).catch(function () { });
}).catch(function () { });
}).catch(function () { });
}
if (pushBox) pushBox.addEventListener('change', function () {
if (pushBox.checked) enablePush();
else disablePush();
});
if ('serviceWorker' in navigator) {
try { navigator.serviceWorker.register('/sw.js').catch(function () { }); } catch (e) { }
}

// --- Open / close ---------------------------------------------------
function openSettings() {
var mc = $('menuClose'); if (mc) mc.click();
scrim.hidden = false;
panel.hidden = false;
if (errEl) errEl.hidden = true;
loadPrefs();
}
function closeSettings() {
scrim.hidden = true;
panel.hidden = true;
}
if (closeBtn) closeBtn.addEventListener('click', closeSettings);
scrim.addEventListener('click', closeSettings);

// --- The main-menu row ------------------------------------------------
// Round 95: the row lives in the ACCOUNT group now
// (menuAccountCard — the same card the Voice and
// Shit-talking rows ride), not at the end of the Tools
// card where Round 38 put it.
function buildMenuRow() {
if ($('menuNotifSettingsBtn')) return;
var card = $('menuAccountCard');
if (!card) return;
var btn = document.createElement('button');
btn.className = 'og-menu-item';
btn.id = 'menuNotifSettingsBtn';
btn.type = 'button';
btn.innerHTML = '<span class="og-mi">🔔</span> Notification settings';
card.appendChild(btn);
btn.addEventListener('click', openSettings);
}
buildMenuRow();
})();

(function () {
var $ = function (id) { return document.getElementById(id); };
var bell = $('ogNotifyBell'), dot = $('ogNotifyDot'),
scrim = $('notifyScrim'), panel = $('ogNotifyPanel'),
listEl = $('notifyList'), emptyEl = $('notifyEmpty'),
readAllBtn = $('notifyReadAllBtn'), closeBtn = $('notifyCloseBtn');
if (!bell) return;
buildPrefs();
var emailBox = $('notifyPrefEmail'), pushBox = $('notifyPrefPush'),
pushMsg = $('notifyPushMsg');
var ICONS = { watch_match: '👀', price_alert: '📈', video_done: '🎬', song_done: '🎵', storage_warning: '⚠️', notice: '🔔', approval_needed: '✋', signin_needed: '🔑' };
var lastItems = [];
function buildPrefs() {
// The preferences section is built here (not in the page
// markup) to keep the page under its push ceiling.
if (!panel) return;
var scroll = panel.querySelector('.og-notify-scroll');
if (!scroll || $('notifyPrefEmail')) return;
var wrap = document.createElement('div');
wrap.className = 'og-notify-prefs';
var h = document.createElement('h3');
h.className = 'og-notify-sec';
h.textContent = 'How OG reaches you';
wrap.appendChild(h);
var card = document.createElement('div');
card.className = 'og-notify-card';
function row(label, id) {
var lab = document.createElement('label');
lab.className = 'og-notify-row';
var sp = document.createElement('span');
sp.textContent = label;
var inp = document.createElement('input');
inp.type = 'checkbox';
inp.id = id;
lab.appendChild(sp);
lab.appendChild(inp);
card.appendChild(lab);
}
row('Email notifications', 'notifyPrefEmail');
row('Push notifications', 'notifyPrefPush');
wrap.appendChild(card);
function note(text, id) {
var p = document.createElement('p');
p.className = 'og-notify-note';
p.textContent = text;
if (id) { p.id = id; p.hidden = true; }
wrap.appendChild(p);
}
note('Email goes to your OG account email — it needs an account, and it only sends once email is switched on for OG.');
note("On iPhone, push works after you add OG to your Home Screen — that's an Apple rule.");
note('', 'notifyPushMsg');
scroll.appendChild(wrap);
}
function relTime(ts) {
if (!ts) return '';
var s = Math.max(0, (Date.now() / 1000) - ts);
if (s < 60) return 'just now';
if (s < 3600) return Math.floor(s / 60) + 'm ago';
if (s < 86400) return Math.floor(s / 3600) + 'h ago';
if (s < 604800) return Math.floor(s / 86400) + 'd ago';
try { return new Date(ts * 1000).toLocaleDateString(); } catch (e) { return ''; }
}
function paintDot(unread) { if (dot) dot.hidden = !(unread > 0); }
function render(items) {
if (!listEl) return;
while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
if (emptyEl) emptyEl.hidden = items.length > 0;
if (readAllBtn) readAllBtn.hidden = items.length === 0;
items.forEach(function (it) {
var row = document.createElement('div');
row.className = 'og-notify-item' + (it.read ? '' : ' unread');
var ico = document.createElement('span');
ico.className = 'og-notify-ico';
ico.textContent = ICONS[it.kind] || '🔔';
var main = document.createElement('div');
main.className = 'og-notify-main';
var t = document.createElement('div');
t.className = 'og-notify-item-title';
t.textContent = it.title || '';
main.appendChild(t);
if (it.body) {
var b = document.createElement('div');
b.className = 'og-notify-item-body';
b.textContent = it.body;
main.appendChild(b);
}
var when = document.createElement('div');
when.className = 'og-notify-item-time';
when.textContent = relTime(it.ts);
main.appendChild(when);
row.appendChild(ico);
row.appendChild(main);
row.addEventListener('click', function () { if (!it.read) markRead(it.id); });
listEl.appendChild(row);
});
}
function refresh() {
fetch('/notifications').then(function (r) { return r.json(); }).then(function (d) {
lastItems = (d && d.items) || [];
paintDot((d && d.unread) || 0);
if (panel && !panel.hidden) render(lastItems);
}).catch(function () { });
}
function markRead(id) {
fetch('/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) }).then(function () { refresh(); }).catch(function () { });
}
function postPrefs(p) {
return fetch('/notifications/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) }).catch(function () { });
}
function loadPrefs() {
fetch('/notifications/prefs').then(function (r) { return r.json(); }).then(function (p) {
if (!p) return;
if (emailBox) emailBox.checked = !!p.email;
if (pushBox) pushBox.checked = !!p.push;
}).catch(function () { });
}
function openPanel() {
if (scrim) scrim.hidden = false;
if (panel) panel.hidden = false;
render(lastItems);
loadPrefs();
refresh();
}
function closePanel() {
if (scrim) scrim.hidden = true;
if (panel) panel.hidden = true;
}
bell.addEventListener('click', openPanel);
if (closeBtn) closeBtn.addEventListener('click', closePanel);
if (scrim) scrim.addEventListener('click', closePanel);
if (readAllBtn) readAllBtn.addEventListener('click', function () {
fetch('/notifications/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ all: true }) }).then(function () { refresh(); }).catch(function () { });
});
if (emailBox) emailBox.addEventListener('change', function () { postPrefs({ email: !!emailBox.checked }); });
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
refresh();
setInterval(refresh, 30000);
document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
})();

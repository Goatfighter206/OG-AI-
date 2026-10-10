/* OG Notification settings (Round 38, Brent 2026-10-10):
   a dedicated settings page for the approval + sign-in
   alerts — the two moments OG needs the visitor (an action
   parked on their OK; a login wall only they can pass).
   Three switches, all default ON, all governing ONLY the
   approval_needed / signin_needed notification kinds:
     alerts       — the master switch (off = no record,
                    no email, no push for these alerts);
     alerts_email — these alerts also go out by email;
     alerts_push  — these alerts also go out as phone push.
   The switches read/write the existing /notifications/prefs
   contract with partial (merge) POSTs. The sheet DOM and
   the main-menu row are built HERE, not in the page markup,
   to keep the page under its push ceiling. Push itself is
   subscribed from the bell panel — this page never
   re-implements that flow; it only gates whether these
   alerts use it. */
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
  + '<p class="og-nset-intro">These switches are only for the alerts OG sends when he needs YOU — when an action is waiting on your OK, or a site needs you to sign in. Your other notifications keep their own settings in the bell panel.</p>'
  + '<h3 class="og-nset-sec">Approval &amp; sign-in alerts</h3>'
  + '<div class="og-nset-card">'
  + '<label class="og-nset-row"><span>Approval &amp; sign-in alerts</span><input type="checkbox" id="nsetAlerts"></label>'
  + '<label class="og-nset-row"><span>Email</span><input type="checkbox" id="nsetAlertsEmail"></label>'
  + '<label class="og-nset-row"><span>Phone push</span><input type="checkbox" id="nsetAlertsPush"></label>'
  + '</div>'
  + '<p class="og-nset-note">Email goes to your OG account email.</p>'
  + '<p class="og-nset-note">Phone push only lands after this phone or browser has allowed notifications and subscribed — that setup lives in the bell panel. This switch only decides whether these alerts use push.</p>'
  + '<p class="og-nset-error" id="nsetError" hidden>That change didn\'t save — the switch is back where it was. Try again.</p>'
  + '</div>';
document.body.appendChild(scrim);
document.body.appendChild(panel);
var alertsBox = $('nsetAlerts'), emailBox = $('nsetAlertsEmail'),
pushBox = $('nsetAlertsPush'), errEl = $('nsetError'),
closeBtn = $('nsetCloseBtn');
if (!alertsBox || !emailBox || !pushBox) return;

// --- Prefs wiring (defaults: all three ON) ------------------------
function loadPrefs() {
fetch('/notifications/prefs').then(function (r) { return r.json(); }).then(function (p) {
if (!p) return;
alertsBox.checked = p.alerts !== false;
emailBox.checked = p.alerts_email !== false;
pushBox.checked = p.alerts_push !== false;
}).catch(function () { });
}
function write(box, key) {
var want = !!box.checked;
var body = {};
body[key] = want;
fetch('/notifications/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (r) {
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
emailBox.addEventListener('change', function () { write(emailBox, 'alerts_email'); });
pushBox.addEventListener('change', function () { write(pushBox, 'alerts_push'); });

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
// Mounted at the end of the Tools card (the card that holds
// the Library row) in the ☰ side menu.
function buildMenuRow() {
var lib = $('menuLibraryBtn');
if (!lib || !lib.parentNode || $('menuNotifSettingsBtn')) return;
var btn = document.createElement('button');
btn.className = 'og-menu-item';
btn.id = 'menuNotifSettingsBtn';
btn.type = 'button';
btn.innerHTML = '<span class="og-mi">🔔</span> Notification settings';
lib.parentNode.appendChild(btn);
btn.addEventListener('click', openSettings);
}
buildMenuRow();
})();

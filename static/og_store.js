// OG store readiness (Round 36): the Report button on OG's
// replies and the Delete account flow. Both live here (not
// in the page markup) to keep the page under its push
// ceiling — the og_notify.js pattern. Everything below is
// inert unless the visitor is signed in: /auth/me decides.
(function () {
var $ = function (id) { return document.getElementById(id); };

// After a successful deletion the page reloads onto the
// auth wall; this flag is how the wall learns to say so.
try {
  if (sessionStorage.getItem('ogAccountDeleted') === '1') {
    sessionStorage.removeItem('ogAccountDeleted');
    var main = $('authMain');
    if (main && main.parentNode) {
      var p = document.createElement('p');
      p.className = 'og-deleted-line';
      p.textContent = 'Your account was deleted.';
      main.parentNode.insertBefore(p, main);
    }
  }
} catch (e) {}

fetch('/auth/me').then(function (r) { return r.json(); })
.then(function (me) {
  if (!me || !me.signed_in) return;
  buildDeleteRow();
  watchMessages();
}).catch(function () {});

// --- Report a reply ---------------------------------------------------------

function replyText(msgEl) {
  var t = msgEl.querySelector('.message-text');
  return t ? (t.innerText || t.textContent || '') : '';
}

function addReportButton(msgEl) {
  if (!msgEl || msgEl.dataset.ogReport) return;
  msgEl.dataset.ogReport = '1';
  var mc = msgEl.querySelector('.message-content');
  if (!mc) return;
  var wrap = document.createElement('div');
  wrap.className = 'og-report-wrap';
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'og-report-btn';
  btn.textContent = 'Report';
  wrap.appendChild(btn);
  var form = document.createElement('div');
  form.className = 'og-report-form';
  form.hidden = true;
  var input = document.createElement('input');
  input.className = 'og-report-input';
  input.type = 'text';
  input.maxLength = 200;
  input.placeholder = 'Why are you reporting this? (optional)';
  var send = document.createElement('button');
  send.type = 'button';
  send.className = 'og-report-send';
  send.textContent = 'Send report';
  var cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'og-report-cancel';
  cancel.textContent = 'Cancel';
  var status = document.createElement('span');
  status.className = 'og-report-status';
  form.appendChild(input);
  form.appendChild(send);
  form.appendChild(cancel);
  form.appendChild(status);
  wrap.appendChild(form);
  mc.appendChild(wrap);
  btn.addEventListener('click', function () {
    form.hidden = !form.hidden;
    if (!form.hidden) input.focus();
  });
  cancel.addEventListener('click', function () {
    form.hidden = true;
    status.textContent = '';
  });
  send.addEventListener('click', function () {
    send.disabled = true;
    status.textContent = 'Sending…';
    fetch('/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: replyText(msgEl).slice(0, 2000),
        reason: input.value.slice(0, 200)
      })
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok) {
        wrap.innerHTML = '';
        var done = document.createElement('span');
        done.className = 'og-report-done';
        done.textContent = 'Reported — thanks.';
        wrap.appendChild(done);
      } else {
        status.textContent = (res.d && (res.d.error || res.d.detail))
          || "That didn't send — try again.";
        send.disabled = false;
      }
    }).catch(function () {
      status.textContent = "That didn't send — try again.";
      send.disabled = false;
    });
  });
}

function watchMessages() {
  var area = $('messagesArea');
  if (!area) return;
  area.querySelectorAll('.message.ai').forEach(addReportButton);
  var mo = new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.classList && n.classList.contains('message')
            && n.classList.contains('ai')) addReportButton(n);
        if (n.querySelectorAll) {
          n.querySelectorAll('.message.ai').forEach(addReportButton);
        }
      });
    });
  });
  mo.observe(area, { childList: true, subtree: true });
}

// --- Delete account -----------------------------------------------------------

function buildDeleteRow() {
  var card = $('menuAccountCard');
  if (!card || $('menuDeleteBtn')) return;
  var btn = document.createElement('button');
  btn.className = 'og-menu-item og-delete-row';
  btn.id = 'menuDeleteBtn';
  btn.type = 'button';
  btn.innerHTML = '<span class="og-mi"><svg><use href="#i-trash"/></svg></span> Delete account';
  card.appendChild(btn);
  btn.addEventListener('click', openDeleteView);
}

function closeDeleteView() {
  var scrim = $('ogDeleteScrim');
  if (scrim) scrim.remove();
}

function openDeleteView() {
  if ($('ogDeleteScrim')) return;
  var scrim = document.createElement('div');
  scrim.className = 'og-delete-scrim';
  scrim.id = 'ogDeleteScrim';
  var panel = document.createElement('div');
  panel.className = 'og-delete-panel';
  var h = document.createElement('h3');
  h.className = 'og-delete-title';
  h.textContent = 'Delete your account?';
  panel.appendChild(h);
  var body = document.createElement('p');
  body.className = 'og-delete-body';
  body.textContent = 'This permanently deletes your account and '
    + 'everything tied to it — OG\u2019s memory of your '
    + 'conversations, your saved files, watches, browser log-ins '
    + 'and connected accounts. There is no undo.';
  panel.appendChild(body);
  var label = document.createElement('label');
  label.className = 'og-delete-label';
  label.textContent = 'Type your password to confirm';
  panel.appendChild(label);
  var pw = document.createElement('input');
  pw.className = 'og-delete-input';
  pw.type = 'password';
  pw.autocomplete = 'current-password';
  panel.appendChild(pw);
  var err = document.createElement('p');
  err.className = 'og-delete-error';
  err.hidden = true;
  panel.appendChild(err);
  var del = document.createElement('button');
  del.type = 'button';
  del.className = 'og-delete-btn';
  del.textContent = 'Delete my account';
  panel.appendChild(del);
  var cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'og-delete-cancel';
  cancel.textContent = 'Cancel';
  panel.appendChild(cancel);
  scrim.appendChild(panel);
  document.body.appendChild(scrim);
  cancel.addEventListener('click', closeDeleteView);
  scrim.addEventListener('click', function (e) {
    if (e.target === scrim) closeDeleteView();
  });
  del.addEventListener('click', function () {
    err.hidden = true;
    del.disabled = true;
    del.textContent = 'Deleting…';
    fetch('/auth/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw.value })
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok) {
        try { sessionStorage.setItem('ogAccountDeleted', '1'); }
        catch (e) {}
        location.reload();
      } else {
        err.textContent = (res.d && (res.d.error || res.d.detail))
          || "That didn't work — try again.";
        err.hidden = false;
        del.disabled = false;
        del.textContent = 'Delete my account';
      }
    }).catch(function () {
      err.textContent = "That didn't work — try again.";
      err.hidden = false;
      del.disabled = false;
      del.textContent = 'Delete my account';
    });
  });
  pw.focus();
}
})();

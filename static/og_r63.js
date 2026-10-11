/* Round 63: 2-step verification (web).
 *
 * Two pieces, both in this asset because the page itself is
 * at its push ceiling (the R62 pattern — this file loads via
 * a stub in static/og_tokens.js):
 *
 * 1. LOGIN CODE STEP. The page's own sign-in handler cannot
 *    represent a two_factor_required reply (any non-ok is an
 *    error there, and its success path would bounce off
 *    /auth/me and lose the challenge), so this asset takes
 *    over #authSigninForm submits in the CAPTURE phase and
 *    runs the login POST itself — exactly one POST per
 *    submit (failure counting stays honest). Plain success
 *    (or a verified code / passkey) reloads the page, which
 *    lands signed-in via /auth/me. A two_factor_required
 *    reply swaps the form for a code step: code input ->
 *    POST /auth/2fa/verify, or "Use passkey" ->
 *    navigator.credentials.get -> POST
 *    /auth/2fa/passkey/verify.
 *
 * 2. ACCOUNT SECURITY. A section injected under the menu's
 *    Account card: 2-step status, TOTP setup (the secret +
 *    otpauth URI shown as TEXT for manual authenticator
 *    entry — there is no QR library on this page, so no QR
 *    is promised or faked), turn-on with a code, recovery
 *    codes shown ONCE with a Copy button, turn-off
 *    (password + code), and Add passkey when the browser
 *    supports WebAuthn. Push approval is labelled honestly:
 *    it comes in a later round.
 */
(function () {
  'use strict';

  var css = ''
    + '.og2fa-step{margin-top:14px}.og2fa-step label{display:block;color:#ccc;'
    + 'font-size:.92em;margin-top:10px}.og2fa-step input{display:block;width:100%;'
    + 'margin-top:5px;padding:11px 12px;border-radius:10px;border:1px solid #444;'
    + 'background:#0d0d0d;color:#f2f2f2;font:inherit;box-sizing:border-box}'
    + '.og2fa-sec{padding:10px 14px;color:#ddd;font-size:.95em}'
    + '.og2fa-sec p{margin:6px 0;line-height:1.45}'
    + '.og2fa-sec code{display:block;word-break:break-all;background:#0d0d0d;'
    + 'border:1px solid #444;border-radius:8px;padding:8px;margin:6px 0;color:#ffc107}'
    + '.og2fa-sec input{display:block;width:100%;margin:6px 0;padding:10px;'
    + 'border-radius:10px;border:1px solid #444;background:#0d0d0d;color:#f2f2f2;'
    + 'font:inherit;box-sizing:border-box}'
    + '.og2fa-btn{display:block;width:100%;margin:8px 0;padding:10px;border-radius:10px;'
    + 'border:1px solid #ffc107;background:#1a1a1a;color:#ffc107;font:inherit;cursor:pointer}'
    + '.og2fa-err{color:#ff7b72}.og2fa-codes{white-space:pre-wrap;word-break:break-all;'
    + 'background:#0d0d0d;border:1px solid #444;border-radius:8px;padding:8px;color:#f2f2f2}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  function $(id) { return document.getElementById(id); }
  function post(path, payload) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { status: r.status, d: d };
      });
    });
  }
  function b64uToBuf(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), buf = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
  }
  function bufToB64u(buf) {
    var bytes = new Uint8Array(buf), out = '';
    for (var i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
    return btoa(out).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  // ------------------------------------------------ login code step
  var challenge = null;
  var stepDiv = null;

  function showStep() {
    var host = $('authSignin');
    if (!host) return;
    var form = $('authSigninForm');
    if (form) form.hidden = true;
    if (!stepDiv) {
      stepDiv = document.createElement('div');
      stepDiv.className = 'og2fa-step';
      stepDiv.innerHTML = '<label>2-step code'
        + '<input id="og2faCode" type="text" inputmode="numeric"'
        + ' autocomplete="one-time-code"></label>'
        + '<p class="og-auth-error" id="og2faError" hidden></p>'
        + '<button class="age-gate-btn" id="og2faVerifyBtn" type="button">Verify</button>'
        + '<button class="age-gate-btn secondary" id="og2faPasskeyBtn" type="button">Use passkey</button>'
        + '<p class="og-auth-linkrow"><a href="#" id="og2faBack">\u2190 Back</a></p>';
      host.appendChild(stepDiv);
      $('og2faVerifyBtn').addEventListener('click', verifyCode);
      $('og2faPasskeyBtn').addEventListener('click', verifyPasskey);
      $('og2faBack').addEventListener('click', function (e) {
        e.preventDefault();
        challenge = null;
        stepDiv.hidden = true;
        if (form) form.hidden = false;
      });
    }
    stepDiv.hidden = false;
    var inp = $('og2faCode');
    if (inp) { inp.value = ''; inp.focus(); }
  }

  function stepErr(msg) {
    var el = $('og2faError');
    if (el) { el.textContent = msg || ''; el.hidden = !msg; }
  }

  function verifyCode() {
    stepErr('');
    post('/auth/2fa/verify', {
      challenge: challenge, code: ($('og2faCode') || {}).value || ''
    }).then(function (res) {
      if (res.d && res.d.ok) { location.reload(); return; }
      stepErr((res.d && res.d.error) || "That code didn't match \u2014 try again.");
    }).catch(function () { stepErr('Connection hiccup \u2014 try that again.'); });
  }

  function verifyPasskey() {
    stepErr('');
    if (!window.PublicKeyCredential || !navigator.credentials) {
      stepErr('This browser has no passkey support \u2014 use a code.');
      return;
    }
    post('/auth/2fa/passkey/options', { challenge: challenge })
      .then(function (res) {
        if (!res.d || !res.d.ok) {
          stepErr((res.d && res.d.error) || 'No passkey on this account \u2014 use a code.');
          return null;
        }
        var o = res.d.options;
        return navigator.credentials.get({
          publicKey: {
            challenge: b64uToBuf(o.challenge),
            rpId: o.rpId,
            allowCredentials: (o.allowCredentials || []).map(function (c) {
              return { type: 'public-key', id: b64uToBuf(c.id) };
            }),
            userVerification: o.userVerification || 'preferred'
          }
        });
      })
      .then(function (cred) {
        if (!cred) return;
        return post('/auth/2fa/passkey/verify', {
          challenge: challenge,
          credential_id: bufToB64u(cred.rawId),
          authenticator_data: bufToB64u(cred.response.authenticatorData),
          client_data_json: bufToB64u(cred.response.clientDataJSON),
          signature: bufToB64u(cred.response.signature)
        }).then(function (res) {
          if (res.d && res.d.ok) { location.reload(); return; }
          stepErr((res.d && res.d.error) || "That passkey didn't check out.");
        });
      })
      .catch(function () { stepErr('Passkey cancelled or failed \u2014 use a code.'); });
  }

  // Capture-phase takeover of the sign-in form ONLY.
  window.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.id !== 'authSigninForm') return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var errEl = $('authSigninError');
    if (errEl) { errEl.textContent = ''; errEl.hidden = true; }
    var btn = $('authSigninBtn');
    if (btn) btn.disabled = true;
    post('/auth/login', {
      email: $('authSigninEmail').value,
      password: $('authSigninPassword').value,
      remember: $('authRemember').checked
    }).then(function (res) {
      if (btn) btn.disabled = false;
      if (res.d && res.d.two_factor_required) {
        challenge = res.d.challenge;
        showStep();
        return;
      }
      if (res.d && res.d.ok) { location.reload(); return; }
      if (errEl) {
        errEl.textContent = (res.d && res.d.error)
          || "That didn't work \u2014 check your details and try again.";
        errEl.hidden = false;
      }
    }).catch(function () {
      if (btn) btn.disabled = false;
      if (errEl) {
        errEl.textContent = 'Connection hiccup \u2014 try that again.';
        errEl.hidden = false;
      }
    });
  }, true);

  // ------------------------------------------------ account security section
  var sec = null, statusEl = null, boxEl = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }
  function btn(text, fn) {
    var b = el('button', 'og2fa-btn', text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  }
  function msg(text, isErr) {
    boxEl.innerHTML = '';
    var p = el('p', isErr ? 'og2fa-err' : '', text);
    boxEl.appendChild(p);
  }

  function refreshStatus() {
    if (!sec) return;
    fetch('/auth/2fa/status').then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.methods) { sec.hidden = true; return; }
        sec.hidden = false;
        var m = d.methods;
        statusEl.textContent = '2-step: ' + (d.enabled ? 'On' : 'Off')
          + ' \u00b7 Code app: ' + (m.totp ? 'On' : 'Off')
          + ' \u00b7 Passkey: ' + (m.passkey === 'unavailable'
            ? 'unavailable on this server' : (m.passkey ? 'On' : 'Off'))
          + ' \u00b7 Push approval: comes in a later round';
        buildActions(d);
      }).catch(function () { /* signed out — section stays hidden */ });
  }

  function buildActions(d) {
    boxEl.innerHTML = '';
    if (!d.methods.totp) {
      boxEl.appendChild(btn('Set up code app', startSetup));
    } else {
      boxEl.appendChild(btn('Turn 2-step off', startDisable));
    }
    if (d.methods.passkey !== 'unavailable'
        && window.PublicKeyCredential && navigator.credentials) {
      boxEl.appendChild(btn('Add a passkey', addPasskey));
    }
  }

  function startSetup() {
    post('/auth/2fa/setup', {}).then(function (res) {
      if (!res.d || !res.d.ok) {
        msg((res.d && res.d.error) || "That didn't work \u2014 try again.", true);
        return;
      }
      boxEl.innerHTML = '';
      boxEl.appendChild(el('p', '', 'Put this secret in your authenticator app:'));
      var c = el('code', '', res.d.secret);
      boxEl.appendChild(c);
      boxEl.appendChild(el('p', '', 'Or paste this link into the app:'));
      boxEl.appendChild(el('code', '', res.d.otpauth_uri));
      var inp = document.createElement('input');
      inp.type = 'text'; inp.inputMode = 'numeric';
      inp.placeholder = 'Code from the app';
      boxEl.appendChild(inp);
      boxEl.appendChild(btn('Turn 2-step on', function () {
        post('/auth/2fa/enable', { code: inp.value }).then(function (r2) {
          if (!r2.d || !r2.d.ok) {
            msg((r2.d && r2.d.error) || "That code didn't match.", true);
            return;
          }
          boxEl.innerHTML = '';
          boxEl.appendChild(el('p', '',
            '2-step is ON. Save these recovery codes \u2014 each works once, '
            + 'and they are shown only this once:'));
          var pre = el('div', 'og2fa-codes',
            (r2.d.recovery_codes || []).join('\n'));
          boxEl.appendChild(pre);
          boxEl.appendChild(btn('Copy codes', function () {
            var text = (r2.d.recovery_codes || []).join('\n');
            if (navigator.clipboard) navigator.clipboard.writeText(text);
          }));
          refreshStatusSoft();
        });
      }));
    });
  }

  function startDisable() {
    boxEl.innerHTML = '';
    var pw = document.createElement('input');
    pw.type = 'password'; pw.placeholder = 'Your password';
    pw.autocomplete = 'current-password';
    var code = document.createElement('input');
    code.type = 'text'; code.inputMode = 'numeric';
    code.placeholder = 'Code app code, or a recovery code';
    boxEl.appendChild(pw);
    boxEl.appendChild(code);
    boxEl.appendChild(btn('Turn 2-step off', function () {
      post('/auth/2fa/disable', { password: pw.value, code: code.value })
        .then(function (res) {
          if (res.d && res.d.ok) {
            msg('2-step is off.');
            refreshStatusSoft();
          } else {
            msg((res.d && res.d.error) || "That didn't work \u2014 try again.", true);
          }
        });
    }));
  }

  function addPasskey() {
    post('/auth/2fa/passkey/register/options', {}).then(function (res) {
      if (!res.d || !res.d.ok) {
        msg((res.d && res.d.error) || 'Passkeys are unavailable here.', true);
        return null;
      }
      var o = res.d.options;
      return navigator.credentials.create({
        publicKey: {
          challenge: b64uToBuf(o.challenge),
          rp: o.rp,
          user: {
            id: b64uToBuf(o.user.id),
            name: o.user.name,
            displayName: o.user.displayName
          },
          pubKeyCredParams: o.pubKeyCredParams,
          attestation: o.attestation,
          authenticatorSelection: o.authenticatorSelection,
          excludeCredentials: (o.excludeCredentials || []).map(function (c) {
            return { type: 'public-key', id: b64uToBuf(c.id) };
          })
        }
      });
    }).then(function (cred) {
      if (!cred) return;
      return post('/auth/2fa/passkey/register/verify', {
        credential_id: bufToB64u(cred.rawId),
        attestation_object: bufToB64u(cred.response.attestationObject),
        client_data_json: bufToB64u(cred.response.clientDataJSON)
      }).then(function (res) {
        if (res.d && res.d.ok) {
          msg('Passkey added.');
          refreshStatusSoft();
        } else {
          msg((res.d && res.d.error) || "That passkey didn't check out.", true);
        }
      });
    }).catch(function () { msg('Passkey setup was cancelled.', true); });
  }

  var softTimer = null;
  function refreshStatusSoft() {
    if (softTimer) clearTimeout(softTimer);
    softTimer = setTimeout(refreshStatus, 400);
  }

  function buildSection() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    sec = document.createElement('div');
    var h = el('h3', 'og-menu-sec', 'Account security');
    h.id = 'og2faSecHead';
    var nav = el('nav', 'og-menu-card', '');
    var inner = el('div', 'og2fa-sec', '');
    statusEl = el('p', '', '2-step: \u2026');
    boxEl = el('div', '', '');
    inner.appendChild(statusEl);
    inner.appendChild(boxEl);
    nav.appendChild(inner);
    card.parentNode.insertBefore(h, card.nextSibling);
    card.parentNode.insertBefore(nav, h.nextSibling);
    sec = nav;
    sec.hidden = true;
    h.hidden = true;
    sec._head = h;
    refreshStatus();
    return true;
  }

  // Show/hide with the Account section + keep status fresh.
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    if (buildSection()) {
      var acctSec = $('menuAccountSec');
      var signedIn = acctSec && !acctSec.hidden;
      sec.hidden = !signedIn;
      if (sec._head) sec._head.hidden = !signedIn;
      if (signedIn && tries % 4 === 0) refreshStatus();
    }
    if (tries > 600) clearInterval(iv);
  }, 1500);
})();

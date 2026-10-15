/* Round 108: REBOOT OG (Brent, 2026-10-12: "Add a re
 * boot for og in the menu under tools" + the server
 * version, "Yes add it for me").
 *
 * A menu row labeled exactly "Reboot OG" rides at the
 * end of the menu's Tools card. It opens a confirm
 * card — OG's standing pattern, never a fat-finger
 * action:
 *
 *   "Reboot OG?" — Reboot / Cancel. Confirming POSTs
 *   /reboot: the server ends this account's live
 *   browser session cleanly and clears OG's cached
 *   working state for the account, then answers a
 *   report (crew work still running is reported,
 *   never killed). The page then hard-reloads fresh.
 *   The reload happens even if the server is too stuck
 *   to answer in time — the result line says which way
 *   it went; if the server cannot be reached at all,
 *   the card says so honestly and still offers the
 *   reload. A reboot is NOT a reset — the card's copy
 *   says so: memory, history, locker, picks and
 *   settings all survive.
 *
 *   OWNER ONLY (the card renders this section only
 *   when GET /reboot/status answers owner: true — the
 *   server re-checks on the endpoint regardless):
 *   "Restart OG's server", behind its own stronger
 *   confirm ("This knocks OG offline for EVERYONE for
 *   about 2 minutes"). POST /reboot/server fires the
 *   restart server-side; the card then polls /health
 *   until OG answers and reloads fresh. When the door
 *   isn't wired the section says "Server restart isn't
 *   wired yet" — never a dead button; when the kill
 *   switch is off it says that instead; inside the
 *   10-minute rate window it names the wait.
 *
 * Loaded via the og_tokens.js ride-along loader like
 * Rounds 62/63/65/66/84/85/90/97/98/106 — the page is
 * at its push ceiling. Defensive by contract:
 * feature-detected, try/caught, never throws. */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function api(path, opts) {
    // credentials: the /reboot routes are auth-gated —
    // every call carries the session cookie explicitly.
    var o = opts || {};
    if (!o.credentials) o.credentials = 'same-origin';
    return fetch(path, o).then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, status: r.status, d: d };
      }, function () {
        return { ok: r.ok, status: r.status, d: null };
      });
    });
  }

  var css = ''
    + '.og108-scrim{position:fixed;inset:0;background:rgba(0,0,0,.62);'
    + 'z-index:9400}'
    + '.og108-card{position:fixed;left:50%;top:50%;'
    + 'transform:translate(-50%,-50%);width:min(92vw,430px);'
    + 'max-height:88dvh;overflow-y:auto;z-index:9401;'
    + 'border:2px solid #ffc107;border-radius:16px;'
    + 'background:linear-gradient(180deg,#1d1703,#121212);'
    + 'color:#f2f2f2;padding:18px;box-shadow:0 18px 50px rgba(0,0,0,.6);'
    + 'font-family:inherit}'
    + '.og108-card[hidden],.og108-scrim[hidden]{display:none!important}'
    + '.og108-top{display:flex;align-items:flex-start;'
    + 'justify-content:space-between;gap:10px}'
    + '.og108-title{font-weight:800;color:#ffc107;font-size:1.12rem;'
    + 'margin:2px 0 0}'
    + '.og108-x{flex:0 0 auto}'
    + '.og108-copy{margin:10px 0 0;font-size:.94rem;line-height:1.5}'
    + '.og108-note{margin:8px 0 0;color:#bbb;font-size:.84rem;'
    + 'line-height:1.45}'
    + '.og108-line{margin:12px 0 0;font-size:.9rem;color:#e8c95a;'
    + 'min-height:1.25em;line-height:1.45;white-space:pre-wrap}'
    + '.og108-btns{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}'
    + '.og108-btns button{border-radius:999px;padding:9px 20px;'
    + 'font-size:.92rem;font-weight:700;cursor:pointer;'
    + 'font-family:inherit}'
    + '.og108-go{border:1px solid #ffc107;background:#ffc107;'
    + 'color:#151515}'
    + '.og108-no{border:1px solid #444;background:transparent;'
    + 'color:#ddd}'
    + '.og108-danger{border:1px solid rgba(255,120,120,.75);'
    + 'background:rgba(255,80,80,.12);color:#ff9c9c}'
    + '.og108-btns button:disabled{opacity:.5;cursor:default}'
    + '.og108-owner{margin-top:16px;border-top:1px solid #333;'
    + 'padding-top:12px}'
    + '.og108-owner-h{font-size:.8rem;font-weight:700;'
    + 'letter-spacing:.06em;color:#999;margin:0}';
  try {
    var st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
  } catch (e) { /* styles are an enhancement; the card still works */ }

  var REFRESH = '<svg viewBox="0 0 24 24" fill="none" '
    + 'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    + 'stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/>'
    + '<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';

  /* ---------------- the card ---------------- */
  var card = null, scrim = null;
  var mainView = null, confirmView = null, waitView = null;
  var lineEl = null, ownerBox = null, rebootBtn = null;
  var restartBtn = null, reloadBtn = null;
  var forgetBtn = null, forgetArmed = false;
  var status = null;          // last /reboot/status answer
  var busy = false;

  function show(view) {
    mainView.hidden = view !== 'main';
    confirmView.hidden = view !== 'confirm';
    waitView.hidden = view !== 'wait';
  }
  function openCard() {
    if (!card) buildCard();
    card.hidden = false;
    scrim.hidden = false;
    show('main');
    paintLine('');
    if (reloadBtn) reloadBtn.hidden = true;
    if (rebootBtn) rebootBtn.disabled = false;
    forgetArmed = false;
    if (forgetBtn) {
      forgetBtn.disabled = false;
      forgetBtn.textContent = 'Reboot + forget saved logins';
    }
    refreshStatus();
  }
  function closeCard() {
    if (!card) return;
    card.hidden = true;
    scrim.hidden = true;
  }
  function paintLine(t) { if (lineEl) lineEl.textContent = t || ''; }

  function reloadFresh() {
    // Best-effort cache refresh, then the reload itself —
    // the reload is the point and never waits on the fetch.
    try {
      var p = fetch(window.location.href, { cache: 'reload' });
      if (p && p.catch) p.catch(function () {});
    } catch (e) { /* the reload below is the real action */ }
    window.location.reload();
  }

  function paintOwner() {
    if (!ownerBox) return;
    ownerBox.innerHTML = '';
    if (!status || !status.owner) { ownerBox.hidden = true; return; }
    ownerBox.hidden = false;
    ownerBox.appendChild(el('p', 'og108-owner-h', 'FOR YOU ONLY'));
    var sr = status.server_restart || {};
    if (!sr.wired) {
      ownerBox.appendChild(el('p', 'og108-note',
        "Server restart isn't wired yet."));
      return;
    }
    if (!sr.enabled) {
      ownerBox.appendChild(el('p', 'og108-note',
        'Server restart is switched off right now.'));
      return;
    }
    restartBtn = el('button', 'og108-danger',
      "Restart OG's server");
    restartBtn.type = 'button';
    if (sr.cooldown_s > 0) {
      restartBtn.disabled = true;
      ownerBox.appendChild(restartBtn);
      var mins = Math.max(1, Math.ceil(sr.cooldown_s / 60));
      ownerBox.appendChild(el('p', 'og108-note',
        "OG's server was just restarted — you can restart "
        + 'again in about ' + mins + ' minute'
        + (mins === 1 ? '' : 's') + '.'));
      return;
    }
    restartBtn.addEventListener('click', function () {
      paintLine('');
      show('confirm');
    });
    ownerBox.appendChild(restartBtn);
    ownerBox.appendChild(el('p', 'og108-note',
      'Restarts OG\u2019s whole server — everyone, not '
      + 'just your account.'));
  }

  function refreshStatus() {
    api('/reboot/status').then(function (res) {
      if (res.ok && res.d) { status = res.d; paintOwner(); }
    }).catch(function () { /* the card still works; POSTs answer honestly */ });
  }

  /* ---------------- the soft reboot ---------------- */
  function runReboot() {
    if (busy) return;
    busy = true;
    if (rebootBtn) rebootBtn.disabled = true;
    if (restartBtn) restartBtn.disabled = true;
    paintLine('Rebooting…');
    var ctl = null, timedOut = false;
    var opts = { method: 'POST',
                 headers: { 'Content-Type': 'application/json' },
                 body: '{}' };
    try {
      ctl = new AbortController();
      opts.signal = ctl.signal;
      setTimeout(function () {
        timedOut = true;
        try { ctl.abort(); } catch (e) {}
      }, 6000);
    } catch (e) { /* no abort support: the fetch answers when it answers */ }
    api('/reboot', opts).then(function (res) {
      busy = false;
      if (res.ok && res.d && res.d.ok) {
        var parts = ['OG rebooted.'];
        if (res.d.browser_ended) {
          parts.push('His browser session ended.');
        }
        if (res.d.crew_running > 0) {
          parts.push(res.d.crew_running + ' crew job'
            + (res.d.crew_running === 1 ? ' is' : 's are')
            + ' still running — left alone, not killed.');
        }
        paintLine(parts.join(' ') + ' Reloading fresh…');
        setTimeout(reloadFresh, 1400);
      } else {
        if (rebootBtn) rebootBtn.disabled = false;
        paintLine((res.d && res.d.error)
          || 'OG\u2019s server answered, but the reboot '
          + 'didn\u2019t go through — try again in a moment.');
      }
    }).catch(function () {
      busy = false;
      if (timedOut) {
        // Too stuck to answer — the reload happens anyway,
        // and the line says which way it went.
        paintLine('OG\u2019s server is too stuck to answer '
          + '— reloading fresh anyway.');
        setTimeout(reloadFresh, 1400);
      } else {
        if (rebootBtn) rebootBtn.disabled = false;
        paintLine('OG\u2019s server didn\u2019t answer — '
          + 'a reboot from here can\u2019t reach him.');
        if (reloadBtn) reloadBtn.hidden = false;
      }
    });
  }

  /* ----------- reboot + forget saved logins (R321) ----------- */
  function runForget() {
    if (busy) return;
    if (!forgetArmed) {
      forgetArmed = true;
      if (forgetBtn) {
        forgetBtn.textContent = 'Sure? Forget logins + reboot';
      }
      paintLine('This deletes your saved browser logins '
        + 'and stops every watch — you log in fresh next '
        + 'time. Tap the button again to do it.');
      return;
    }
    busy = true;
    forgetArmed = false;
    if (forgetBtn) forgetBtn.disabled = true;
    if (rebootBtn) rebootBtn.disabled = true;
    if (restartBtn) restartBtn.disabled = true;
    paintLine('Rebooting + forgetting…');
    api('/reboot/forget', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}' }).then(function (res) {
      busy = false;
      if (res.ok && res.d && res.d.ok) {
        var parts = ['OG rebooted.'];
        if (res.d.browser_ended) {
          parts.push('His browser session ended.');
        }
        if (res.d.forget_body) parts.push(res.d.forget_body);
        else if (res.d.logins_forgotten) {
          parts.push('Saved logins forgotten.');
        }
        paintLine(parts.join(' ') + ' Reloading fresh…');
        setTimeout(reloadFresh, 1600);
      } else {
        if (forgetBtn) {
          forgetBtn.disabled = false;
          forgetBtn.textContent = 'Reboot + forget saved logins';
        }
        if (rebootBtn) rebootBtn.disabled = false;
        paintLine((res.d && res.d.error)
          || 'OG\u2019s server answered, but it didn\u2019t '
          + 'go through — try again in a moment.');
      }
    }).catch(function () {
      busy = false;
      if (forgetBtn) {
        forgetBtn.disabled = false;
        forgetBtn.textContent = 'Reboot + forget saved logins';
      }
      if (rebootBtn) rebootBtn.disabled = false;
      paintLine('OG\u2019s server didn\u2019t answer — '
        + 'nothing was forgotten.');
    });
  }

  /* ---------------- the server restart ---------------- */
  function runRestart() {
    if (busy) return;
    busy = true;
    var btns = confirmView.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) btns[i].disabled = true;
    api('/reboot/server', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}' }).then(function (res) {
      busy = false;
      for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
      if (res.ok && res.d && res.d.restarting) {
        show('wait');
        pollHealth(0);
      } else {
        show('main');
        paintLine((res.d && res.d.error)
          || 'That didn\u2019t go through — try again in a moment.');
        refreshStatus();
      }
    }).catch(function () {
      busy = false;
      for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
      show('main');
      paintLine('OG\u2019s server didn\u2019t answer — the '
        + 'restart can\u2019t reach him from here.');
    });
  }

  function pollHealth(tries) {
    var note = waitView.querySelector('.og108-line');
    if (tries >= 60) {
      if (note) note.textContent = 'OG is taking longer '
        + 'than usual to come back. Give it a moment, '
        + 'then reload.';
      var rb = waitView.querySelector('.og108-reload');
      if (rb) rb.hidden = false;
      return;
    }
    fetch('/health', { cache: 'no-store' }).then(function (r) {
      if (r.ok) {
        if (note) note.textContent = 'OG\u2019s back — '
          + 'reloading fresh.';
        setTimeout(reloadFresh, 800);
      } else {
        setTimeout(function () { pollHealth(tries + 1); }, 5000);
      }
    }).catch(function () {
      setTimeout(function () { pollHealth(tries + 1); }, 5000);
    });
  }

  function buildCard() {
    scrim = el('div', 'og108-scrim', '');
    scrim.id = 'ogRebootScrim';
    scrim.hidden = true;
    scrim.addEventListener('click', closeCard);
    document.body.appendChild(scrim);

    card = el('section', 'og108-card', '');
    card.id = 'ogRebootCard';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-label', 'Reboot OG');
    card.hidden = true;
    document.body.appendChild(card);

    /* main view — the confirm card itself */
    mainView = el('div', '', '');
    var top = el('div', 'og108-top', '');
    top.appendChild(el('h2', 'og108-title', 'Reboot OG?'));
    var x = el('button', 'og-circle-btn og108-x', '');
    x.type = 'button';
    x.setAttribute('aria-label', 'Close');
    x.innerHTML = '<svg viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round">'
      + '<line x1="6" y1="6" x2="18" y2="18"/>'
      + '<line x1="18" y1="6" x2="6" y2="18"/></svg>';
    x.addEventListener('click', closeCard);
    top.appendChild(x);
    mainView.appendChild(top);
    mainView.appendChild(el('p', 'og108-copy',
      'Your stuff stays. This just gives OG a fresh start.'));
    mainView.appendChild(el('p', 'og108-note',
      'Ends OG\u2019s browser session if one is live and '
      + 'clears his working caches for your account. Your '
      + 'memory, history, locker and settings are not '
      + 'touched — a reboot is not a reset.'));
    lineEl = el('p', 'og108-line', '');
    lineEl.setAttribute('aria-live', 'polite');
    mainView.appendChild(lineEl);
    var btns = el('div', 'og108-btns', '');
    rebootBtn = el('button', 'og108-go', 'Reboot');
    rebootBtn.type = 'button';
    rebootBtn.addEventListener('click', runReboot);
    btns.appendChild(rebootBtn);
    var cancel = el('button', 'og108-no', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', closeCard);
    btns.appendChild(cancel);
    reloadBtn = el('button', 'og108-no', 'Reload anyway');
    reloadBtn.type = 'button';
    reloadBtn.hidden = true;
    reloadBtn.addEventListener('click', reloadFresh);
    btns.appendChild(reloadBtn);
    mainView.appendChild(btns);
    /* Round 321: the second door — clearly separate from
       the plain reboot, because the plain reboot cannot
       reach saved logins (that's how a stuck log-in
       survived it). Its own two-tap confirm: the first
       tap arms it and spells out exactly what forgetting
       means; the second fires POST /reboot/forget. */
    mainView.appendChild(el('p', 'og108-note',
      'Stuck on a saved log-in a plain reboot can\'t '
      + 'reach? The door below reboots AND forgets every '
      + 'saved browser log-in (and stops your watches) — '
      + 'you log in fresh next time.'));
    var fbtns = el('div', 'og108-btns', '');
    forgetBtn = el('button', 'og108-no',
      'Reboot + forget saved logins');
    forgetBtn.type = 'button';
    forgetBtn.addEventListener('click', runForget);
    fbtns.appendChild(forgetBtn);
    mainView.appendChild(fbtns);
    ownerBox = el('div', 'og108-owner', '');
    ownerBox.hidden = true;
    mainView.appendChild(ownerBox);
    card.appendChild(mainView);

    /* restart confirm view — its own, stronger copy */
    confirmView = el('div', '', '');
    confirmView.hidden = true;
    confirmView.appendChild(el('h2', 'og108-title',
      'Restart OG\u2019s server?'));
    confirmView.appendChild(el('p', 'og108-copy',
      'This knocks OG offline for EVERYONE for about '
      + '2 minutes.'));
    var cbtns = el('div', 'og108-btns', '');
    var go = el('button', 'og108-danger', 'Restart Server');
    go.type = 'button';
    go.addEventListener('click', runRestart);
    cbtns.appendChild(go);
    var back = el('button', 'og108-no', 'Cancel');
    back.type = 'button';
    back.addEventListener('click', function () { show('main'); });
    cbtns.appendChild(back);
    confirmView.appendChild(cbtns);
    card.appendChild(confirmView);

    /* waiting view — polling /health */
    waitView = el('div', '', '');
    waitView.hidden = true;
    waitView.appendChild(el('h2', 'og108-title',
      'Server restarting'));
    waitView.appendChild(el('p', 'og108-copy',
      'OG will be back in about 2 minutes.'));
    var wline = el('p', 'og108-line', 'Waiting for OG…');
    waitView.appendChild(wline);
    var wbtns = el('div', 'og108-btns', '');
    var wrb = el('button', 'og108-no og108-reload', 'Reload');
    wrb.type = 'button';
    wrb.hidden = true;
    wrb.addEventListener('click', reloadFresh);
    wbtns.appendChild(wrb);
    waitView.appendChild(wbtns);
    card.appendChild(waitView);
  }

  /* ---------------- the menu row ---------------- */
  var row = null;
  function buildRow() {
    if (row) return true;
    var upload = $('menuUploadBtn');
    if (!upload) return false;
    var cardNav = upload.closest ? upload.closest('nav')
      : upload.parentNode;
    if (!cardNav) return false;
    row = el('button', 'og-menu-item', '');
    row.type = 'button';
    row.id = 'og108RebootRow';
    var ico = el('span', 'og-mi', '');
    ico.innerHTML = REFRESH;
    row.appendChild(ico);
    row.appendChild(el('span', '', 'Reboot OG'));
    row.addEventListener('click', function () {
      try {
        if (typeof closeMenu === 'function') closeMenu();
      } catch (e) { /* the card opens either way */ }
      openCard();
    });
    // Last in the Tools card, after the Location row —
    // the location note paragraph belongs to that row,
    // so the row goes after it, at the card's end.
    cardNav.appendChild(row);
    return true;
  }

  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    var built = false;
    try { built = buildRow(); } catch (e) { /* retry next tick */ }
    if (built || tries > 300) clearInterval(iv);
  }, 200);
})();

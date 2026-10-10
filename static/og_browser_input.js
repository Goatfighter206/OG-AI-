/* OG — Round 43: "Take control you can actually use."
   Visitor-takeover input for the WEB browser panel, built for
   the STILL view (the live player keeps working as before):
   - While the visitor drives, the panel takes the WHOLE screen
     (100dvw x 100dvh, reparented to <body> so no page chrome or
     transformed ancestor can shrink it), slim bar on top.
   - Two small TOGGLE icons in that bar: ⌨️ keyboard, 👆 tap.
     Tap one to activate it, tap again to deactivate; the icon
     shows its state (gold = on). Nothing else appears until its
     icon is tapped.
   - ⌨️ on → a real text field on OUR page (the phone keyboard
     actually opens) whose text is typed into the page's focused
     field via POST /browser/input, plus Enter / Backspace.
   - 👆 on → taps on the still become real page clicks, and
     press-and-hold drags become real mouse drags, at
     coordinates mapped through the still's natural size and
     its object-fit:contain letterboxing.
   The server accepts input only from the session owner and
   only while the visitor holds control. */
(function () {
  function $(id) { return document.getElementById(id); }
  var panel = $('browserPanel'), actions = $('browserActions'),
      shot = $('browserShot'), pillTop = $('ogTaskPillTop'),
      takeBtn = $('browserTakeBtn'), handBtn = $('browserHandBtn');
  if (!panel || !actions || !shot) return;
  var API = (typeof API_URL !== 'undefined' && API_URL)
    ? API_URL : window.location.origin;

  /* --- the two toggle icons (inserted before Hand back, so the
     full-screen bar reads: End task · ⌨️ · 👆 ····· Hand back) --- */
  var kbBtn = document.createElement('button');
  kbBtn.type = 'button'; kbBtn.id = 'ogKbBtn';
  kbBtn.className = 'og-icon-btn'; kbBtn.textContent = '⌨️';
  kbBtn.title = 'Keyboard'; kbBtn.setAttribute('aria-label', 'Keyboard');
  kbBtn.setAttribute('aria-pressed', 'false'); kbBtn.hidden = true;
  var ptrBtn = document.createElement('button');
  ptrBtn.type = 'button'; ptrBtn.id = 'ogPtrBtn';
  ptrBtn.className = 'og-icon-btn'; ptrBtn.textContent = '👆';
  ptrBtn.title = 'Tap & drag'; ptrBtn.setAttribute('aria-label', 'Tap and drag');
  ptrBtn.setAttribute('aria-pressed', 'false'); ptrBtn.hidden = true;
  actions.insertBefore(kbBtn, handBtn);
  actions.insertBefore(ptrBtn, handBtn);

  /* --- the keyboard strip: appears ONLY while ⌨️ is on --- */
  var strip = document.createElement('div');
  strip.id = 'ogKbStrip'; strip.hidden = true;
  strip.innerHTML =
    '<input id="ogKbText" type="text" autocomplete="off" ' +
    'autocapitalize="off" autocorrect="off" spellcheck="false" ' +
    'placeholder="Type here — it goes into the page">' +
    '<button type="button" id="ogKbSend">Type it</button>' +
    '<button type="button" id="ogKbEnter">↵ Enter</button>' +
    '<button type="button" id="ogKbBack">⌫</button>';
  panel.insertBefore(strip, actions);
  var msg = document.createElement('p');
  msg.id = 'ogInputMsg'; msg.hidden = true;
  panel.insertBefore(msg, actions);
  var kbText = $('ogKbText');

  var driving = false, kbOn = false, ptrOn = false;
  var fullApplied = false, placeholder = null, savedStyle = null;
  var pillHome = null;

  function say(text) {
    msg.textContent = text || '';
    msg.hidden = !text;
  }

  /* --- full-screen takeover (driving state only) --- */
  function applyFull() {
    if (fullApplied) return;
    placeholder = document.createComment('og-r43-panel-home');
    panel.parentNode.insertBefore(placeholder, panel);
    savedStyle = panel.getAttribute('style');
    document.body.appendChild(panel);
    panel.classList.add('og-r43-full');
    panel.style.position = 'fixed';
    panel.style.inset = '0';
    panel.style.width = '100dvw';
    panel.style.height = '100dvh';
    panel.style.maxWidth = 'none';
    panel.style.minWidth = '0';
    panel.style.margin = '0';
    panel.style.zIndex = '2147483000';
    document.body.classList.add('og-r43-lock');
    /* The ONE task bubble (same element, same updater — it is
       MOVED, never duplicated) rides into the panel at top
       center, so "Opening <host>…" / "Browsing <host>" reads
       identically in the full-screen state too. */
    if (pillTop && pillTop.parentNode) {
      pillHome = { parent: pillTop.parentNode, next: pillTop.nextSibling };
      panel.appendChild(pillTop);
      pillTop.classList.add('og-r43-pill');
    }
    fullApplied = true;
  }
  function removeFull() {
    if (!fullApplied) return;
    if (pillTop && pillHome) {
      pillTop.classList.remove('og-r43-pill');
      if (pillHome.next && pillHome.next.parentNode === pillHome.parent) {
        pillHome.parent.insertBefore(pillTop, pillHome.next);
      } else {
        pillHome.parent.appendChild(pillTop);
      }
      pillHome = null;
    }
    panel.classList.remove('og-r43-full');
    if (savedStyle) panel.setAttribute('style', savedStyle);
    else panel.removeAttribute('style');
    document.body.classList.remove('og-r43-lock');
    if (placeholder && placeholder.parentNode) {
      placeholder.parentNode.replaceChild(panel, placeholder);
    }
    placeholder = null; savedStyle = null;
    fullApplied = false;
  }

  /* --- toggles --- */
  function setKb(on) {
    kbOn = on;
    kbBtn.classList.toggle('og-on', on);
    kbBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    strip.hidden = !on;
    if (on) { try { kbText.focus(); } catch (e) {} }
    else say('');
  }
  function setPtr(on) {
    ptrOn = on;
    ptrBtn.classList.toggle('og-on', on);
    ptrBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    shot.classList.toggle('og-ptr-on', on);
    if (on) say('👆 Tap the picture to click. Hold and drag to drag.');
    else say('');
  }
  kbBtn.addEventListener('click', function () { setKb(!kbOn); });
  ptrBtn.addEventListener('click', function () { setPtr(!ptrOn); });

  /* --- input plumbing --- */
  function refreshShot(delay) {
    window.setTimeout(function () {
      if (!shot.hidden) {
        shot.src = API + '/browser/screenshot?ts=' + Date.now();
      }
    }, delay || 0);
  }
  function sendInput(payload, shotDelay) {
    return fetch(API + '/browser/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return {}; });
    }).then(function (res) {
      if (res && res.ok) { say(''); refreshShot(shotDelay); }
      else say((res && res.error) || "That didn't go through.");
      return res;
    }).catch(function () {
      say('Connection hiccup — try again.');
      return { ok: false };
    });
  }

  /* --- keyboard strip actions --- */
  function typeFromField() {
    var text = kbText.value;
    if (!text) { say('Type something first.'); return; }
    sendInput({ kind: 'type', text: text }, 300).then(function (res) {
      if (res && res.ok) {
        kbText.value = '';
        try { kbText.focus(); } catch (e) {}
      }
    });
  }
  $('ogKbSend').addEventListener('click', typeFromField);
  kbText.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); typeFromField(); }
  });
  $('ogKbEnter').addEventListener('click', function () {
    sendInput({ kind: 'key', key: 'enter' }, 700);
  });
  $('ogKbBack').addEventListener('click', function () {
    sendInput({ kind: 'key', key: 'backspace' }, 250);
  });

  /* --- pointer: tap = click, hold-and-move = real mouse drag ---
     Coordinates are mapped from the displayed <img> box through
     its object-fit:contain letterboxing into the still's
     NATURAL pixels; the server scales them to page CSS pixels. */
  function frame() {
    var nw = shot.naturalWidth, nh = shot.naturalHeight;
    if (!nw || !nh) return null;
    var r = shot.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var scale = Math.min(r.width / nw, r.height / nh);
    return { nw: nw, nh: nh, scale: scale,
             ox: r.left + (r.width - nw * scale) / 2,
             oy: r.top + (r.height - nh * scale) / 2,
             dw: nw * scale, dh: nh * scale };
  }
  function toNatural(cx, cy, f, clamp) {
    var x = cx - f.ox, y = cy - f.oy;
    if (clamp) {
      x = Math.max(0, Math.min(x, f.dw));
      y = Math.max(0, Math.min(y, f.dh));
    } else if (x < 0 || y < 0 || x > f.dw || y > f.dh) {
      return null; /* a tap in the letterbox bars hits nothing */
    }
    return { x: Math.round(x / f.scale), y: Math.round(y / f.scale) };
  }
  var press = null;
  shot.addEventListener('pointerdown', function (e) {
    if (!driving || !ptrOn) return;
    press = { x: e.clientX, y: e.clientY, t: Date.now(),
              samples: [{ x: e.clientX, y: e.clientY, t: Date.now() }],
              lastPush: Date.now() };
    try { shot.setPointerCapture(e.pointerId); } catch (err) {}
    e.preventDefault();
  });
  shot.addEventListener('pointermove', function (e) {
    if (!press) return;
    var now = Date.now();
    var last = press.samples[press.samples.length - 1];
    if (now - press.lastPush >= 25 &&
        (Math.abs(e.clientX - last.x) >= 3 ||
         Math.abs(e.clientY - last.y) >= 3)) {
      press.samples.push({ x: e.clientX, y: e.clientY, t: now });
      press.lastPush = now;
    }
  });
  function endPress(e, cancelled) {
    if (!press) return;
    var p = press; press = null;
    if (!driving || !ptrOn || cancelled) return;
    var f = frame(); if (!f) return;
    var dx = e.clientX - p.x, dy = e.clientY - p.y;
    var disp = Math.sqrt(dx * dx + dy * dy);
    var dur = Date.now() - p.t;
    if (disp < 10 && dur < 600) {
      var pt = toNatural(e.clientX, e.clientY, f, false);
      if (pt) {
        sendInput({ kind: 'click', x: pt.x, y: pt.y,
                    nw: f.nw, nh: f.nh }, 450);
      }
      return;
    }
    /* drag (or a plain hold): replay the finger's path as a
       mouse drag — pressed, moved with the button held,
       released — at the pace the finger moved. */
    p.samples.push({ x: e.clientX, y: e.clientY, t: Date.now() });
    var path = [];
    for (var i = 0; i < p.samples.length; i++) {
      var np = toNatural(p.samples[i].x, p.samples[i].y, f, true);
      if (np) path.push(np);
    }
    if (path.length < 2) path.push(path[0] || { x: 0, y: 0 });
    sendInput({ kind: 'drag', path: path, nw: f.nw, nh: f.nh,
                dur: dur }, 500);
  }
  shot.addEventListener('pointerup', function (e) { endPress(e, false); });
  shot.addEventListener('pointercancel', function (e) { endPress(e, true); });

  /* --- driving state: own status poll (the inline panel script
     keeps its state in a closure), plus click accelerators so
     the takeover feels instant. --- */
  function reconcile(st) {
    var d = !!(st && st.enabled && st.active &&
               st.control === 'visitor');
    driving = d;
    kbBtn.hidden = !d;
    ptrBtn.hidden = !d;
    if (d) applyFull();
    else {
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      say('');
      removeFull();
    }
  }
  function poll() {
    fetch(API + '/browser/status').then(function (r) {
      return r.json();
    }).then(reconcile).catch(function () {});
  }
  if (takeBtn) {
    takeBtn.addEventListener('click', function () {
      window.setTimeout(poll, 500);
    });
  }
  if (handBtn) {
    handBtn.addEventListener('click', function () {
      removeFull(); /* optimistic; the poll confirms the truth */
      window.setTimeout(poll, 400);
    });
  }
  poll();
  window.setInterval(poll, 2500);
})();

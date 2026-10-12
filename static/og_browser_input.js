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
  /* Round 61: a takeover the visitor has ASKED for (Take
     control tapped) whose server confirmation has not landed
     yet. The full-screen bar must stay whole on the intent
     alone — see reconcile. takeMisses bounds the fast
     re-poll; takeWarned keeps the honest line to once. */
  var takeAsked = false, takeMisses = 0, takeWarned = false;
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
    resetZoom();
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
  /* Round 61: the full state paints the still with
     object-fit:cover (see the CSS) — map through the fit the
     browser actually used: cover scales to the LARGER ratio
     and crops, contain to the smaller and letterboxes. The
     centering offsets handle both (cover's go negative).
     Round 79: the math is a pure function of the RENDERED
     rect on purpose. The zoom layer below transforms the
     <img> with a uniform scale + translate; a uniform
     transform preserves content fractions, so the same
     formula over the post-transform rect (which is what
     getBoundingClientRect returns) stays exact — the node
     suite proves the mapping under transformed rects. */
  function frameFromRect(r, nw, nh, fit) {
    var scale = fit === 'cover'
      ? Math.max(r.width / nw, r.height / nh)
      : Math.min(r.width / nw, r.height / nh);
    return { nw: nw, nh: nh, scale: scale,
             ox: r.left + (r.width - nw * scale) / 2,
             oy: r.top + (r.height - nh * scale) / 2,
             dw: nw * scale, dh: nh * scale };
  }
  function frame() {
    var nw = shot.naturalWidth, nh = shot.naturalHeight;
    if (!nw || !nh) return null;
    var r = shot.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var fit = 'contain';
    try {
      fit = window.getComputedStyle(shot).objectFit || 'contain';
    } catch (e) {}
    return frameFromRect(r, nw, nh, fit);
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

  /* --- Round 79: the still gets its OWN zoom — the page's
     pinch zoom never touches the chrome again -------------
     Brent's 2026-10-11 recording: pinching to zoom the
     still in the full state zoomed the WHOLE page (the
     browser's visual-viewport zoom) — the control bar
     scaled, panned, and clipped off-screen ("Hand b…")
     while he was trying to drive. There was no app zoom
     at all; the browser supplied it, and it drags every
     fixed element with it.
     Now, in the full (driving) state only:
     - a two-finger pinch zooms the STILL (1x–4x) and a
       one-finger drag pans it while pointer mode is off
       (a fast double-tap resets it); the transform lives
       on #browserShot ALONE and the view clips it, so the
       control bar (End task · ⌨️ · 👆 · Hand back), the
       note line, and the task pill riding above the still
       are outside the transformed subtree — pinned,
       unscaled, tappable, the whole time.
     - the browser's own page zoom is refused at the
       source: touch-action:none on the view in the full
       state (CSS) hands every touch to these pointer
       handlers, and the panel swallows WebKit's
       gesturestart/gesturechange while full, so a pinch
       that starts on the bar can't zoom the page either.
     - pointer mode (👆) keeps its exact input path: one
       finger is a click/drag for the page (the handlers
       above), and a second finger converts the gesture
       to a zoom, cancelling any in-flight press.
     Leaving the full state resets the zoom. */
  var view = $('browserView');
  var zoom = { scale: 1, tx: 0, ty: 0 };
  var zPtrs = {}, zCount = 0;
  var pinch0 = null, pan0 = null, lastTap = { t: 0, x: 0, y: 0 };
  function zoomLive() { return fullApplied && !!view; }
  function applyZoom() {
    var vw = (view && view.clientWidth) || 0,
        vh = (view && view.clientHeight) || 0;
    if (vw && vh && isFinite(vw) && isFinite(vh)) {
      /* pan clamp: the still can never be dragged so far
         that its scaled body leaves the view empty */
      var mx = vw * (zoom.scale - 1) / 2,
          my = vh * (zoom.scale - 1) / 2;
      zoom.tx = Math.max(-mx, Math.min(mx, zoom.tx));
      zoom.ty = Math.max(-my, Math.min(my, zoom.ty));
    }
    if (shot.style) {
      shot.style.transform = (zoom.scale === 1 && !zoom.tx && !zoom.ty)
        ? ''
        : 'translate(' + zoom.tx + 'px,' + zoom.ty + 'px) scale(' + zoom.scale + ')';
    }
  }
  function resetZoom() {
    zoom.scale = 1; zoom.tx = 0; zoom.ty = 0;
    pinch0 = null; pan0 = null;
    zPtrs = {}; zCount = 0;
    applyZoom();
  }
  function zPts() {
    var pts = [];
    for (var k in zPtrs) if (zPtrs.hasOwnProperty(k)) pts.push(zPtrs[k]);
    return pts;
  }
  function zDist() {
    var pts = zPts();
    if (pts.length < 2) return 0;
    var dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
    return Math.sqrt(dx * dx + dy * dy);
  }
  function zMid() {
    var pts = zPts();
    if (pts.length < 2) return pts[0] || { x: 0, y: 0 };
    return { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
  }
  if (view) {
    view.addEventListener('pointerdown', function (e) {
      if (!zoomLive()) return;
      zPtrs[e.pointerId] = { x: e.clientX, y: e.clientY };
      zCount++;
      /* Capture discipline: with pointer mode ON, the
         shot owns its pointer (the input path above) —
         never steal it. Otherwise the view captures, so
         a pan can't lose its pointer mid-drag. A second
         finger steals both captures for the pinch; the
         in-flight press is cancelled below, so the shot
         never hears a half-gesture. */
      if (zCount === 2) {
        for (var k in zPtrs) {
          if (zPtrs.hasOwnProperty(k)) {
            try { view.setPointerCapture(Number(k)); } catch (err) {}
          }
        }
        /* a second finger makes it a zoom — an in-flight
           tap/drag input is cancelled, never half-sent */
        press = null;
        var m = zMid();
        pinch0 = { d: zDist() || 1, mx: m.x, my: m.y,
                   scale: zoom.scale, tx: zoom.tx, ty: zoom.ty };
        pan0 = null;
        e.preventDefault();
      } else if (zCount === 1 && !ptrOn) {
        try { view.setPointerCapture(e.pointerId); } catch (err) {}
        pan0 = { x: e.clientX, y: e.clientY, tx: zoom.tx, ty: zoom.ty,
                 t: Date.now(), moved: false, tapOnly: zoom.scale <= 1 };
      }
    });
    view.addEventListener('pointermove', function (e) {
      if (!zoomLive() || !zPtrs[e.pointerId]) return;
      zPtrs[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (pinch0 && zCount >= 2) {
        var d = zDist();
        if (!d) return;
        var m = zMid();
        zoom.scale = Math.max(1, Math.min(4, pinch0.scale * d / pinch0.d));
        zoom.tx = pinch0.tx + (m.x - pinch0.mx);
        zoom.ty = pinch0.ty + (m.y - pinch0.my);
        applyZoom();
        e.preventDefault();
      } else if (pan0 && !pinch0) {
        var dx = e.clientX - pan0.x, dy = e.clientY - pan0.y;
        if (Math.abs(dx) + Math.abs(dy) > 6) pan0.moved = true;
        if (pan0.moved) {
          e.preventDefault();
          if (!pan0.tapOnly) {
            zoom.tx = pan0.tx + dx;
            zoom.ty = pan0.ty + dy;
            applyZoom();
          }
        }
      }
    });
    var zEnd = function (e) {
      if (!zPtrs[e.pointerId]) return;
      delete zPtrs[e.pointerId];
      if (zCount > 0) zCount--;
      if (zCount < 2) pinch0 = null;
      if (zCount === 0) {
        if (pan0 && !pan0.moved && !ptrOn && zoomLive()) {
          /* plain tap, pointer mode off: a fast second
             tap puts the still back to 1x */
          var now = Date.now();
          if (now - lastTap.t < 350 &&
              Math.abs(e.clientX - lastTap.x) < 40 &&
              Math.abs(e.clientY - lastTap.y) < 40) {
            resetZoom();
            lastTap = { t: 0, x: 0, y: 0 };
          } else {
            lastTap = { t: now, x: e.clientX, y: e.clientY };
          }
        }
        pan0 = null;
      }
    };
    view.addEventListener('pointerup', zEnd);
    view.addEventListener('pointercancel', zEnd);
    view.addEventListener('wheel', function (e) {
      if (!zoomLive()) return;
      e.preventDefault();
      var f = Math.exp(-(e.deltaY || 0) * 0.0015);
      zoom.scale = Math.max(1, Math.min(4, zoom.scale * f));
      applyZoom();
    }, { passive: false });
  }
  panel.addEventListener('gesturestart', function (e) {
    if (fullApplied) e.preventDefault();
  });
  panel.addEventListener('gesturechange', function (e) {
    if (fullApplied) e.preventDefault();
  });
  window.OG79ZOOM = {
    frameFromRect: frameFromRect,
    state: function () {
      return { scale: zoom.scale, tx: zoom.tx, ty: zoom.ty };
    },
    reset: resetZoom
  };

  /* --- driving state: own status poll (the inline panel script
     keeps its state in a closure), plus click accelerators so
     the takeover feels instant. --- */
  function reconcile(st) {
    var d = !!(st && st.enabled && st.active &&
               st.control === 'visitor');
    driving = d;
    if (!st || !st.enabled || !st.active) takeAsked = false;
    /* Round 61: the toggles follow the takeover INTENT too.
       They used to be fail-closed on the confirmed flip
       alone, so an unconfirmed take left the full-screen
       bar carrying only End task (Brent's 2026-10-10
       photo). While a take the visitor asked for is still
       unconfirmed, the buttons stay up; an input the
       server refuses is answered in words by sendInput,
       never silently — and Hand back (guaranteed by the
       CSS in the full state) always steps back out. */
    var full = panel.classList.contains('og-panel-full');
    var showToggles = d || (takeAsked && full);
    kbBtn.hidden = !showToggles;
    ptrBtn.hidden = !showToggles;
    if (d) {
      takeAsked = false;
      applyFull();
    } else {
      if (!(takeAsked && full)) {
        if (kbOn) setKb(false);
        if (ptrOn) setPtr(false);
        say('');
      } else if (takeMisses < 12) {
        /* Confirmation still owed: poll again soon instead
           of waiting out the 2.5 s interval. */
        takeMisses++;
        window.setTimeout(poll, 400);
      } else if (!takeWarned) {
        takeWarned = true;
        say("Take control didn't go through — OG is still " +
            'driving. The buttons stay here; tap Hand back ' +
            'to step out, or try Take control again.');
      }
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
      /* Round 61: record the intent BEFORE the first poll —
         it is what keeps the bar whole (see reconcile)
         while the server confirmation is in flight. */
      takeAsked = true; takeMisses = 0; takeWarned = false;
      window.setTimeout(poll, 350);
    });
  }
  if (handBtn) {
    handBtn.addEventListener('click', function () {
      takeAsked = false;
      removeFull(); /* optimistic; the poll confirms the truth */
      window.setTimeout(poll, 400);
    });
  }
  poll();
  window.setInterval(poll, 2500);
})();

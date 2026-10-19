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

/* OG — Round 321 Ticket A: THE BROWSER WINDOW REBUILD
   (Brent's confirmation drill, 2026-10-18; spec of
   2026-10-15 + the drill). What the rounds above left
   behind, and what this section fixes:
   - TWO full-screen states competed: the page's
     og-panel-full class and this file's og-r43-full
     reparent hack. The letterbox fix (object-fit:cover)
     was keyed to the page's class only, so whenever the
     input layer's own state was the one in force the
     still fell back to contain — a thin band mid-screen,
     the desktop page squeezed unreadable (drill video).
     NOW: ONE window state, owned here — the og-r43-full
     class ALONE carries the geometry (CSS does the work;
     no reparenting, no inline geometry styles), and it
     applies while the visitor watches OR drives.
   - The input layer only ever existed on the still,
     while the panel defaults to Steel's cross-origin
     live-player iframe — taps there never reach these
     handlers and typing can never focus a field (Drill
     3's dead clicks). In the window state the still IS
     the surface (the iframe is display:none by CSS), so
     the window the visitor sees is the window they can
     drive; the frame loop below keeps it fresh.
   - The window gets its own chrome: a header naming
     state + site ("Opening <host>…" / "Browsing —
     <host>" / "You're in control — <host>", minutes
     left) painted from the same /browser/status poll,
     and a bottom bar: Stop task · ⌨️ · 👆 · Fit ·
     Take control / Hand back · ✕. The bar's buttons
     press the panel's own buttons (ONE control path —
     the page still posts /browser/control and
     /browser/end; nothing here duplicates them).
   - Keyboard rule (the drill): when the phone keyboard
     opens, the window is sized to the VISUAL viewport
     (visualViewport resize/scroll → panel height/top),
     so the page view reflows ABOVE the keyboard instead
     of hiding behind it.
   - A quick swipe on the still scrolls the page (kind
     'scroll'); a slow press-and-hold stays a drag.
   - Leaving the window (✕) parks it back to the docked
     panel — the session itself ends on Stop task, on
     the page's pagehide /browser/leave beacon, or on the
     server's 20-minute idle kill; when the session ends
     underneath, the window closes itself. */
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

  /* --- Round 321 Ticket A: the window's own chrome.
     A header (state + site + minutes left, plus the ✕
     that parks the window back to the docked panel) and
     a bottom bar that carries Stop task, the ⌨️/👆
     toggles (moved here from the legacy actions row —
     same buttons, same handlers), a fit toggle, and
     Take control / Hand back. Both are ordinary panel
     children; the og-r43-full CSS lays the panel out as
     a column and orders them (header first, bar last),
     and hides the legacy actions row / Open button /
     floating ✕ while the window state is on. Outside
     the window state both are display:none (CSS), so
     the docked panel is exactly as before. --- */
  var head = document.createElement('div');
  head.id = 'ogWinHead'; head.className = 'og-win-head';
  head.innerHTML =
    '<span class="og-win-state" id="ogWinState">Browser</span>' +
    '<span class="og-win-site" id="ogWinSite"></span>' +
    '<span class="og-win-meta" id="ogWinMeta"></span>' +
    '<button type="button" id="ogWinClose" title="Close window" ' +
    'aria-label="Close window">✕</button>';
  panel.appendChild(head);
  var headState = $('ogWinState'), headSite = $('ogWinSite'),
      headMeta = $('ogWinMeta'), winCloseBtn = $('ogWinClose');
  var bar = document.createElement('div');
  bar.id = 'ogWinBar'; bar.className = 'og-win-bar';
  var stopBtn = document.createElement('button');
  stopBtn.type = 'button'; stopBtn.id = 'ogWinStop';
  stopBtn.className = 'og-win-stop'; stopBtn.textContent = 'Stop task';
  stopBtn.title = 'Stop task — end the browser session';
  stopBtn.setAttribute('aria-label', 'Stop task');
  var fitBtn = document.createElement('button');
  fitBtn.type = 'button'; fitBtn.id = 'ogWinFit';
  fitBtn.className = 'og-win-fit'; fitBtn.textContent = 'Fit: Fill';
  fitBtn.title = 'Toggle how the page fills the window';
  fitBtn.setAttribute('aria-label', 'Toggle page fit');
  var winTakeBtn = document.createElement('button');
  winTakeBtn.type = 'button'; winTakeBtn.id = 'ogWinTake';
  winTakeBtn.className = 'og-win-take';
  winTakeBtn.textContent = 'Take control';
  winTakeBtn.setAttribute('aria-label', 'Take control');
  bar.appendChild(stopBtn);
  bar.appendChild(kbBtn);   /* moves the ⌨️ toggle into the bar */
  bar.appendChild(ptrBtn);  /* moves the 👆 toggle into the bar */
  bar.appendChild(fitBtn);
  bar.appendChild(winTakeBtn);
  panel.appendChild(bar);
  var endTaskBtn = $('browserEndTaskBtn'),
      openBtn = $('browserOpenBtn'), pageCloseBtn = $('browserCloseBtn');

  var driving = false, kbOn = false, ptrOn = false;
  /* Round 61: a takeover the visitor has ASKED for (Take
     control tapped) whose server confirmation has not landed
     yet. The full-screen bar must stay whole on the intent
     alone — see reconcile. takeMisses bounds the fast
     re-poll; takeWarned keeps the honest line to once. */
  var takeAsked = false, takeMisses = 0, takeWarned = false;
  var fullApplied = false;
  var pillHome = null;
  var panelHome = null;
  /* Round 321 Ticket A: the visitor has opened the
     window (Open browser / Take control / the bar) and
     not parked it — the window state follows this (and
     a confirmed takeover), NOT the takeover alone, so
     the visitor can also WATCH full-screen while OG
     drives. winShotTimer runs the still's frame loop
     while the window is up. */
  var winWanted = false, winShotTimer = null;

  function say(text) {
    msg.textContent = text || '';
    msg.hidden = !text;
  }

  /* --- the window state (Round 321 Ticket A: ONE state,
     class-only — the og-r43-full CSS carries ALL the
     geometry; the Round 43 reparent + inline-style pin
     is retired, because a second mechanism holding half
     the state is what kept the letterbox alive). --- */
  function applyFull() {
    if (fullApplied) return;
    panel.classList.add('og-r43-full');
    document.body.classList.add('og-r43-lock');
    /* Ticket A follow-up (the invisible-bar root): the panel
       lives inside .chat-container, whose backdrop-filter
       (styles.css) makes it a stacking context — the panel's
       z-index:9000 then only competes INSIDE the chat
       subtree, and og_shell's #ogShellDock (a later body
       child) paints and hit-tests over the window's bottom
       strip, burying #ogWinBar under the composer + seat
       bar (production QA 2026-10-19: the bar rendered with
       real geometry yet every tap landed on the seat bar).
       While the window is full, the panel is therefore
       MOVED to <body> (same element, same listeners — the
       page's own reference stays valid), where its z-index
       outranks the dock and its fixed box is the viewport
       itself. removeFull puts it back in its exact home. */
    if (panel.parentNode && panel.parentNode !== document.body) {
      panelHome = { parent: panel.parentNode, next: panel.nextSibling };
      document.body.appendChild(panel);
    }
    /* The ONE task bubble (same element, same updater — it is
       MOVED, never duplicated) rides into the panel at top
       center, so "Opening <host>…" / "Browsing <host>" reads
       identically in the window state too. */
    if (pillTop && pillTop.parentNode) {
      pillHome = { parent: pillTop.parentNode, next: pillTop.nextSibling };
      panel.appendChild(pillTop);
      pillTop.classList.add('og-r43-pill');
    }
    fullApplied = true;
    fitWin();
    startWinShots();
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
    /* Ticket A follow-up: undo applyFull's reparent — the
       panel returns to its exact home slot in
       .chat-container, so the docked small-bar state is
       byte-for-byte the pre-window DOM. */
    if (panelHome && panel.parentNode === document.body) {
      if (panelHome.next && panelHome.next.parentNode === panelHome.parent) {
        panelHome.parent.insertBefore(panel, panelHome.next);
      } else {
        panelHome.parent.appendChild(panel);
      }
    }
    panelHome = null;
    stopWinShots();
    panel.classList.remove('og-r43-full');
    panel.style.height = '';
    panel.style.top = '';
    document.body.classList.remove('og-r43-lock');
    fullApplied = false;
  }

  /* --- Round 321 Ticket A: the keyboard rule. While the
     window is up, the panel is sized to the VISUAL
     viewport: when the phone keyboard opens, the visual
     viewport shrinks and the window (a flex column:
     header / page view / strip / bar) reflows into
     what's actually visible — the page stays on screen
     above the keyboard instead of hiding behind it.
     offsetTop keeps it glued to the visible region when
     the page itself is scrolled under the keyboard. --- */
  function fitWin() {
    if (!fullApplied) return;
    var vv = window.visualViewport;
    if (!vv) return;
    panel.style.height = vv.height + 'px';
    panel.style.top = vv.offsetTop + 'px';
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', fitWin);
    window.visualViewport.addEventListener('scroll', fitWin);
  }

  /* --- Round 321 Ticket A: the still's frame loop while
     the window is up — the still IS the window's surface
     (the live-player iframe is display:none in this
     state), so it must stay fresh while OG works AND
     while the visitor drives. ~1.3 s cadence, paused
     when the tab is hidden; per-input refreshes ride
     refreshShot() as before. --- */
  /* --- Round 321 Ticket A3f: THE load-aware still loader.
     Every still refresh — this loop, refreshShot() after
     an input, and the page script's loadShot() (via
     window.ogStillLoad) — goes through this ONE loader.
     Why: setting an <img>'s src ABORTS its in-flight
     load, and /browser/screenshot costs a fresh CDP
     capture (production round trips meet or beat the
     1300 ms tick), so the shipped loops reset the src
     before any load could finish — the img sat on its
     last completed (pre-typing) frame forever while the
     server logged fresh stills it had "handed out"
     (convicted on ogtest7: the served frame held the
     typed text; the displayed one never did). The
     loader starts each frame in an off-DOM Image and
     swaps the visible img's src ONLY to a URL that has
     finished loading; a refresh requested mid-load
     replaces the pending intent, never the in-flight
     load; an error keeps the current frame. --- */
  var stillBusy = false, stillQueued = false,
      stillBusyCb = null, stillQueuedCb = null;
  function stillStart(cb) {
    stillBusy = true;
    stillBusyCb = cb || null;
    var url = API + '/browser/screenshot?ts=' + Date.now();
    var img = new Image();
    img.onload = function () { stillSettle(true, url); };
    img.onerror = function () { stillSettle(false, url); };
    img.src = url;
  }
  function stillSettle(ok, url) {
    stillBusy = false;
    var cb = stillBusyCb; stillBusyCb = null;
    if (ok) shot.src = url;
    if (cb) { try { cb(ok); } catch (e) {} }
    if (stillQueued) {
      stillQueued = false;
      var qcb = stillQueuedCb; stillQueuedCb = null;
      stillStart(qcb);
    }
  }
  function stillLoad(cb) {
    if (stillBusy) {
      stillQueued = true;
      if (cb) stillQueuedCb = cb;
      return;
    }
    stillStart(cb);
  }
  window.ogStillLoad = stillLoad;
  function tickWinShot() {
    if (!fullApplied || document.hidden) return;
    if (shot.hidden) shot.hidden = false;
    stillLoad(null);
  }
  function startWinShots() {
    if (winShotTimer) return;
    tickWinShot();
    winShotTimer = window.setInterval(tickWinShot, 1300);
  }
  function stopWinShots() {
    if (winShotTimer) {
      window.clearInterval(winShotTimer);
      winShotTimer = null;
    }
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
    if (on) say('👆 Tap the picture to click. Swipe to scroll. Hold and drag to drag.');
    else say('');
  }
  kbBtn.addEventListener('click', function () { setKb(!kbOn); });
  ptrBtn.addEventListener('click', function () { setPtr(!ptrOn); });

  /* --- input plumbing --- */
  function refreshShot(delay) {
    window.setTimeout(function () {
      if (!shot.hidden) {
        /* Ticket A3f: through the load-aware loader — the
           post-input frame can no longer be aborted by
           (or abort) the loop's in-flight load. */
        stillLoad(null);
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
    if (dur < 500 && Math.abs(dy) > 60 &&
        Math.abs(dy) > 2 * Math.abs(dx)) {
      /* Round 321 Ticket A: a quick swipe is a SCROLL,
         not a drag — finger up means "read further down"
         (a positive wheel deltaY scrolls down in CDP).
         The server clamps the delta and wheels at the
         page center; a slow press-and-hold below stays
         a real mouse drag. */
      sendInput({ kind: 'scroll', dx: 0,
                  dy: Math.round(-dy * 3) }, 350);
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
  /* --- Round 321 Ticket A: the window header — state +
     site, from the SAME status payload this poll already
     fetches (page_url / loading_host / control /
     minutes_left ride /browser/status today). --- */
  function hostOf(url) {
    try { return new URL(url).hostname || ''; } catch (e) { return ''; }
  }
  function paintHead(st) {
    if (!headState) return;
    if (!st || !st.enabled || !st.active) {
      headState.textContent = 'Browser';
      if (headSite) headSite.textContent = '';
      if (headMeta) headMeta.textContent = '';
      winTakeBtn.textContent = 'Take control';
      return;
    }
    var host = hostOf(st.page_url) || st.loading_host || '';
    var t;
    if (st.phase === 'starting' || st.loading_host) {
      t = 'Opening ' + (st.loading_host || host || '…') + '…';
    } else if (st.control === 'visitor') {
      t = "You're in control";
    } else {
      t = 'Browsing';
    }
    headState.textContent = t;
    if (headSite) headSite.textContent = host ? ' — ' + host : '';
    if (headMeta) {
      headMeta.textContent = st.minutes_left
        ? st.minutes_left + ' min left' : '';
    }
    winTakeBtn.textContent = st.control === 'visitor'
      ? 'Hand back' : 'Take control';
  }

  function reconcile(st) {
    var d = !!(st && st.enabled && st.active &&
               st.control === 'visitor');
    driving = d;
    paintHead(st);
    if (!st || !st.enabled || !st.active) {
      /* The session ended underneath (Stop task, the
         20-minute idle kill, budget, page-leave): the
         window closes itself and nothing is left open —
         no state outlives the session it points at. */
      takeAsked = false;
      winWanted = false;
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      say('');
      removeFull();
      return;
    }
    /* Round 61: the toggles follow the takeover INTENT too.
       They used to be fail-closed on the confirmed flip
       alone, so an unconfirmed take left the full-screen
       bar carrying only End task (Brent's 2026-10-10
       photo). While a take the visitor asked for is still
       unconfirmed, the buttons stay up; an input the
       server refuses is answered in words by sendInput,
       never silently — and Hand back always steps back
       out. (Ticket A: the toggles live in the window's
       bottom bar now, so intent alone no longer needs
       the page's full-screen class to keep them up.) */
    var showToggles = d || takeAsked;
    kbBtn.hidden = !showToggles;
    ptrBtn.hidden = !showToggles;
    if (d) {
      takeAsked = false;
      applyFull();
    } else if (takeAsked) {
      if (winWanted) applyFull();
      if (takeMisses < 12) {
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
    } else if (winWanted) {
      /* Watching OG drive, full-screen: the input
         toggles retire (the server refuses input while
         OG drives anyway) but the window stays up. */
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      say('');
      applyFull();
    } else {
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
      /* Round 61: record the intent BEFORE the first poll —
         it is what keeps the bar whole (see reconcile)
         while the server confirmation is in flight.
         Ticket A: a takeover also opens the window. */
      takeAsked = true; takeMisses = 0; takeWarned = false;
      winWanted = true;
      window.setTimeout(poll, 350);
    });
  }
  if (handBtn) {
    handBtn.addEventListener('click', function () {
      /* Ticket A: handing back returns the DRIVING to OG;
         the window stays up so the visitor keeps watching
         (the ✕ parks the window). The poll confirms. */
      takeAsked = false;
      window.setTimeout(poll, 400);
    });
  }
  /* --- Round 321 Ticket A: window chrome wiring. Every
     button presses the panel's OWN button where one
     exists (one control path — the page still posts
     /browser/control and /browser/end). --- */
  if (openBtn) {
    openBtn.addEventListener('click', function () {
      winWanted = true;
      applyFull();
    });
  }
  if (pageCloseBtn) {
    pageCloseBtn.addEventListener('click', function () {
      winWanted = false;
      removeFull();
    });
  }
  winCloseBtn.addEventListener('click', function () {
    /* Park the window back to the docked panel. The
       SESSION is not ended here — it ends on Stop task,
       on leaving the page (the /browser/leave beacon),
       or on the server's 20-minute idle kill. */
    winWanted = false;
    takeAsked = false;
    if (kbOn) setKb(false);
    if (ptrOn) setPtr(false);
    say('');
    removeFull();
  });
  stopBtn.addEventListener('click', function () {
    winWanted = false;
    takeAsked = false;
    if (endTaskBtn) endTaskBtn.click();
    removeFull();
  });
  winTakeBtn.addEventListener('click', function () {
    if (driving) { if (handBtn) handBtn.click(); }
    else if (takeBtn) takeBtn.click();
  });
  fitBtn.addEventListener('click', function () {
    var contain = panel.classList.toggle('og-win-contain');
    fitBtn.textContent = contain ? 'Fit: Width' : 'Fit: Fill';
  });
  poll();
  window.setInterval(poll, 2500);
})();

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
  fitBtn.className = 'og-win-fit'; fitBtn.textContent = 'Fit: Width';
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
  /* Ticket A4b: the ROUTINE clear — mode changes and
     toggle-offs clear the line only when no failed
     input's word is standing (see stickyErr at the
     input plumbing). Deliberate words (a hint, a fresh
     failure, a success) still go through say(). */
  function clearSay() { if (!stickyErr) say(''); }

  /* --- the window state (Round 321 Ticket A: ONE state,
     class-only — the og-r43-full CSS carries ALL the
     geometry; the Round 43 reparent + inline-style pin
     is retired, because a second mechanism holding half
     the state is what kept the letterbox alive). --- */
  /* Ticket A4e: THE FIT STATE. The window's default is
     the WHOLE page on screen (Fit: Width — the CSS
     default is contain). Fill (cover, cropped) exists
     only as the visitor's explicit Fit-button gesture
     (fitFill), and it never outlives the state that
     asked for it: applyFull (the window opening) and a
     confirmed take-control both return the window to
     Width — Brent's drill showed the cropped state
     arriving unasked and stranding the form's right
     edge off-screen, and his requirement is that the
     full screen fits ALL of the page, not part of it. */
  var fitFill = false;
  function setFit(fill) {
    fitFill = !!fill;
    panel.classList.toggle('og-win-fill', fitFill);
    fitBtn.textContent = fitFill ? 'Fit: Fill' : 'Fit: Width';
    paintMarker();
  }
  function assertFitWidth() { if (fitFill) setFit(false); }

  function applyFull() {
    if (fullApplied) return;
    assertFitWidth();
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
    paintMarker();
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
    /* Ticket A4: the marker lives only while the window
       is up — parking the window hides it (its position
       is kept, so a quick reopen within the staleness
       window restores it). */
    paintMarker();
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
  /* --- Round 321 Ticket A4: the loader grows a WATCHDOG
     and a generation rule (the never-black fix). A3f's
     loader is strictly serial with NO timeout: one
     screenshot request that never settles — a
     pool-starved server holding the capture open, a
     stalled mobile connection that never sends FIN —
     leaves the busy flag set forever, every later
     refresh queues behind a load that will never
     finish, and the visible img keeps whatever it had:
     at window open that is NOTHING, so the view sits on
     its near-black gradient for the whole session
     (Brent's 2026-10-19 drill: black from re-entry to
     the end of the session while the header, bar and
     toggles all lived). Now each load carries a
     generation number and a 9 s watchdog: when the
     watchdog fires, the load's slot frees (a queued or
     next refresh starts at once), but the abandoned
     load is not cancelled — if it still completes
     before any NEWER frame has painted, its frame
     paints (a slow real frame beats a black screen);
     a frame never paints over a newer one, and the
     visible img's src is only ever assigned a finished
     frame — across open, Take control, Hand back,
     Fit/Fill and close/reopen the last fully-loaded
     frame stays on screen until the next one lands. --- */
  var STILL_WATCHDOG_MS = 9000;
  var stillSeq = 0, stillPaintedGen = 0, stillBusyGen = 0,
      stillQueued = false, stillQueuedCb = null,
      stillWatchdog = null;
  /* Ticket A4d: when the on-screen frame last landed, and
     whether the paused-picture note is standing (see
     tickWinShot). */
  var stillPaintedAt = 0, stalePicNote = false;
  function stillStart(cb) {
    var gen = ++stillSeq;
    stillBusyGen = gen;
    var url = API + '/browser/screenshot?ts=' + Date.now();
    var img = new Image();
    var settled = false;
    function settle(ok) {
      if (settled) return;
      settled = true;
      stillSettle(gen, ok, url, cb);
    }
    img.onload = function () { settle(true); };
    img.onerror = function () { settle(false); };
    img.src = url;
    if (stillWatchdog) window.clearTimeout(stillWatchdog);
    stillWatchdog = window.setTimeout(function () {
      stillWatchdog = null;
      if (stillBusyGen !== gen) return;
      /* The wedge-breaker: free the slot without
         touching the visible img — the abandoned load
         may still paint on a late settle (see the
         generation rule in stillSettle), and a queued
         refresh starts now instead of never. */
      stillBusyGen = 0;
      if (stillQueued) {
        stillQueued = false;
        var qcb = stillQueuedCb; stillQueuedCb = null;
        stillStart(qcb);
      }
    }, STILL_WATCHDOG_MS);
  }
  function stillSettle(gen, ok, url, cb) {
    var wasBusy = gen === stillBusyGen;
    if (wasBusy) {
      stillBusyGen = 0;
      if (stillWatchdog) {
        window.clearTimeout(stillWatchdog);
        stillWatchdog = null;
      }
    }
    if (ok && gen > stillPaintedGen) {
      stillPaintedGen = gen;
      shot.src = url;
      /* Ticket A4d: a fresh picture retires the
         paused-picture note (see tickWinShot). */
      stillPaintedAt = Date.now();
      if (stalePicNote) {
        stalePicNote = false;
        clearSay();
      }
    }
    if (cb) { try { cb(ok); } catch (e) {} }
    if (wasBusy && stillQueued) {
      stillQueued = false;
      var qcb = stillQueuedCb; stillQueuedCb = null;
      stillStart(qcb);
    }
  }
  function stillLoad(cb) {
    if (stillBusyGen) {
      stillQueued = true;
      if (cb) stillQueuedCb = cb;
      return;
    }
    stillStart(cb);
  }
  /* Ticket A4e: a fresh load NOW, even while one is in
     flight — the in-flight load is abandoned exactly
     the way the 9 s watchdog abandons it (slot freed,
     its late settle may still paint under the
     generation rule), because a type that just landed
     must become VISIBLE, not wait out a starved load
     (Brent's drill: the field stayed empty-looking for
     the whole video; ogtest15's operator had to press
     refresh by hand to see the dots). */
  function stillForce() {
    if (stillBusyGen) {
      stillBusyGen = 0;
      if (stillWatchdog) {
        window.clearTimeout(stillWatchdog);
        stillWatchdog = null;
      }
    }
    stillStart(null);
  }
  window.ogStillLoad = stillLoad;
  /* Gate/debug handle (the OG79ZOOM precedent): the
     loader's vitals, so a drill can see generations
     advance instead of guessing at a black screen. */
  window.OGSTILL = {
    state: function () {
      return { seq: stillSeq, paintedGen: stillPaintedGen,
               busyGen: stillBusyGen, queued: stillQueued,
               watchdogMs: STILL_WATCHDOG_MS };
    }
  };
  function tickWinShot() {
    if (!fullApplied || document.hidden) return;
    if (shot.hidden) shot.hidden = false;
    stillLoad(null);
    /* Ticket A4: the pointer marker's staleness rule is
       evaluated on this same tick (see the marker block
       below) — a position that goes stale fades within
       one tick of the window it was given. */
    paintMarker();
    /* Ticket A4d: never drive blind on a dead picture
       without saying so. On the ogtest14 drill the server
       instance died mid-session; the loader (by design)
       held the last frame while /browser/input kept
       dispatching against the LIVE page — which settled
       upward as it finished loading — so taps aimed at
       the frozen picture landed a field high on the real
       page, with no sign anything was wrong. While the
       visitor drives with tap mode on, a picture older
       than ~6 s now names itself paused; the note clears
       on the next fresh frame (stillSettle). Inputs are
       never refused — the visitor's hand is theirs — but
       they are never silently aimed at a corpse either. */
    if (driving && ptrOn && stillPaintedAt &&
        Date.now() - stillPaintedAt > 6000 &&
        !stickyErr && !stalePicNote) {
      stalePicNote = true;
      say('⚠ Picture paused — the live page may have ' +
          'moved on. Taps follow the picture you see; ' +
          'wait for it to refresh before tapping.');
    }
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
    else clearSay();
  }
  function setPtr(on) {
    ptrOn = on;
    ptrBtn.classList.toggle('og-on', on);
    ptrBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    shot.classList.toggle('og-ptr-on', on);
    if (on) say('👆 Tap the picture to click. Swipe to scroll. Hold and drag to drag.');
    else clearSay();
  }
  kbBtn.addEventListener('click', function () { setKb(!kbOn); });
  ptrBtn.addEventListener('click', function () { setPtr(!ptrOn); });

  /* --- input plumbing --- */
  /* Ticket A4b: a FAILED input's word is STICKY. sendInput
     used to report a refusal through say() alone, and the
     next reconcile's routine say('') (the watching branch
     runs it every poll) wiped the line within a poll
     cycle — a tap the server refused (409 while the
     control record had transiently reverted, ogtest11)
     looked exactly like a tap that landed: the marker
     paints from the local press either way, and the only
     trace of the refusal vanished before the visitor
     could read it. A failure now records itself here;
     reconcile's routine clears respect it, and only a
     SUCCESSFUL input (or the session's confirmed end)
     clears it. The typed text itself was never cleared
     on failure (typeFromField) — now the reason survives
     alongside it, so a type across a flap either lands
     or visibly fails with the text still in the bar. */
  var stickyErr = '';
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
  /* Ticket A4e: sends are SERIALIZED, a type is VISIBLE
     while it flies, and the writing spot is ANCHORED.
     Brent's drill (2026-10-20): he tapped the Email
     field, pressed Type it — and nothing he could see
     happened for the rest of the video, while the text
     sat in the box and the field stayed empty. The
     anatomy, proven in the A4e hunt: one type is ~70
     sequential CDP round trips on the server (connect,
     attach, per-character key events, the delivery
     readback), so under the frame starvation his
     session already had, a type flies for tens of
     seconds. The old sendInput fired every input the
     moment it was tapped — concurrent requests racing
     on separate server connections against one page —
     said nothing while a send was in flight, and typed
     into whatever the page happened to focus at
     dispatch time: his extra taps while waiting (the
     caret "follows the mouse", his words) moved the
     writing spot, and the send had no memory of the
     field he chose. Now:
       - one input is in flight at a time; taps and keys
         queue behind a type instead of racing it;
       - a type shows itself: the button reads "Typing…"
         and the line says so; at 20 s it says it is
         still working; at 100 s the client stops waiting
         and says plainly the answer never came (the text
         stays in the box — the server may still land it,
         and the line says that too);
       - a click that lands in a field earns an ANCHOR
         from the server (the field's identity), the type
         carries it, and the server puts the caret back
         in THAT field before typing and verifies the
         text grew THERE — the writing spot stays put. */
  var sendChain = Promise.resolve();
  var typeAnchor = null;
  /* Ticket A4g: the spot is a MACHINE, not a bare slot.
     A4e's slot was the ogtest17 step-6 failure: the
     operator tapped the Password field, the press
     landed on its eye button, the server verified no
     field and answered with no anchor — and the slot
     kept the PREVIOUS field's anchor (Email), so the
     type appended the password text to Email while the
     server's readback (which measures the anchored
     field) answered ok. The slot was also never
     cleared at all — not on hand-back, not at the
     session's end. Now:
       - SUPERSESSION: every press is stamped with a
         sequence number (tap, in sendInput) which the
         server echoes; the ack's word is final for
         that tap — an anchor arms the spot, NO anchor
         means the tap chose no field and the spot is
         REFUSED: a type/key then stops HERE, in the
         window, honestly, and is never sent, until a
         tap verifies a field again. An ack whose echo
         does not match its tap is no verdict at all —
         it cannot resurrect a superseded spot, and the
         tap it answered earns nothing: the spot
         refuses rather than guess.
       - LEGACY ACKS (no echo — a pre-A4g server) keep
         the A4e rule: an anchor arms, silence leaves
         the spot as it was, so a mixed deploy degrades
         to A4e, never worse.
       - DEATH: control leaving the visitor's hands
         (the flip below), the session's confirmed end,
         a new session key, or the page navigating
         under an armed spot (spotDeathCheck) — the
         visitor re-taps to write.
       - AMBIENT (no tap yet this session) still types
         the way Round 43 did: the anchor only ever
         improved on that flow, it never gated it. */
  /* Ticket A4j: the spot also follows the LATEST PRESS,
     not just the latest settled tap. ogtest20's step 6
     (the ogtest17 signature, third production time):
     the operator tapped Password and typed test1234 in
     the same breath; the server logged ONE input POST —
     the type, carrying the still-armed EMAIL anchor —
     and the Password tap never arrived at all. A press
     can die between the finger and sendInput without a
     word: pointercancel (a browser gesture takeover, a
     second finger converting the gesture to a zoom —
     the zoom layer nulls the press outright), a release
     frame() cannot map (the still mid-swap, naturalWidth
     0), a release point the letterbox rejects, a control
     flip mid-press. Every one of those exits left the
     PREVIOUS spot armed, and a type dispatched after
     them bound the stale anchor at send time and landed
     in the old field with an honest ok. A DISPATCHED
     tap was already safe — the send chain settles its
     ack before a later type dispatches (proven in the
     A4j gate's slow-ack phase) — so the hole is exactly
     the undispatched press. Now a recorded press
     RETIRES the settled spot on the spot
     ('pending', anchor dropped, the settled spot kept
     in spotBackup): the press's own dispatch + ack
     settles the spot as before; a SCROLL dispatch
     restores the backup (a wheel at the page center
     never moved the page's focus, so the scroll is not
     a tap and earns no verdict); and EVERY silent exit
     leaves the spot 'pending' — a type/key then stops
     HERE, in the window, with the honest word, and is
     never sent against the previous field. Unsettled
     means blocked, never silently rebound. */
  var spotState = 'ambient'; /* 'ambient'|'armed'|'refused'|'pending' */
  var spotBackup = null; /* the settled spot a press retired */
  var pressBackups = {}; /* tap number -> its retired spot,
                            held until the tap's ack settles */
  var tapSeq = 0;
  function applySpotAck(payload, res) {
    if (!res || res.ok !== true) return;
    if (spotState === 'pending' && !('tap' in res) &&
        !res.anchor) {
      /* Ticket A4j: a LEGACY ack (no echo — a pre-A4g
         server) cannot verdict the press that retired
         the spot, and A4g's rule for legacy silence is
         "the spot as it was": restore the retired spot
         this tap carried in with it, so a mixed deploy
         degrades to A4e and never wedges 'pending'.
         An echo-carrying ack never reaches this
         branch — its word below is final. */
      var bak = pressBackups[payload.tap];
      delete pressBackups[payload.tap];
      if (bak) {
        spotState = bak.state; typeAnchor = bak.anchor;
      } else {
        spotState = 'ambient'; typeAnchor = null;
      }
      return;
    }
    delete pressBackups[payload.tap];
    if ('tap' in res) {
      if (res.tap !== payload.tap) {
        /* A stale or garbled ack: it is not this tap's
           verdict, so the tap earned NOTHING the client
           can trust — the spot refuses rather than
           guess (and a superseded spot stays dead). */
        typeAnchor = null; spotState = 'refused';
        return;
      }
      if (res.anchor) {
        typeAnchor = res.anchor; spotState = 'armed';
      } else {
        typeAnchor = null; spotState = 'refused';
      }
    } else if (res.anchor) {
      typeAnchor = res.anchor; spotState = 'armed';
    }
  }
  var spotKey = '', spotUrl = '';
  function spotDeathCheck(key, url) {
    if (key && spotKey && key !== spotKey) {
      typeAnchor = null; spotState = 'ambient';
    }
    if (key) spotKey = key;
    if (url && spotUrl && url !== spotUrl &&
        spotState === 'armed') {
      typeAnchor = null; spotState = 'refused';
    }
    if (url) spotUrl = url;
  }
  var typeBtn = $('ogKbSend');
  function sendInput(payload, shotDelay) {
    /* Ticket A4g: every press carries its sequence
       number, stamped HERE — at call time, in gesture
       order — so its ack can be matched to its tap
       even while sends queue behind a long type. */
    if (payload && (payload.kind === 'click' ||
                    payload.kind === 'drag')) {
      payload.tap = ++tapSeq;
    }
    var run = sendChain.then(function () {
      return sendInputNow(payload, shotDelay);
    });
    sendChain = run.then(function () {}, function () {});
    return run;
  }
  function sendInputNow(payload, shotDelay) {
    var isType = !!(payload && payload.kind === 'type');
    /* Ticket A4g: a REFUSED spot stops the type HERE —
       the visitor's latest tap verified no field, so
       there is nowhere honest to type. Nothing is
       sent, the box keeps its text (typeFromField only
       clears on an ok answer), and the word — sticky,
       like any refused input's — says what to do. */
    if ((isType || (payload && payload.kind === 'key')) &&
        spotState === 'pending') {
      /* Ticket A4j: the latest press never settled —
         it either never dispatched or its answer never
         came back. There is no honest field to type
         into, and the previous field's anchor is gone
         on purpose: the type stops HERE, the box keeps
         its text, the word says to tap again. */
      stickyErr = 'Your last tap didn\'t reach the ' +
        'page — nothing was typed. Tap the field ' +
        'again, then type.';
      say(stickyErr);
      return Promise.resolve({ ok: false });
    }
    if ((isType || (payload && payload.kind === 'key')) &&
        spotState === 'refused') {
      stickyErr = 'Tap the field you want to type in ' +
        'first — your last tap didn\'t land in one, ' +
        'so nothing was typed.';
      say(stickyErr);
      return Promise.resolve({ ok: false });
    }
    if ((isType || (payload && payload.kind === 'key')) &&
        typeAnchor) {
      payload = Object.assign({}, payload,
                              { anchor: typeAnchor });
    }
    var ctl = null, slowTimer = null, deadTimer = null;
    if (isType) {
      if (typeBtn) {
        typeBtn.disabled = true;
        typeBtn.textContent = 'Typing…';
      }
      if (!stickyErr) say('✍️ Typing into the page…');
      try {
        if (window.AbortController) {
          ctl = new AbortController();
          slowTimer = window.setTimeout(function () {
            if (!stickyErr) {
              say('✍️ Still typing — the page is answering ' +
                  'slowly. Your text is on its way.');
            }
          }, 20000);
          deadTimer = window.setTimeout(function () {
            try { ctl.abort(); } catch (e) {}
          }, 100000);
        }
      } catch (e) { ctl = null; }
    }
    function settle() {
      if (slowTimer) window.clearTimeout(slowTimer);
      if (deadTimer) window.clearTimeout(deadTimer);
      if (isType && typeBtn) {
        typeBtn.disabled = false;
        typeBtn.textContent = 'Type it';
      }
    }
    return fetch(API + '/browser/input', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctl ? ctl.signal : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; });
    }).then(function (res) {
      settle();
      if (res && res.ok) {
        stickyErr = '';
        say(''); refreshShot(shotDelay);
        /* Ticket A4e: a landed type is also SHOWN — the
           frame loop may be starved, so a second, forced
           frame is requested once the first has had its
           chance to settle (ogtest15's operator had to
           press ↻ by hand to see the dots). */
        if (isType) {
          window.setTimeout(function () {
            if (!shot.hidden) stillForce();
          }, 1600);
        }
        /* Ticket A4: a landed input CONFIRMS the pointer
           position it carried — the marker's freshness
           rides the ack, not just the gesture (the
           marker block below owns the position). */
        if (payload && payload.kind === 'click' &&
            typeof payload.x === 'number') {
          notePtr(payload.x, payload.y);
          /* Ticket A4g: the tap's ack settles the
             writing spot (see applySpotAck at the
             anchor declaration) — an anchor arms it, a
             verified no-field answer refuses it, a
             stale echo is ignored. A4e armed on an
             anchor and otherwise kept the old spot,
             which is how a tap on the password field's
             eye button left Email armed (ogtest17). */
          applySpotAck(payload, res);
        } else if (payload && payload.kind === 'drag' &&
                   payload.path && payload.path.length) {
          var lastPt = payload.path[payload.path.length - 1];
          if (lastPt) notePtr(lastPt.x, lastPt.y);
          /* Ticket A4g: a drag mints at its press point
             too (the server verifies there) — its ack
             settles the spot the same way. */
          applySpotAck(payload, res);
        }
      }
      else {
        /* Ticket A4b: sticky (see stickyErr above) — the
           refusal stays readable until an input lands. */
        stickyErr = (res && res.error) || "That didn't go through.";
        say(stickyErr);
      }
      return res;
    }).catch(function () {
      settle();
      if (isType && ctl && ctl.signal && ctl.signal.aborted) {
        /* Ticket A4e: the client stopped waiting at
           100 s. The server thread is NOT cancelled by
           the abort — the honest word is that the text
           may still land, and it stays in the box so
           the visitor keeps both copies of the truth. */
        stickyErr = '⌛ That type never answered back — it ' +
          'may still land on the page. Your text is still ' +
          'in the box; check the field before sending again.';
      } else {
        stickyErr = 'Connection hiccup — try again.';
      }
      say(stickyErr);
      return { ok: false };
    });
  }
  /* Ticket A4e: the screen this window is driven FROM,
     reported once per confirmed take-control. The
     server fits the page itself to these CSS pixels
     (Emulation device metrics), so the picture that
     comes back is phone-shaped: the whole page, at a
     readable size, with the Width fit nearly exact.
     Best-effort: any failure leaves the desktop-shaped
     page and the Width default still shows all of it.
     Ticket A4f: FIRE-AND-FORGET, in the strong sense —
     A4e sent this through the serialized input chain,
     where a slow metrics call (the same ~70-call CDP
     transport, on a starved session) made the
     visitor's next type QUEUE behind a report they
     never asked for, and a refused report (the erased
     record's 409) painted its refusal into the window
     as if the visitor's own input had failed. A side-
     effect of the hold must never gate the hold's
     traffic or speak in the window: the report now
     rides its own fetch, its outcome is swallowed
     whole, and nothing anywhere waits on it. */
  function sendClientView() {
    var w = 0, h = 0, dpr = 1;
    try {
      w = Math.round(document.documentElement.clientWidth ||
                     window.innerWidth || 0);
      h = Math.round(document.documentElement.clientHeight ||
                     window.innerHeight || 0);
      dpr = window.devicePixelRatio || 1;
    } catch (e) {}
    if (!w || !h) return;
    try {
      fetch(API + '/browser/input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'viewport', vw: w,
                               vh: h, dpr: dpr })
      }).then(function () {}, function () {});
    } catch (e) {}
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
    /* Ticket A4j: a recorded press retires the settled
       spot NOW — the visitor's hand has chosen a new
       target, and until this press dispatches and its
       ack settles, no type may ride the old field's
       anchor (see the spot machine's A4j note). */
    spotBackup = { state: spotState, anchor: typeAnchor };
    typeAnchor = null; spotState = 'pending';
    try { shot.setPointerCapture(e.pointerId); } catch (err) {}
    /* Ticket A4: the finger IS the pointer — the marker
       (below) takes the position from this press and
       follows it through the gesture. */
    ptrHeld = true;
    var fDown = frame();
    if (fDown) {
      var pDown = toNatural(e.clientX, e.clientY, fDown, true);
      if (pDown) notePtr(pDown.x, pDown.y);
    }
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
      /* Ticket A4: the marker follows the finger at the
         same sampling pace the drag path is recorded. */
      var fMove = frame();
      if (fMove) {
        var pMove = toNatural(e.clientX, e.clientY, fMove, true);
        if (pMove) notePtr(pMove.x, pMove.y);
      }
    }
  });
  function endPress(e, cancelled) {
    if (!press) return;
    var p = press; press = null;
    /* Ticket A4: the press is over — the marker stays at
       the release point under its staleness rule. */
    ptrHeld = false;
    paintMarker();
    /* Ticket A4j: every exit from here that sends
       NOTHING (cancelled, no longer driving, no
       mappable frame, a release point in the bars)
       leaves the spot 'pending' on purpose — the
       press retired the old spot at pointerdown and
       never earned a new one, so the next type blocks
       in the window instead of riding the old field. */
    if (!driving || !ptrOn || cancelled) return;
    var f = frame(); if (!f) return;
    var dx = e.clientX - p.x, dy = e.clientY - p.y;
    var disp = Math.sqrt(dx * dx + dy * dy);
    var dur = Date.now() - p.t;
    if (disp < 10 && dur < 600) {
      var pt = toNatural(e.clientX, e.clientY, f, false);
      if (pt) {
        /* Ticket A4j: the press dispatched — its ack
           owns the spot's settlement now. The retired
           spot rides along keyed by the tap's number
           (stamped inside sendInput), in case the ack
           is a legacy one whose silence restores it
           (see applySpotAck). */
        var clickPayload = { kind: 'click', x: pt.x,
          y: pt.y, nw: f.nw, nh: f.nh };
        sendInput(clickPayload, 450);
        if (clickPayload.tap != null) {
          pressBackups[clickPayload.tap] = spotBackup;
        }
        spotBackup = null;
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
      /* Ticket A4j: a scroll is not a tap — the wheel
         rides at the page center and never moves the
         page's focus, so the spot the press retired is
         restored exactly as it was. */
      if (spotBackup) {
        spotState = spotBackup.state;
        typeAnchor = spotBackup.anchor;
        spotBackup = null;
      }
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
    /* Ticket A4j: dispatched — the drag's ack settles
       the spot (it mints at its press point, A4g);
       the retired spot rides along as for a click. */
    var dragPayload = { kind: 'drag', path: path,
      nw: f.nw, nh: f.nh, dur: dur };
    sendInput(dragPayload, 500);
    if (dragPayload.tap != null) {
      pressBackups[dragPayload.tap] = spotBackup;
    }
    spotBackup = null;
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
    /* Ticket A4: the marker rides the same rendered
       rect the zoom just changed — repaint it against
       the new mapping. */
    paintMarker();
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
  /* --- Round 321 Ticket A4: SWIPE CAPTURE. The dashboard
     swipe navigation (og_dashboards.js) listens for
     touchstart/touchend on the DOCUMENT in the bubble
     phase and opens the Crypto Hub / Financial Smart
     Agent on any horizontal swipe >= 70px that doesn't
     start on a form field — it has no idea the browser
     window is up, so on Brent's drill a sideways swipe
     he aimed at the PAGE ejected him full-screen into
     the Crypto Hub mid-session. While the window is up,
     a touch that starts on the window is the page's
     (the pointer handlers above turn it into the page's
     click / drag / scroll input): a capture-phase
     stopPropagation on the panel keeps the gesture from
     ever reaching the document listeners. Propagation
     only — never preventDefault — so taps, clicks and
     the keyboard strip behave exactly as before; and
     only while the window is full, so with the window
     closed the dashboard swipes work exactly as before.
     (og_dashboards.js itself is untouched.) --- */
  function swallowWindowTouch(e) {
    if (fullApplied) e.stopPropagation();
  }
  panel.addEventListener('touchstart', swallowWindowTouch, true);
  panel.addEventListener('touchmove', swallowWindowTouch, true);
  panel.addEventListener('touchend', swallowWindowTouch, true);
  window.OG79ZOOM = {
    frameFromRect: frameFromRect,
    state: function () {
      return { scale: zoom.scale, tx: zoom.tx, ty: zoom.ty };
    },
    reset: resetZoom
  };

  /* --- Round 321 Ticket A4: THE POINTER MARKER ------------
     The stills carry no OS pointer (a CDP screenshot has
     no cursor in it), so on Brent's drill the visitor in
     Take control could not see where the mouse was — the
     page area read as dead black space. The marker is a
     client-side arrow rendered OVER the still at the
     last known pointer position, in the still's NATURAL
     pixels, positioned through the SAME frame mapping
     the input path uses (frame()/frameFromRect — the
     computed object-fit, the Round 79 zoom transform
     included, since both derive from the img's rendered
     rect), so the arrow sits exactly where a tap lands.
     Position sources, in the order the client learns
     them: the visitor's own press / drag path while
     pointer mode is on (pointerdown / pointermove /
     pointerup above), and the /browser/input ACK for a
     landed click or drag (sendInput). /browser/status
     carries NO pointer field (audited og_browser.py:
     active / viewer_url / control / minutes_left /
     page_url / loading_host / phase only), so while OG
     drives there is no server position to show — the
     marker then holds the visitor's last own position
     until it goes stale.
     STALENESS RULE (the stated rule): the marker shows
     while the window is up and a session is live AND
     either a press is in flight or the position is
     younger than PTR_STALE_MS (12 s); it hides when the
     position goes stale, when the window closes, and
     when the session's end is confirmed. It repaints on
     the frame tick, on zoom / fit changes, and on every
     new position.
     The CARET half of the ticket is deliberately NOT a
     drawing: the real field caret blinks inside the
     browsed page, so what the visitor needs is a frame
     fresh enough to show the focused field and the
     typed text — the loader above (A3f + the A4
     watchdog) is that half, and the A4 gate proves it
     by ink: tap the field, type, and the typed frame
     paints in the panel. No fake caret is rendered. --- */
  var PTR_STALE_MS = 12000;
  var ptrPos = null;      /* {nx, ny, at} — natural px + ms */
  var ptrHeld = false;
  var ptrLastClient = null;
  var markEl = document.createElement('div');
  markEl.id = 'ogPtrMark';
  markEl.hidden = true;
  markEl.setAttribute('aria-hidden', 'true');
  markEl.innerHTML =
    '<svg viewBox="0 0 20 20" width="20" height="20">' +
    '<path d="M1 1 L1 15.2 L4.9 12 L7.1 17.8 L9.3 16.9 ' +
    'L7.1 11.2 L12.4 11.2 Z" fill="#ffc107" ' +
    'stroke="#111" stroke-width="1.4" ' +
    'stroke-linejoin="round"/></svg>';
  if (view) view.appendChild(markEl);
  function notePtr(nx, ny) {
    ptrPos = { nx: nx, ny: ny, at: Date.now() };
    paintMarker();
  }
  function paintMarker() {
    if (!markEl || !view) return false;
    var show = false;
    ptrLastClient = null;
    if (fullApplied && ptrPos &&
        (ptrHeld || (Date.now() - ptrPos.at) < PTR_STALE_MS)) {
      var f = frame();
      if (f) {
        var vr = view.getBoundingClientRect();
        var cx = f.ox + ptrPos.nx * f.scale,
            cy = f.oy + ptrPos.ny * f.scale;
        markEl.style.left = (cx - vr.left) + 'px';
        markEl.style.top = (cy - vr.top) + 'px';
        ptrLastClient = { x: cx, y: cy };
        show = true;
      }
    }
    markEl.hidden = !show;
    return show;
  }
  /* Gate/debug handle (the OG79ZOOM / OGSTILL
     precedent): where the marker believes the pointer
     is, in natural and client pixels, and its age. */
  window.OGPTR = {
    staleMs: PTR_STALE_MS,
    pos: function () {
      var shown = paintMarker();
      if (!ptrPos) return { shown: false };
      return { shown: shown,
               nx: ptrPos.nx, ny: ptrPos.ny,
               cx: ptrLastClient ? ptrLastClient.x : null,
               cy: ptrLastClient ? ptrLastClient.y : null,
               ageMs: Date.now() - ptrPos.at,
               held: ptrHeld };
    }
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
  /* Ticket A4b: paintHead takes the EFFECTIVE control
     (reconcile's held state, below) when one is passed:
     the header and the bar's Take/Hand-back label name
     the control the visitor actually holds, never a
     single status read's raw field — one reverted read
     used to flip the header to "Browsing" and relabel
     the button mid-takeover (the ogtest11 flap). */
  function paintHead(st, effControl) {
    if (!headState) return;
    if (!st || !st.enabled || !st.active) {
      headState.textContent = 'Browser';
      if (headSite) headSite.textContent = '';
      if (headMeta) headMeta.textContent = '';
      winTakeBtn.textContent = 'Take control';
      return;
    }
    var ctl = effControl === undefined ? st.control : effControl;
    var host = hostOf(st.page_url) || st.loading_host || '';
    var t;
    if (st.phase === 'starting' || st.loading_host) {
      t = 'Opening ' + (st.loading_host || host || '…') + '…';
    } else if (ctl === 'visitor') {
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
    winTakeBtn.textContent = ctl === 'visitor'
      ? 'Hand back' : 'Take control';
  }

  /* --- Round 321 Ticket A4: AUTO-OPEN + the confirmed-
     end rule.
     AUTO-OPEN: a browser session that becomes active
     surfaces the window BY ITSELF, the way a text
     arrives (Brent, on his drill: OG's chat showed a
     status pill and an "Open browser" button he had to
     hunt for — "it should pop up like a text"). The
     session's key is its viewer_url (the page script's
     own session key): the first active read carrying a
     key this client has neither auto-opened nor parked
     raises winWanted, and the branches below open the
     window. The visitor's own close is respected:
     parking the window records the key (parkedKey), the
     status ticks that follow carry the SAME key, and
     the window stays down until a NEW session (a new
     key, or a fresh client after a confirmed end)
     starts. The pill / Open browser button remain as
     the manual re-open path, unchanged.
     CONFIRMED END: a session's end is acted on only
     when TWO consecutive status reads agree it is gone.
     One inactive-shaped read — the shape a pool-starved
     /browser/status returns while the session is very
     much alive (its server default payload IS
     active:false) — used to tear the window down, stop
     the frame loop and reset the header on the spot;
     combined with the A3f loader's missing watchdog
     that is the second half of Brent's black screen
     (window torn down around the Crypto ejection, black
     on re-entry). Now the first such read only arms a
     fast confirmation poll (~400 ms, the takeAsked
     pattern); the window, the last frame and the header
     all stay exactly as they were unless the next read
     confirms the end. A real end therefore still closes
     the window itself — about one fast poll later. --- */
  var layerActive = false, inactiveReads = 0,
      curKey = '', autoKey = '', parkedKey = null,
      prevDriving = false;

  /* --- Round 321 Ticket A4b: CONFIRMED CONTROL — the
     confirmed-end rule, applied to the control field.
     A4 confirmed the session's END before acting on it
     but left control following SINGLE status reads:
     one read whose control field said "og" while the
     visitor held the wheel flipped the header to
     "Browsing", closed the keyboard strip, hid the
     toggles and killed pointer input until the visitor
     re-took control by hand (ogtest11's flap — the
     server record itself can transiently revert: it is
     written WHOLE by paths holding pre-takeover copies,
     and og_browser now carries control across those
     writes by timestamp; this rule is the client's half
     of the same fix). Control once CONFIRMED held is
     therefore held through a dissenting read: the first
     only arms a fast (~400 ms) confirmation poll, and
     the panel drops to Browsing only when TWO
     consecutive reads agree the visitor no longer
     drives. An alternating field (visitor/og/visitor…)
     never reaches two in a row, so control survives
     flapping however many polls it spans. The visitor's
     OWN Hand back is intent, not noise — handAsked
     applies it on the first agreeing read, no wait. --- */
  var ctrlHeld = false, ctrlDissent = 0, handAsked = false;

  /* --- Ticket A4f: THE HOLD THAT NOISE CANNOT DROP —
     A4b's two-read rule assumed a dissenting read is a
     TRANSIENT lie: a stale whole-record write lands
     once, the next read tells the truth again. The
     ogtest16 drill (2026-10-20, the A4e build) met the
     other shape: the usage store is ONE file that every
     writer loads and saves WHOLE, under a lock that
     exists once per server process — and there are two
     processes. A writer whose load predates the control
     grant and whose save follows it ERASES the grant
     from the file (the A4f server proof: the grant, its
     control_ts and all, overwritten whole; nobody
     handed back). Every later read then truthfully
     says "og" — two "agreeing" reads that are really
     one overwrite — and A4b's rule dropped the panel
     mid-drive, exactly as coded. Read-counting cannot
     tell that lie from a real change of hands; the
     system's own contract can: while this window is up,
     control changes hands ONLY at the visitor's word
     (OG is hands-off for the whole takeover by design).
     So, once a take is CONFIRMED held and the window is
     up, dissenting reads are noise, full stop: the
     panel holds everything, polls fast, and REPAIRS
     the record — reassertControl re-posts the visitor's
     own grant (throttled) until the reads agree again,
     which also un-409s the visitor's inputs and hands
     the page its phone fit (the first viewport report
     met the erased record and was refused; viewOwed
     re-reports it when the record agrees). The same
     repair covers an UNCONFIRMED take: the tap's own
     post may be the write the race ate (the drill's
     first tap "did not register" — one post, no
     confirmation, the warning after twelve misses).
     The exits are unchanged and complete: the
     visitor's own Hand back (handAsked), the confirmed
     end (two inactive reads), End task. The dissent
     counter survives as the streak's measure; while
     the window is PARKED the pre-A4f two-read drop
     still applies — a parked panel asserts nothing, so
     a hand-back given in chat while the window is down
     is honored the old way. --- */
  var lastAssertAt = 0, viewOwed = false;
  function reassertControl() {
    var now = Date.now();
    if (now - lastAssertAt < 2500) return;
    lastAssertAt = now;
    try {
      fetch(API + '/browser/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'visitor' })
      }).then(function () {}, function () {});
    } catch (e) {}
  }

  function reconcile(st) {
    var act = !!(st && st.enabled && st.active);
    var key = (st && st.viewer_url) || '';
    if (!act) {
      if (layerActive && inactiveReads < 1) {
        /* Unconfirmed: hold everything — window, frame,
           header — and ask again fast. */
        inactiveReads++;
        window.setTimeout(poll, 400);
        return;
      }
      inactiveReads = 0;
      layerActive = false;
      autoKey = '';
      parkedKey = null;
      curKey = '';
      prevDriving = false;
      ctrlHeld = false;
      ctrlDissent = 0;
      handAsked = false;
      viewOwed = false;
      lastAssertAt = 0;
      ptrPos = null;
      driving = false;
      paintHead(st);
      paintMarker();
      /* The session ended underneath (Stop task, the
         20-minute idle kill, budget, page-leave): the
         window closes itself and nothing is left open —
         no state outlives the session it points at. */
      takeAsked = false;
      winWanted = false;
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      stickyErr = '';
      /* Ticket A4g: the writing spot dies with the
         session — the confirmed end is where the death
         lives (A4e named the rule but no reset existed):
         back to ambient, so a fresh session starts
         tapless, the way Round 43 typed. */
      typeAnchor = null;
      spotState = 'ambient'; spotKey = ''; spotUrl = '';
      spotBackup = null; /* Ticket A4j: no press outlives it either */
      pressBackups = {};
      say('');
      removeFull();
      return;
    }
    inactiveReads = 0;
    /* Ticket A4g: the spot's status-riding deaths — a
       NEW session key retires the old session's spot
       to ambient, and a page_url change under an
       armed spot means the page the tap named is gone
       (refuse; the server checks the same stamp). */
    spotDeathCheck(key, (st && st.page_url) || '');
    if (key) curKey = key;
    /* Ticket A4b: the EFFECTIVE control (see the block
       above) — header, toggles, strip and the pointer
       gate all follow this, never the raw field. */
    var rawD = st.control === 'visitor';
    var d;
    if (handAsked) {
      /* The visitor's own Hand back: settle on the
         first read that agrees; until it lands the
         visitor still drives. */
      if (!rawD) {
        handAsked = false;
        ctrlHeld = false;
        ctrlDissent = 0;
        d = false;
      } else {
        d = true;
      }
    } else if (rawD) {
      if (!ctrlHeld) {
        /* A fresh confirmation: the next repair streak
           starts with its first re-post unthrottled. */
        lastAssertAt = 0;
      }
      if (ctrlDissent > 0) {
        /* The dissent streak just ended — the record
           agrees again; the screen report is owed once
           more (its first send may have met the erased
           record and been refused). */
        viewOwed = true;
      }
      ctrlHeld = true;
      ctrlDissent = 0;
      d = true;
    } else if (ctrlHeld) {
      ctrlDissent++;
      if (ctrlDissent === 1) {
        /* A NEW streak is new evidence the record
           broke again — its first repair is never
           throttled; the streak's later repairs are. */
        lastAssertAt = 0;
      }
      if (!winWanted && ctrlDissent >= 2) {
        /* Parked: the pre-A4f rule — a hand-back given
           in chat while the window is down is honored
           after two agreeing reads, and nothing is
           re-asserted on a panel nobody is looking at. */
        ctrlHeld = false;
        ctrlDissent = 0;
        viewOwed = false;
        d = false;
      } else {
        /* Ticket A4f: HOLD EVERYTHING (header, strip,
           toggles, pointer), ask again fast, and repair
           the record — a dissenting read while the
           window is up is the store race's noise, never
           a change of hands (see the block above). */
        d = true;
        window.setTimeout(poll, 400);
        if (winWanted) reassertControl();
      }
    } else {
      d = false;
      if (takeAsked) {
        /* Ticket A4f: an unconfirmed take re-posts its
           own grant — the tap's write may be the one
           the race ate (the drill's first tap). The
           confirmation polls below remain the only
           truth-teller; this just gives the record a
           second chance to hold what the visitor
           already asked for. */
        reassertControl();
      }
    }
    driving = d;
    paintHead(st, d ? 'visitor' : 'og');
    /* AUTO-OPEN (above): a session key this client has
       neither opened for nor parked on raises the
       window unprompted; a keyless session raises it
       on the inactive->active edge unless the visitor
       parked this client session. */
    if (key && key !== autoKey && key !== parkedKey) {
      autoKey = key;
      winWanted = true;
    } else if (!layerActive && !key && parkedKey === null) {
      winWanted = true;
    }
    layerActive = true;
    if (d !== prevDriving) {
      /* The control flip gets a fresh frame of its own
         (the loader coalesces it with any in-flight
         load): the state the header just named is the
         state the picture shows. */
      prevDriving = d;
      refreshShot(150);
      if (!d && spotState === 'armed') {
        /* Ticket A4g: control left the visitor's hands
           — the spot dies with the hold that earned it.
           After re-taking control, a type must earn a
           fresh tap; it never rides a dead hold's
           anchor into a page OG may have moved on. */
        typeAnchor = null; spotState = 'refused';
      }
      if (d) {
        /* Ticket A4e: taking control returns the window
           to the whole page (a Fill chosen while watching
           never follows the visitor into the driver's
           seat), and the page is told the size of the
           screen it is being driven from (sendClientView
           — the server fits the page itself to it). */
        assertFitWidth();
        viewOwed = true;
      }
    }
    if (d && viewOwed) {
      /* Ticket A4f: the screen report fires when the
         hold (re)confirms — on the flip, and once more
         when a dissent streak ends (the first report
         may have met the erased record and been
         refused). Fire-and-forget — see sendClientView. */
      viewOwed = false;
      sendClientView();
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
         OG drives anyway) but the window stays up.
         Ticket A4b: the routine clear is guarded — a
         failed input's word survives the mode change
         that follows it (the ogtest11 erasure). */
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      clearSay();
      applyFull();
    } else {
      if (kbOn) setKb(false);
      if (ptrOn) setPtr(false);
      clearSay();
      removeFull();
    }
  }
  /* Ticket A4d: poll replies land IN REQUEST ORDER, and a
     hung poll DIES instead of landing late. On the
     ogtest14 drill the server instance failed mid-session;
     the status fetches queued behind the dying workers
     stopped resolving, and the window's header sat on its
     last paint — "Opening facebook.com…" — for the whole
     back half of the session while frames, taps and typing
     (other routes) still worked: the header is painted
     ONLY from this poll, and this poll had no timeout and
     no ordering, so nothing bounded its staleness and a
     slow starting-era reply resolving late would repaint
     that stale state over the truth. Each poll now carries
     a sequence number — reconcile runs only for a reply
     newer than every reply already applied — and an
     abort turns a wedged fetch into a quiet miss the next
     interval poll retries.
     Ticket A4i: the abort budget is 30 s, not A4d's 10 s.
     A4d sized it for a DYING instance, whose fetches hang
     forever; production's live failure is the other
     shape — a HEALTHY but pool-starved instance whose
     /browser/status reads legitimately take ~12 s (every
     read pays a DB-backed tier lookup and a Steel
     viewer call; the ogtest19 drill's faulthandler found
     the threads parked in og_accounts._session_for /
     og_db execute). At 10 s EVERY poll aborted, reconcile
     never ran once in either drill, and with it died
     everything reconcile alone does — the auto-open,
     the header paint, the take confirmation and the A4f
     reassert repair — while the timeout-free pollers
     (the page panel, og_shell) kept painting on late
     answers, so the session looked alive and the two
     control POSTs (a file-only route, answered at once,
     200 in the Render log) changed nothing anyone could
     see. Proven in repro/a4/drive_a4i_slowfull.js: at a
     12 s status delay the take posts exactly once, never
     confirms, and confirms with no new tap the moment
     the delay lifts. The abort stays a wedge-breaker
     only: freshness is the sequence number's job, never
     the clock's, and the budget must exceed the status
     route's real worst-case read, not a healthy LAN's.
     And the poll no longer PILES UP: it used to fire on
     every interval and every fast re-poll regardless of
     a read already in flight, so under exactly this
     latency the browser's per-origin connection cap
     queued fresh reads behind a wall of stale ones and
     stretched the effective latency past ANY abort
     budget (the first A4i candidate — 30 s alone —
     failed its own slow gate on this). One read in
     flight: the next poll waits for it to land or for
     the wedge-breaker to free the slot, and a reply
     that lands late is still judged by its sequence
     number. Under drill latency the layer's read now
     costs the route's latency, not the queue's. */
  var pollSeq = 0, pollApplied = 0, pollBusy = false;
  function poll() {
    /* Ticket A4i: ONE read in flight (see the block
       above) — a poll asked for while one is flying is
       a no-op; the flying read's landing (reconcile
       re-schedules the fast polls) and the 2.5 s
       interval keep the cadence. */
    if (pollBusy) return;
    pollBusy = true;
    var id = ++pollSeq;
    var ctl = null, timer = null;
    function done() {
      pollBusy = false;
      if (timer) window.clearTimeout(timer);
    }
    try {
      if (window.AbortController) {
        ctl = new AbortController();
      }
    } catch (e) { ctl = null; }
    /* The wedge-breaker: at 30 s the fetch is aborted
       where it can be, and the slot frees either way —
       a deader-than-dead read can never lock the poll
       shut (a late landing is still sequence-judged). */
    timer = window.setTimeout(function () {
      try { if (ctl) ctl.abort(); } catch (e) {}
      pollBusy = false;
    }, 30000);
    fetch(API + '/browser/status',
          ctl ? { signal: ctl.signal } : undefined)
    .then(function (r) { return r.json(); })
    .then(function (st) {
      done();
      if (id <= pollApplied) return;
      pollApplied = id;
      reconcile(st);
    }).catch(function () { done(); });
  }
  if (takeBtn) {
    takeBtn.addEventListener('click', function () {
      /* Round 61: record the intent BEFORE the first poll —
         it is what keeps the bar whole (see reconcile)
         while the server confirmation is in flight.
         Ticket A: a takeover also opens the window. */
      takeAsked = true; takeMisses = 0; takeWarned = false;
      handAsked = false;
      winWanted = true;
      /* Ticket A4f: a fresh tap is fresh intent — its
         repair re-post (reconcile) starts unthrottled. */
      lastAssertAt = 0;
      window.setTimeout(poll, 350);
    });
  }
  if (handBtn) {
    handBtn.addEventListener('click', function () {
      /* Ticket A: handing back returns the DRIVING to OG;
         the window stays up so the visitor keeps watching
         (the ✕ parks the window). The poll confirms.
         Ticket A4b: the hand-back is the visitor's own
         intent (handAsked) — reconcile settles it on the
         first agreeing read, never behind the two-read
         dissent rule that guards against noise. */
      takeAsked = false;
      handAsked = true;
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
      parkedKey = curKey;
      removeFull();
    });
  }
  winCloseBtn.addEventListener('click', function () {
    /* Park the window back to the docked panel. The
       SESSION is not ended here — it ends on Stop task,
       on leaving the page (the /browser/leave beacon),
       or on the server's 20-minute idle kill. Ticket
       A4: the park is recorded against this session's
       key, so the auto-open never re-pops a window the
       visitor closed on purpose (see reconcile). */
    winWanted = false;
    parkedKey = curKey;
    takeAsked = false;
    handAsked = false;
    stickyErr = '';
    if (kbOn) setKb(false);
    if (ptrOn) setPtr(false);
    say('');
    removeFull();
  });
  stopBtn.addEventListener('click', function () {
    winWanted = false;
    takeAsked = false;
    handAsked = false;
    if (endTaskBtn) endTaskBtn.click();
    removeFull();
  });
  winTakeBtn.addEventListener('click', function () {
    if (driving) { if (handBtn) handBtn.click(); }
    else if (takeBtn) takeBtn.click();
  });
  fitBtn.addEventListener('click', function () {
    /* Ticket A4e: the ONLY road into Fill is this tap
       (see setFit) — and the same button is always the
       way back to the whole page. The frame itself is
       untouched (same img, same loaded frame: the flip
       can never blank the picture). */
    setFit(!fitFill);
  });
  poll();
  window.setInterval(poll, 2500);
})();

// Round 56: Buy-more-tokens card + pack picker.
// Brent's rules (2026-10-10): tokens stay with the user until
// they're gone; they only start burning once the weekly cap
// runs out; buying needs an active subscription. The card
// sits on the RIGHT of the plan card in the menu; balance and
// buy links come from GET /tokens (server-gated: links only
// ever exist for subscribers). Picker layout (Round 57, owner
// directive): the packs you can buy on top, then the balance
// and token info underneath them — all inside the same area.
(function () {
  "use strict";

  function fmt(n) { return Number(n || 0).toLocaleString("en-US"); }

  function rateRows(rates) {
    return [
      ["Chat", "1 token per chat token"],
      ["Picture", fmt(rates.image) + " tokens each"],
      ["Story video", fmt(rates.video) + " tokens each"],
      ["Song", fmt(rates.song) + " tokens each"],
      ["Voice reply", fmt(rates.tts) + " tokens each"]
    ].map(function (r) {
      return "<div>" + r[0] + " — " + r[1] + "</div>";
    }).join("");
  }

  function buildPanel(data) {
    var panel = document.createElement("section");
    panel.id = "ogTokensPanel";
    panel.className = "og-tokens-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Token packs");
    panel.hidden = true;
    var packs = (data.packs || []).map(function (p) {
      if (p.available && p.url) {
        return '<a class="og-pack" href="' + p.url + '">' +
          '<span class="og-pack-price">$' + p.price + "</span>" +
          '<span class="og-pack-amt">' + fmt(p.tokens) +
          " tokens</span></a>";
      }
      return '<div class="og-pack off">' +
        '<span class="og-pack-price">$' + p.price + "</span>" +
        '<span class="og-pack-off">not open yet</span></div>';
    }).join("");
    var gateBlock = "";
    if (!data.subscriber) {
      gateBlock = '<div class="og-tokens-subonly">Token packs ' +
        'are subscribers only — you need an active plan to buy ' +
        'tokens. <a href="/pro">See the plans</a></div>';
    }
    panel.innerHTML =
      '<div class="og-tokens-top"><h2>Token packs</h2>' +
      '<button type="button" class="og-circle-btn" ' +
      'id="ogTokensClose" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button></div>' +
      '<div class="og-tokens-scroll">' +
      gateBlock + packs +
      '<p class="og-tokens-balrow">Your tokens: <b>' +
      fmt(data.balance) + " on hand</b></p>" +
      '<div class="og-tokens-rates"><b>How tokens burn</b><br>' +
      "Your weekly plan limits always spend first. Tokens only " +
      "start burning when you run out of your weekly cap:" +
      rateRows(data.rates || {}) + "</div>" +
      '<p class="og-tokens-legal">Tokens never expire and stay ' +
      "on your account until you use them all. Tokens can't be " +
      "transferred or turned back into cash.</p>" +
      "</div>";
    document.body.appendChild(panel);
    var scrim = document.createElement("div");
    scrim.id = "ogTokensScrim";
    scrim.className = "og-tokens-scrim";
    scrim.hidden = true;
    document.body.appendChild(scrim);
    function close() {
      panel.classList.remove("open");
      scrim.hidden = true;
      setTimeout(function () { panel.hidden = true; }, 260);
    }
    function open() {
      panel.hidden = false;
      scrim.hidden = false;
      requestAnimationFrame(function () {
        panel.classList.add("open");
      });
    }
    panel.querySelector("#ogTokensClose")
      .addEventListener("click", close);
    scrim.addEventListener("click", close);
    return { open: open, close: close };
  }

  function tryBuild() {
    var planCard = document.querySelector(".og-plan-card");
    if (!planCard || document.getElementById("ogTokensCard")) {
      return;
    }
    fetch("/tokens", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || document.getElementById("ogTokensCard")) {
          return;
        }
        var row = document.createElement("div");
        row.className = "og-plan-row";
        planCard.parentNode.insertBefore(row, planCard);
        row.appendChild(planCard);
        var card = document.createElement("section");
        card.id = "ogTokensCard";
        card.className = "og-tokens-card";
        card.innerHTML =
          '<h3 class="og-tokens-name">Tokens</h3>' +
          '<p class="og-tokens-bal"><b>' + fmt(data.balance) +
          "</b> on hand</p>" +
          '<p class="og-tokens-note">Only burn after your ' +
          "weekly cap runs out. Never expire.</p>" +
          '<button type="button" class="og-plan-action ' +
          'og-tokens-action" id="ogTokensOpen">Buy more ' +
          "tokens →</button>";
        row.appendChild(card);
        var panelApi = buildPanel(data);
        card.querySelector("#ogTokensOpen")
          .addEventListener("click", panelApi.open);
      })
      .catch(function () { /* card stays absent — menu intact */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryBuild);
  } else {
    tryBuild();
  }
  // The menu can be re-rendered; a light retry covers late
  // plan cards without polling forever.
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    tryBuild();
    if (document.getElementById("ogTokensCard") || tries > 20) {
      clearInterval(iv);
    }
  }, 1500);
})();

/* Round 70: the shared full-view scaffold + OSM map
   (static/og_r70.js) that the reshaped og_r65 (Languages
   page) and og_r66 (Driving / Construction views) build
   on; same ride-along loader — the page is at its push
   ceiling. Consumers resolve window.OG70 at tap time, so
   stub order here is a courtesy, not a dependency. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r70.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 62: message actions + hands-free live in their own
   asset (static/og_r62.js); the page sits at its push
   ceiling, so the asset loads from here — the page's last
   static script — instead of a new page tag. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r62.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 63: 2-step verification lives in its own asset
   (static/og_r63.js); same ride-along loader as Round 62 —
   the page is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r63.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 65: the Languages menu section lives in its own asset
   (static/og_r65.js); same ride-along loader as Rounds 62/63 —
   the page is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r65.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 66: the GPS menu section (driving + job sites) lives in
   its own asset (static/og_r66.js); same ride-along loader as
   Rounds 62/63/65 — the page is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r66.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 84: the shit-talking switch row (menu Account card)
   lives in its own asset (static/og_r84.js); same ride-along
   loader as Rounds 62/63/65/66 — the page is at its push
   ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r84.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 85: the in-shell plans view (the plan card's tap
   opens the plans inside the shell instead of navigating
   to /pro — see static/og_r85.js for why); same ride-along
   loader as Rounds 62/63/65/66/84. Its src carries the
   Round 85 version stamp like every injected asset here:
   any round that changes a covered static bumps the stamp
   in this file AND in index_epic.html in the same push. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r85.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 90: the Permissions folder (menu row -> full
   view) + the chat maps handoff live in their own asset
   (static/og_r90.js); same ride-along loader as Rounds
   62/63/65/66/84/85 — the page is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r90.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Generator 7a: the podcast card (script approval + in-chat
   audio player) lives in its own asset (static/og_podcast.js);
   same ride-along loader as Rounds 62/63/65/66 — the page is
   at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_podcast.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Generator 7b: the designer (thumbnail / poster / meme
   interception + in-chat design card) lives in its own asset
   (static/og_designer.js); same ride-along loader — the page
   is at its push ceiling. Loaded after 7a so its sendMessage
   wrap lands OUTSIDE the page's generic /image wrapper:
   designer phrasing is claimed before a poster/meme ask can
   fall through to a plain picture. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_designer.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Generator 7c: the book card (outline approval + chapter
   progress + reader overlay) lives in its own asset
   (static/og_books.js); same ride-along loader — the page
   is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_books.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Generator 7d: the motion-video card (plan + cost approval
   + in-chat <video> player) lives in its own asset
   (static/og_motion.js); same ride-along loader — the page
   is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_motion.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Generator 7f: the voice-lab card (clone upload +
   attestation approval + sfx approval/player) lives in its
   own asset (static/og_voice_lab.js); same ride-along loader
   — the page is at its push ceiling. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_voice_lab.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Wallet Phase A: the Connections Wallet row (connect /
   status / history / remove) lives in its own asset
   (static/og_wallet.js); same ride-along loader — the page
   is at its push ceiling. The asset itself stays invisible
   while the wallet gate is off. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_wallet.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 97: the real wave meter (bars driven by the /tts
   playback element's Web Audio analyser, shown only while
   OG's voice actually plays) lives in its own asset
   (static/og_r97.js); same ride-along loader as Rounds
   62/63/65/66/84/85/90 — the page is at its push ceiling.
   Loaded before og_stoplisten.js, which stays LAST. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r97.js' + '?v=99';
  document.head.appendChild(s);
})();

/* Round 98: the Voice folder (menu row -> dedicated
   Voice page) + the speaking seam that carries the
   account's stored voice pick into the page's /tts
   requests live in their own asset (static/og_r98.js);
   same ride-along loader as Rounds
   62/63/65/66/84/85/90/97 — the page is at its push
   ceiling. Loaded before og_stoplisten.js, which
   stays LAST. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r98.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 106: the locker save becomes an icon — the
   temp-upload save moves to the topbar beside the pen,
   and the in-chat video/song saves become top-right
   icons of their cards (static/og_r106.js); same
   ride-along loader as Rounds
   62/63/65/66/84/85/90/97/98 — the page is at its push
   ceiling. Loaded before og_stoplisten.js, which
   stays LAST. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r106.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 108: Reboot OG — the Tools-card row + the
   confirm card (the per-account soft reboot, and the
   owner-only "Restart OG's server" behind its own
   stronger confirm) live in their own asset
   (static/og_r108.js); same ride-along loader as
   Rounds 62/63/65/66/84/85/90/97/98/106 — the page is
   at its push ceiling. Loaded before
   og_stoplisten.js, which stays LAST. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r108.js' + '?v=98';
  document.head.appendChild(s);
})();

/* Round 275: the header bubble's live helper line —
   the activity layer that polls /activity/status and
   paints what OG / his helpers are really doing into
   the status pills, over the idle text only (static/
   og_r275.js); same ride-along loader as Rounds
   62/63/65/66/84/85/90/97/98/106/108 — the page is at
   its push ceiling. Loaded before og_stoplisten.js,
   which stays LAST. */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_r275.js' + '?v=99';
  document.head.appendChild(s);
})();

/* Stop-listening on leave: the Talk kill-switch (mic off
   the moment the user leaves the page, partial dictation
   discarded, no auto-restart on return) lives in its own
   asset (static/og_stoplisten.js); same ride-along loader
   — the page is at its push ceiling. Loaded LAST so its
   recognition onresult/onend wrap lands outside every
   earlier wrap (e.g. og_r62's voice approvals). */
(function () {
  var s = document.createElement('script');
  s.src = '/static/og_stoplisten.js' + '?v=98';
  document.head.appendChild(s);
})();

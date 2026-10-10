// Round 56: Buy-more-tokens card + pack picker.
// Brent's rules (2026-10-10): tokens stay with the user until
// they're gone; they only start burning once the weekly cap
// runs out; buying needs an active subscription. The card
// sits on the RIGHT of the plan card in the menu; balance and
// buy links come from GET /tokens (server-gated: links only
// ever exist for subscribers).
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
      'id="ogTokensClose" aria-label="Close">✕</button></div>' +
      '<div class="og-tokens-scroll">' +
      '<p class="og-tokens-balrow">Your balance: <b>' +
      fmt(data.balance) + " tokens</b></p>" +
      gateBlock + packs +
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

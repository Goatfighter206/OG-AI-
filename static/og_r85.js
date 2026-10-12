// Round 85: the in-shell plans view.
//
// Brent's report (2026-10-12): tapping the menu's plan card
// "crashes the server" — the card's face had no handler at
// all (a dead tap over most of its area), and its one live
// row was a TOP-LEVEL navigation to /pro, the only menu
// action that leaves the shell. When the service blips
// (the R80 distress signature — measured live again
// tonight: /health at 8-23 s, both hosts cycling 502s,
// then recovering on their own), a navigation strands the
// user on a dead page and the shell is gone, while every
// in-page action survives via fetch + the shell's
// reconnecting watcher.
//
// This asset fixes the tap where it lives:
// - ANY click on the plan card (face or the Change-plan
//   row) opens the plans INSIDE the shell — no navigation.
//   The row's href="/pro" stays as the no-JS fallback.
// - The view renders all six tiers from GET /tiers (the
//   single source for tier data) and marks the signed-in
//   user's current plan (from GET /history, the same
//   source the menu's own plan line reads).
// - Checkout buttons are client constants (the created
//   Stripe links), gated by the payload's checkout_open —
//   the R58/R59 machinery. The r85 suite pins the four
//   server-default constants against og_tiers._DEFAULT_LINKS
//   and red against stripe-activation/LINKS.md (red's link
//   lives in Render env OG_LINK_RED, which is why /tiers
//   reports red checkout_open=true in production).
// - Feedback discipline (R83): the view opens the instant
//   the card is tapped, with a loading line; a failed fetch
//   lands on a retry line inside the view — never a silent
//   nothing, and the shell is never torn down.
//
// Disclosed difference vs /pro: the server-rendered page
// appends ?client_reference_id=<ogai_uid> to its buttons;
// ogai_uid is httpOnly, so no client script can read it
// and these buttons cannot append it. Purchases from here
// attribute through the same machinery unattributed buyers
// already use (link metadata + the /pro/success attach
// flows). The view's foot keeps a link to the full /pro
// page for the server-rendered path.
(function () {
  "use strict";

  var LINKS = {
    red: "https://buy.stripe.com/fZu8wQ8GBdZl8Dw0yadby09",
    standard: "https://buy.stripe.com/00wfZi8GB3kHaLEft4dby04",
    pro: "https://buy.stripe.com/00wcN63mh2gD4ng6Wydby05",
    blue: "https://buy.stripe.com/3cIeVeg936wT9HAbcOdby06",
    blackout: "https://buy.stripe.com/6oU9AUf4Zg7t6vo94Gdby07"
  };

  var CSS = [
    ".og-plans-panel{position:fixed;top:0;right:0;height:100vh;",
    "height:100dvh;width:min(430px,94vw);background:#0c0c0c;",
    "border-left:1px solid #2b2b2b;z-index:9600;",
    "transform:translateX(105%);transition:transform .25s ease;",
    "display:flex;flex-direction:column;",
    "padding:18px 16px calc(16px + env(safe-area-inset-bottom));",
    "box-shadow:-12px 0 32px rgba(0,0,0,.5)}",
    ".og-plans-panel.open{transform:translateX(0)}",
    ".og-plans-panel[hidden]{display:none}",
    ".og-plans-scrim{position:fixed;inset:0;",
    "background:rgba(0,0,0,.55);z-index:9599}",
    ".og-plans-scrim[hidden]{display:none}",
    ".og-plans-top{display:flex;align-items:center;",
    "justify-content:space-between;gap:12px;padding:0 2px 12px}",
    ".og-plans-top h2{margin:0;color:#f5f5f5;font-size:1.25rem}",
    ".og-plans-scroll{flex:1 1 auto;overflow-y:auto;min-height:0}",
    ".og-plans-note{color:#a8a8a8;line-height:1.5;margin:4px 2px 14px}",
    ".og-plans-card{background:linear-gradient(165deg,#1d1d1d,#131313);",
    "border:1px solid #2b2b2b;border-radius:20px;padding:16px;",
    "margin:0 0 14px}",
    ".og-plans-card.current{border-color:rgba(255,193,7,.75)}",
    ".og-plans-name{margin:0;font-size:1.18rem;font-weight:700;",
    "color:#f5f5f5}",
    ".og-plans-price{margin:4px 0 0;color:#ffc107;font-weight:700;",
    "font-size:1.05rem}",
    ".og-plans-price span{color:#a8a8a8;font-weight:400;",
    "font-size:.85rem}",
    ".og-plans-facts{margin:10px 0 0;padding:0 0 0 18px;",
    "color:#bcbcbc;line-height:1.55;font-size:.94rem}",
    ".og-plans-buy{display:block;text-align:center;margin-top:13px;",
    "padding:12px 16px;border-radius:999px;background:#ffc107;",
    "color:#111;font-weight:700;text-decoration:none}",
    ".og-plans-off{display:block;text-align:center;margin-top:13px;",
    "padding:12px 16px;border-radius:999px;border:1px solid #3a3a3a;",
    "color:#8f8f8f;font-weight:700}",
    ".og-plans-current{display:block;text-align:center;",
    "margin-top:13px;padding:12px 16px;border-radius:999px;",
    "border:1px solid rgba(255,193,7,.75);color:#ffc107;",
    "font-weight:700}",
    ".og-plans-foot{margin-top:2px;text-align:center}",
    ".og-plans-foot a{color:#bdbdbd}",
    ".og-plans-retry{background:#ffc107;color:#111;border:none;",
    "border-radius:999px;padding:9px 22px;font-weight:700;",
    "font-family:inherit;cursor:pointer;margin-top:10px}"
  ].join("");

  function fmt(n) { return Number(n || 0).toLocaleString("en-US"); }

  var panel = null, scrim = null, scrollEl = null;

  function ensurePanel() {
    if (panel) return;
    var st = document.createElement("style");
    st.id = "og-r85-css";
    st.textContent = CSS;
    document.head.appendChild(st);
    panel = document.createElement("section");
    panel.id = "ogPlansPanel";
    panel.className = "og-plans-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Plans");
    panel.hidden = true;
    panel.innerHTML =
      '<div class="og-plans-top"><h2>Plans</h2>' +
      '<button type="button" class="og-circle-btn" ' +
      'id="ogPlansClose" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg></button></div>' +
      '<div class="og-plans-scroll" id="ogPlansScroll"></div>';
    document.body.appendChild(panel);
    scrim = document.createElement("div");
    scrim.id = "ogPlansScrim";
    scrim.className = "og-plans-scrim";
    scrim.hidden = true;
    document.body.appendChild(scrim);
    scrollEl = panel.querySelector("#ogPlansScroll");
    panel.querySelector("#ogPlansClose")
      .addEventListener("click", close);
    scrim.addEventListener("click", close);
  }

  function close() {
    if (!panel) return;
    panel.classList.remove("open");
    scrim.hidden = true;
    setTimeout(function () { panel.hidden = true; }, 260);
  }

  function open() {
    ensurePanel();
    panel.hidden = false;
    scrim.hidden = false;
    requestAnimationFrame(function () {
      panel.classList.add("open");
    });
    scrollEl.innerHTML =
      '<p class="og-plans-note">Loading the plans…</p>';
    load();
  }

  function load() {
    var tiersP = fetch("/tiers", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; });
    var histP = fetch("/history", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
    Promise.all([tiersP, histP]).then(function (res) {
      var data = res[0];
      if (!data || !data.tiers) { renderError(); return; }
      var current = res[1] && res[1].tier ? res[1].tier : null;
      render(data.tiers, current);
    }).catch(renderError);
  }

  function renderError() {
    if (!scrollEl) return;
    scrollEl.innerHTML =
      '<p class="og-plans-note">The plans didn\'t load — ' +
      'OG didn\'t go anywhere, it\'s just this list.</p>' +
      '<button type="button" class="og-plans-retry" ' +
      'id="ogPlansRetry">Try again</button>';
    var b = scrollEl.querySelector("#ogPlansRetry");
    if (b) b.addEventListener("click", load);
  }

  function facts(t) {
    var caps = t.weekly_caps || {};
    var out = [
      "Chat — " + fmt(t.chat_tokens_week) + " tokens a week",
      fmt(caps.images) + " images a week",
      fmt(caps.video) + " story videos a week",
      fmt(caps.song) + " songs a week",
      fmt(caps.tts) + " voice replies a week"
    ];
    if (t.storage_bytes > 0) {
      out.push(t.storage_gb + " GB file storage");
    }
    if (t.browser_min > 0) {
      out.push("OG's browser — " + fmt(t.browser_min) +
        " minutes a day");
    }
    return out;
  }

  function render(tiers, current) {
    var html = "";
    tiers.forEach(function (t) {
      var isCurrent = current && t.tier === current;
      html += '<section class="og-plans-card' +
        (isCurrent ? " current" : "") + '">';
      html += '<p class="og-plans-name">' + (t.badge || "") +
        " " + (t.name || t.tier) + "</p>";
      if (t.price > 0) {
        html += '<p class="og-plans-price">$' +
          fmt(t.price) + " <span>/month</span></p>";
      } else {
        html += '<p class="og-plans-price">Free</p>';
      }
      html += '<ul class="og-plans-facts">' +
        facts(t).map(function (f) {
          return "<li>" + f + "</li>";
        }).join("") + "</ul>";
      if (isCurrent) {
        html += '<span class="og-plans-current">Your plan</span>';
      } else if (t.price > 0 && t.checkout_open && LINKS[t.tier]) {
        html += '<a class="og-plans-buy" href="' + LINKS[t.tier] +
          '">Get ' + (t.name || t.tier) + " →</a>";
      } else if (t.price > 0) {
        html += '<span class="og-plans-off">Not open yet</span>';
      }
      html += "</section>";
    });
    html += '<p class="og-plans-foot"><a href="/pro">Full plans ' +
      'page</a> — the long version, same plans and prices.</p>';
    scrollEl.innerHTML = html;
  }

  // The card wiring: any click inside the menu's plan card
  // opens the view. Clicks on the tokens card (a sibling
  // section inside the same row) are NOT inside
  // .og-plan-card and keep their own behavior. The Change-
  // plan anchor keeps href="/pro" as the no-JS fallback;
  // with this asset loaded its navigation is replaced by
  // the in-shell view.
  document.addEventListener("click", function (e) {
    var card = e.target && e.target.closest
      ? e.target.closest(".og-plan-card") : null;
    if (!card) return;
    e.preventDefault();
    open();
  });
})();

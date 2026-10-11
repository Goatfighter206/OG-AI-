/* Wallet Phase A (tokenize, DARK): the Connections Wallet row.
   This asset lives in its own file and loads via a loader stub
   in static/og_tokens.js — the page is at its push ceiling.

   GATE-OFF = INVISIBLE: the row is injected into the
   Connections panel ONLY after GET /wallet/status answers
   enabled:true. While the wallet is dark (or unreachable),
   nothing is added to the panel at all.

   States: not connected ("Connect a payment method" -> POST
   /wallet/setup -> the Stripe-hosted setup page; OG's pages
   never render a card field), connected (brand + last4 +
   expiry, "Remove" = the revoke: detach at Stripe + wipe,
   same request), and a History view of wallet_charges.

   Capacitor branch (plan §5/§6 posture): inside the app
   shells (window.Capacitor present) purchasing UI is
   STATUS-ONLY — the row shows whether a method is connected
   and points at the web to manage it; no Connect/Remove
   controls are rendered. */
(function () {
  "use strict";

  var inCapacitor = !!window.Capacitor;

  function $(sel, root) { return (root || document).querySelector(sel); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function fmtMoney(cents) {
    return "$" + (Number(cents || 0) / 100).toFixed(2);
  }

  function buildSection() {
    var scroll = $(".og-connections-scroll");
    if (!scroll || document.getElementById("ogWalletSection")) return null;
    var head = el("h3", "og-connections-sec", "Wallet");
    head.id = "ogWalletSection";
    var card = el("div", "og-connections-card og-connections-formcard");
    card.id = "ogWalletCard";
    scroll.appendChild(head);
    scroll.appendChild(card);
    return card;
  }

  function paint(card, s) {
    card.innerHTML = "";
    var msg = el("p", "og-connections-note", "");
    msg.id = "ogWalletMessage";
    msg.setAttribute("aria-live", "polite");

    if (s.connected) {
      var line = "💳 " + (s.brand || "Card") + " •••• " + (s.last4 || "····");
      if (s.exp) line += " — expires " + s.exp;
      card.appendChild(el("p", "og-connections-copy", line));
      card.appendChild(el("p", "og-connections-note",
        "Charged today: " + fmtMoney(s.charges_today_cents) +
        " · per-charge limit " + fmtMoney(s.ceiling_cents) +
        " · daily limit " + fmtMoney(s.daily_ceiling_cents) +
        ". OG charges this card only after you approve each " +
        "charge's preview."));
      var histBox = el("div", "og-connections-logins");
      histBox.id = "ogWalletHistory";
      histBox.hidden = true;
      var histBtn = el("button", "og-connections-primary", "History");
      histBtn.type = "button";
      histBtn.addEventListener("click", function () {
        if (!histBox.hidden) { histBox.hidden = true; return; }
        histBox.hidden = false;
        histBox.innerHTML = "";
        fetch("/wallet/history").then(function (r) { return r.json(); })
          .then(function (d) {
            var charges = (d && d.charges) || [];
            if (!charges.length) {
              histBox.appendChild(el("p", "og-connections-copy",
                "No wallet charges yet."));
              return;
            }
            charges.forEach(function (c) {
              var when = c.ts ? new Date(c.ts * 1000).toISOString().slice(0, 10) : "";
              var row = el("div", "og-connections-login-row");
              row.appendChild(el("span", "",
                (c.description || c.merchant || "Charge") + " — " +
                fmtMoney(c.amount_cents) + " · " + (c.status || "") +
                (when ? " · " + when : "")));
              histBox.appendChild(row);
            });
          }).catch(function () {
            histBox.appendChild(el("p", "og-connections-copy",
              "History is unavailable right now."));
          });
      });
      card.appendChild(histBtn);
      card.appendChild(histBox);
      if (inCapacitor) {
        card.appendChild(el("p", "og-connections-note",
          "Manage your payment method on the web — in the app " +
          "this row is status-only."));
      } else {
        var rm = el("button", "og-connections-danger",
          "Remove payment method");
        rm.type = "button";
        rm.addEventListener("click", function () {
          if (!window.confirm("Remove your payment method? It is " +
              "detached at Stripe and OG's record is wiped — no " +
              "wallet charge can be made after this.")) return;
          fetch("/wallet/remove", { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.ok) { refresh(); }
              else {
                msg.textContent = "Remove didn't go through — " +
                  "the wallet is unavailable right now. Try again.";
              }
            }).catch(function () {
              msg.textContent = "Remove didn't go through — " +
                "the wallet is unavailable right now. Try again.";
            });
        });
        card.appendChild(rm);
      }
    } else {
      card.appendChild(el("p", "og-connections-copy",
        "Connect a payment method once, and OG can pay for " +
        "things you approve — each charge is previewed and " +
        "approved by you first, never charged silently."));
      if (inCapacitor) {
        card.appendChild(el("p", "og-connections-note",
          "Connect a payment method on the web — in the app " +
          "this row is status-only."));
      } else {
        var btn = el("button", "og-connections-primary",
          "Connect a payment method");
        btn.type = "button";
        btn.addEventListener("click", function () {
          btn.disabled = true;
          fetch("/wallet/setup", { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.url) { window.location.href = d.url; return; }
              if (d && d.connected) { refresh(); return; }
              btn.disabled = false;
              msg.textContent = "Wallet setup is unavailable " +
                "right now. Try again in a bit.";
            }).catch(function () {
              btn.disabled = false;
              msg.textContent = "Wallet setup is unavailable " +
                "right now. Try again in a bit.";
            });
        });
        card.appendChild(btn);
      }
      card.appendChild(el("p", "og-connections-note",
        "Card entry happens on Stripe's hosted page — OG never " +
        "sees or stores your card number. Per-charge limit " +
        fmtMoney(s.ceiling_cents) + ", daily limit " +
        fmtMoney(s.daily_ceiling_cents) + " on your plan."));
    }
    card.appendChild(msg);
  }

  function refresh() {
    fetch("/wallet/status").then(function (r) { return r.json(); })
      .then(function (s) {
        if (!s || !s.enabled) return;  // dark: stay invisible
        var card = document.getElementById("ogWalletCard") || buildSection();
        if (card) paint(card, s);
      }).catch(function () { /* dark/unreachable: stay invisible */ });
  }

  var openBtn = document.getElementById("menuConnectionsBtn");
  if (openBtn) openBtn.addEventListener("click", refresh);
  refresh();
  window.ogRefreshWallet = refresh;
})();

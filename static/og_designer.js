/* Generator 7b: the designer — thumbnail / poster / meme.
   Two doors, one core (POST /designer/generate):
   1. It wraps sendMessage OUTSIDE the page's generic /image
      wrapper (this asset loads last, via the og_tokens.js
      ride-along loader), so a designer ask ("make a poster
      for...", "make a meme: ... / top: ... / bottom: ...")
      is claimed HERE and never falls through to a plain
      text-less picture. The result renders in-chat exactly
      like an /image result: caption, the picture itself, and
      a download link.
   2. A status card polls GET /designer/status, so a design
      made through the /chat claim seam (or another client)
      also surfaces here with its picture + download.
   The visitor's words are burned in server-side (PIL) —
   this file never asks an image model to render text. */
(function () {
  "use strict";

  var KIND = /\b(thumbnail|poster|meme)\b/i;
  var VERB = /\b(make|create|design|generate|build|draw|render|produce)\b/i;
  var QUESTION = /^\s*(what|why|how|when|where|who)\b[^.?!]{0,60}\?/i;

  function wantsDesign(msg) {
    if (!msg || QUESTION.test(msg)) return false;
    var m = KIND.exec(msg);
    if (!m) return false;
    var head = msg.slice(0, m.index + 1);
    if (VERB.test(head)) return true;
    if (/\bi (want|need)\b|\bcan you\b|\bcould you\b/i.test(head)) return true;
    return /^\s*(thumbnail|poster|meme)\s*[:\-]/i.test(msg);
  }

  function addDesignMessage(data) {
    var area = document.getElementById("messagesArea");
    if (!area) return;
    var div = document.createElement("div");
    div.className = "message ai";
    div.innerHTML =
      '<div class="message-content"><div class="message-text"></div>' +
      '<div class="message-time">' +
      new Date().toLocaleTimeString() + "</div></div>";
    var textEl = div.querySelector(".message-text");
    var cap = document.createElement("div");
    cap.textContent = data.response || "🎨 Design — done.";
    textEl.appendChild(cap);
    var img = document.createElement("img");
    img.className = "og-gen-image";
    img.alt = "OG design: " + (data.name || data.design_kind || "");
    img.src = data.image || data.design_url || "";
    img.addEventListener("load", function () {
      if (typeof ogFollowBottom === "function") ogFollowBottom(false);
    });
    textEl.appendChild(img);
    var a = document.createElement("a");
    a.className = "og-img-download";
    a.href = data.image || data.download_url || data.design_url || "#";
    a.setAttribute("download", data.name || "og-design.png");
    a.textContent = "⬇ Download design";
    textEl.appendChild(a);
    if (data.saved) {
      var s = document.createElement("div");
      s.textContent = "Saved to your Library — Images ✓";
      textEl.appendChild(s);
    }
    area.appendChild(div);
    if (typeof ogFollowBottom === "function") ogFollowBottom(false);
  }

  function ogMakeDesign(message) {
    var input = document.getElementById("messageInput");
    if (typeof addMessage === "function") addMessage("user", message);
    if (input) {
      input.value = "";
      input.dispatchEvent(new Event("input"));
    }
    if (typeof isProcessing !== "undefined") isProcessing = true;
    if (typeof updateStatus === "function") updateStatus("OG is designing...");
    if (typeof setMood === "function") setMood("talking");
    var area = document.getElementById("messagesArea");
    var bubble = document.createElement("div");
    bubble.className = "message ai";
    bubble.innerHTML =
      '<div class="message-content"><div class="message-text">' +
      '<span class="og-drawing-pulse">🎨 Designing that up for you…</span>' +
      '</div><div class="message-time">' +
      new Date().toLocaleTimeString() + "</div></div>";
    if (area) area.appendChild(bubble);
    if (typeof ogFollowBottom === "function") ogFollowBottom(false);
    return fetch("/designer/generate", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: message })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        bubble.remove();
        if (data && data.ok && (data.image || data.design_url)) {
          dismissed = data.id || dismissed;  // card must not re-show it
          addDesignMessage(data);
          if (typeof speakReply === "function") {
            speakReply(data.response || "");
          }
        } else if (typeof addMessage === "function") {
          addMessage("ai", (data && (data.response || data.error)) ||
            "Yo, the design bench glitched — hit me again in a sec.");
        }
      }).catch(function () {
        bubble.remove();
        if (typeof addMessage === "function") {
          addMessage("ai",
            "Yo, the design bench glitched — hit me again in a sec.");
        }
      }).finally(function () {
        if (typeof isProcessing !== "undefined") isProcessing = false;
        if (typeof updateStatus === "function") {
          updateStatus("Online & Ready");
        }
        if (typeof setMood === "function") setMood("");
      });
  }

  // Wrap sendMessage (retrying until the page's own chain
  // exists); designer phrasing wins, everything else
  // delegates down the chain untouched.
  var wrapped = false;
  function tryWrap() {
    if (wrapped || typeof sendMessage !== "function") return;
    var prevSend = sendMessage;
    sendMessage = function () {
      var input = document.getElementById("messageInput");
      var msg = input ? input.value.trim() : "";
      if (msg && !(typeof isProcessing !== "undefined" && isProcessing) &&
          wantsDesign(msg)) {
        return ogMakeDesign(msg);
      }
      return prevSend.apply(this, arguments);
    };
    wrapped = true;
  }
  tryWrap();
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    tryWrap();
    if (wrapped || tries > 20) clearInterval(iv);
  }, 1000);

  /* --- Status card (designs made via the /chat seam) --- */
  var css = document.createElement("style");
  css.textContent =
    "#ogDesignerCard{position:fixed;right:12px;bottom:92px;z-index:2147482000;" +
    "width:min(92vw,380px);background:#101010;color:#f5eeda;" +
    "border:1px solid #c9a227;border-radius:14px;padding:12px 14px;" +
    "font:14px/1.45 system-ui,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)}" +
    "#ogDesignerCard h3{margin:0 24px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogDesignerCard img{width:100%;border-radius:10px;margin:6px 0}" +
    "#ogDesignerCard .ogdc-meta{opacity:.85;font-size:12.5px}" +
    "#ogDesignerCard .ogdc-btns{display:flex;gap:8px;margin-top:8px}" +
    "#ogDesignerCard a.ogdc-dl{background:#c9a227;color:#000;" +
    "border-radius:9px;padding:8px 12px;font-weight:700;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogDesignerCard .ogdc-x{position:absolute;top:6px;right:8px;" +
    "background:none;border:0;color:#f5eeda;font-size:16px;cursor:pointer}";
  document.head.appendChild(css);

  var card = document.createElement("section");
  card.id = "ogDesignerCard";
  card.hidden = true;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Designer");
  document.body.appendChild(card);
  var bootTime = Date.now() / 1000;
  var dismissed = "";

  function hide() { card.hidden = true; card.innerHTML = ""; }

  function poll() {
    fetch("/designer/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.enabled === false || !d.job) return hide();
        var j = d.job;
        // Only surface designs made this session via the
        // seam — the generate door already renders its own
        // result inline (and dismisses its id above), and
        // older jobs live in the Library Images tab.
        if (dismissed === j.id) return hide();
        if (j.created && j.created < bootTime - 5) return hide();
        card.innerHTML = "";
        var x = document.createElement("button");
        x.className = "ogdc-x"; x.textContent = "✕";
        x.addEventListener("click", function () {
          dismissed = j.id; hide();
        });
        card.appendChild(x);
        var h = document.createElement("h3");
        h.textContent = "🎨 Your design is ready";
        card.appendChild(h);
        var img = document.createElement("img");
        img.src = j.design_url; img.alt = j.name || "OG design";
        card.appendChild(img);
        var meta = document.createElement("div");
        meta.className = "ogdc-meta";
        meta.textContent = (j.name || "") +
          (j.saved ? " · saved to Library Images ✓"
                   : " · download within 24 hours");
        card.appendChild(meta);
        var btns = document.createElement("div");
        btns.className = "ogdc-btns";
        var dl = document.createElement("a");
        dl.className = "ogdc-dl"; dl.href = j.download_url;
        dl.setAttribute("download", "");
        dl.textContent = "⬇ Download";
        btns.appendChild(dl);
        card.appendChild(btns);
        card.hidden = false;
      })
      .catch(function () { /* a failed poll just waits */ });
  }
  poll();
  setInterval(poll, 5000);
})();

/* Generator 7d: the motion-video card — pending approval
   (prompt + duration/ratio + estimated provider cost +
   Approve/Decline doors riding POST /motion/approve and
   /motion/decline), the rendering state, and the done state
   with a real <video> player + download + locker save.
   Polls GET /motion/status. While motion is dark the status
   answers enabled:false and this card never appears. Lives
   in its own asset: the page is at its push ceiling, so this
   loads via a loader stub in static/og_tokens.js, and all
   of its CSS is injected from here. */
(function () {
  "use strict";
  var dismissedJob = "", dismissedPending = "";
  var css = document.createElement("style");
  css.textContent =
    "#ogMotionCard{position:fixed;right:12px;bottom:92px;z-index:2147482000;" +
    "width:min(92vw,380px);background:#101010;color:#f5eeda;border:1px solid #c9a227;" +
    "border-radius:14px;padding:12px 14px;font:14px/1.45 system-ui,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)}" +
    "#ogMotionCard h3{margin:0 54px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogMotionCard .ogmc-meta{opacity:.85;font-size:12.5px;margin-bottom:6px}" +
    "#ogMotionCard video{width:100%;margin:8px 0 4px;border-radius:8px;background:#000}" +
    "#ogMotionCard .ogmc-btns{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}" +
    "#ogMotionCard button,#ogMotionCard a.ogmc-dl{background:#c9a227;color:#000;" +
    "border:0;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogMotionCard button.ogmc-no{background:#2a2a2a;color:#f5eeda;" +
    "border:1px solid #555}" +
    "#ogMotionCard .ogmc-x{position:absolute;top:8px;right:8px;width:42px;height:42px;" +
    "border-radius:50%;background:#1b1b1b;border:1px solid #333;color:#e8e8e8;" +
    "display:grid;place-items:center;cursor:pointer;padding:0}" +
    "#ogMotionCard .ogmc-x svg{width:21px;height:21px}" +
    "#ogMotionCard .ogmc-x:hover{border-color:rgba(255,193,7,.7);color:#ffc107}" +
    /* Round 106: the locker save is an icon at the card's
       top right, left of the canonical x (Brent: icon only). */
    "##ogMotionCard button.ogmc-save{position:absolute;top:8px;right:58px;" +
    "width:42px;height:42px;border-radius:50%;background:#1b1b1b;" +
    "border:1px solid #333;color:#e8e8e8;display:grid;place-items:center;" +
    "cursor:pointer;padding:0}" +
    "##ogMotionCard .ogmc-save svg{width:21px;height:21px;fill:none;" +
    "stroke:currentColor;stroke-width:2;stroke-linecap:round;" +
    "stroke-linejoin:round}" +
    "##ogMotionCard button.ogmc-save:hover{border-color:rgba(255,193,7,.7);" +
    "color:#ffc107}" +
    "##ogMotionCard button.ogmc-save.is-saved{background:linear-gradient(" +
    "135deg,#ffc107,#ff8f00);color:#151515;border-color:transparent}" +
    "##ogMotionCard button.ogmc-save.is-saved svg{fill:currentColor}" +
    "##ogMotionCard .ogmc-savenote{color:#ff9c9c;font-size:12.5px;" +
    "align-self:center}";
  document.head.appendChild(css);

  var card = document.createElement("section");
  card.id = "ogMotionCard";
  card.hidden = true;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Motion video");
  document.body.appendChild(card);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function post(path) {
    return fetch(path, { method: "POST", credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }); });
  }
  function hide() { card.hidden = true; card.innerHTML = ""; }
  function head(title) {
    card.innerHTML = "";
    var x = el("button", "ogmc-x");
    x.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
    x.setAttribute("aria-label", "Close");
    x.addEventListener("click", function () {
      var j = card.getAttribute("data-job");
      var p = card.getAttribute("data-pending");
      if (j) dismissedJob = j;
      if (p) dismissedPending = p;
      hide();
    });
    card.appendChild(x);
    card.appendChild(el("h3", null, title));
  }

  function renderPending(p) {
    var key = p.prompt || "pending";
    card.setAttribute("data-pending", key);
    card.removeAttribute("data-job");
    if (dismissedPending === key && p.stage === "await_approval") return hide();
    head("🎞️ Motion video — approve the render");
    card.appendChild(el("div", null, p.prompt || "Motion clip"));
    var meta = [];
    if (p.duration) meta.push(p.duration + " seconds");
    if (p.ratio) meta.push(p.ratio);
    if (p.est_cost != null) meta.push("est. $" + Number(p.est_cost).toFixed(2));
    card.appendChild(el("div", "ogmc-meta", meta.join(" · ")));
    if (p.stage !== "await_approval") {
      card.appendChild(el("div", "ogmc-meta",
        "OG is shaping the prompt — the approval lands here when it's ready."));
      card.hidden = false;
      return;
    }
    var btns = el("div", "ogmc-btns");
    var yes = el("button", null, "✅ Render it");
    var no = el("button", "ogmc-no", "No — scrap it");
    yes.addEventListener("click", function () {
      yes.disabled = true;
      post("/motion/approve").then(function () { poll(); });
    });
    no.addEventListener("click", function () {
      no.disabled = true;
      post("/motion/decline").then(function () {
        dismissedPending = key; hide();
      });
    });
    btns.appendChild(yes); btns.appendChild(no);
    card.appendChild(btns);
    card.hidden = false;
  }

  function renderJob(j) {
    card.setAttribute("data-job", j.id || "");
    card.removeAttribute("data-pending");
    if (j.state === "done") {
      if (dismissedJob === j.id) return hide();
      head("🎞️ Your motion clip is ready");
      card.appendChild(el("div", null, j.title || "Motion clip"));
      if (j.detail) card.appendChild(el("div", "ogmc-meta", j.detail));
      var video = document.createElement("video");
      video.controls = true;
      video.src = j.motion_url || ("/motion/download/" + j.id);
      video.preload = "metadata";
      card.appendChild(video);
      var btns = el("div", "ogmc-btns");
      var dl = el("a", "ogmc-dl", "⬇ Download");
      dl.href = video.src;
      dl.setAttribute("download", "");
      btns.appendChild(dl);
      var snote = el("span", "ogmc-savenote", "");
      btns.appendChild(snote);
      card.appendChild(btns);
      if (!j.saved) {
        /* Round 106: the save rides the same POST, but it
           is an icon now — the folder glyph at the card's
           top right, left of the x. Saved = the glyph
           fills gold; a failure keeps the old words, on
           the note line + the icon's label. */
        var save = el("button", "ogmc-save");
        save.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
        save.setAttribute("aria-label", "Save to my locker");
        save.setAttribute("title", "Save to my locker");
        save.addEventListener("click", function () {
          save.disabled = true;
          post("/motion/save/" + j.id).then(function (d) {
            if (d && d.ok) {
              save.classList.add("is-saved");
              save.setAttribute("aria-label", "Saved");
              save.setAttribute("title", "Saved");
            } else {
              snote.textContent = "Locker said no";
              save.setAttribute("aria-label", "Locker said no");
              save.setAttribute("title", "Locker said no");
            }
          });
        });
        card.appendChild(save);
        card.querySelector("h3").style.marginRight = "108px";
      }
      card.hidden = false;
      return;
    }
    if (j.state === "failed") {
      if (dismissedJob === j.id) return hide();
      head("🎞️ Motion clip didn't finish");
      card.appendChild(el("div", "ogmc-meta",
        j.detail || "That render didn't make it — nothing was charged."));
      card.hidden = false;
      return;
    }
    head("🎞️ Rendering your motion clip…");
    card.appendChild(el("div", "ogmc-meta",
      j.detail || "In the studio — rendering real motion…"));
    card.hidden = false;
  }

  function poll() {
    fetch("/motion/status", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.enabled) return hide();
        if (d.pending) return renderPending(d.pending);
        if (d.job) return renderJob(d.job);
        hide();
      })
      .catch(function () { /* card stays as-is */ });
  }
  poll();
  setInterval(poll, 5000);
})();

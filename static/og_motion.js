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
    "#ogMotionCard h3{margin:0 24px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogMotionCard .ogmc-meta{opacity:.85;font-size:12.5px;margin-bottom:6px}" +
    "#ogMotionCard video{width:100%;margin:8px 0 4px;border-radius:8px;background:#000}" +
    "#ogMotionCard .ogmc-btns{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}" +
    "#ogMotionCard button,#ogMotionCard a.ogmc-dl{background:#c9a227;color:#000;" +
    "border:0;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogMotionCard button.ogmc-no{background:#2a2a2a;color:#f5eeda;" +
    "border:1px solid #555}" +
    "#ogMotionCard .ogmc-x{position:absolute;top:6px;right:8px;background:none;" +
    "border:0;color:#f5eeda;font-size:16px;cursor:pointer;padding:4px}";
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
    var x = el("button", "ogmc-x", "✕");
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
      if (!j.saved) {
        var save = el("button", null, "Save to my locker");
        save.addEventListener("click", function () {
          save.disabled = true;
          post("/motion/save/" + j.id).then(function (d) {
            save.textContent = d && d.ok ? "Saved ✓" : "Locker said no";
          });
        });
        btns.appendChild(save);
      }
      card.appendChild(btns);
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

/* Generator 7a: the podcast card — pending-script approval
   (script preview, line count, estimated minutes, the two
   voices, Approve/Decline doors riding POST /podcast/approve
   and /podcast/decline), the generating state, and the done
   state with a real <audio> player + download + locker save.
   Polls GET /podcast/status, exactly like the page's own
   video/song cards poll theirs. Lives in its own asset: the
   page is at its push ceiling, so this loads via a loader
   stub in static/og_tokens.js (the R62/R63/R65/R66 pattern),
   and all of its CSS is injected from here. */
(function () {
  "use strict";
  var dismissedJob = "", dismissedPending = "";
  var css = document.createElement("style");
  css.textContent =
    "#ogPodcastCard{position:fixed;right:12px;bottom:92px;z-index:2147482000;" +
    "width:min(92vw,380px);background:#101010;color:#f5eeda;border:1px solid #c9a227;" +
    "border-radius:14px;padding:12px 14px;font:14px/1.45 system-ui,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)}" +
    "#ogPodcastCard h3{margin:0 24px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogPodcastCard .ogpc-meta{opacity:.85;font-size:12.5px;margin-bottom:6px}" +
    "#ogPodcastCard pre{white-space:pre-wrap;max-height:110px;overflow:auto;" +
    "background:#000;border-radius:8px;padding:8px;font-size:12px;margin:6px 0}" +
    "#ogPodcastCard audio{width:100%;margin:8px 0 4px}" +
    "#ogPodcastCard .ogpc-btns{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}" +
    "#ogPodcastCard button,#ogPodcastCard a.ogpc-dl{background:#c9a227;color:#000;" +
    "border:0;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogPodcastCard button.ogpc-no{background:#2a2a2a;color:#f5eeda;" +
    "border:1px solid #555}" +
    "#ogPodcastCard .ogpc-x{position:absolute;top:6px;right:8px;background:none;" +
    "border:0;color:#f5eeda;font-size:16px;cursor:pointer;padding:4px}" +
    "#ogPodcastCard .ogpc-err{color:#ff9c9c;font-size:12.5px}";
  document.head.appendChild(css);

  var card = document.createElement("section");
  card.id = "ogPodcastCard";
  card.hidden = true;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Podcast");
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
    var x = el("button", "ogpc-x", "✕");
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
    card.setAttribute("data-pending", p.title || "pending");
    card.removeAttribute("data-job");
    if (dismissedPending === (p.title || "pending") &&
        p.stage === "await_approval") return hide();
    head("🎙️ Podcast — approve the script");
    card.appendChild(el("div", null, p.title || "Untitled episode"));
    var meta = [];
    if (p.lines) meta.push(p.lines + " lines");
    if (p.est_min) meta.push("~" + p.est_min + " min");
    if (p.voices && p.voices.length)
      meta.push("Host 1: " + p.voices[0] + " · Host 2: " + p.voices[1]);
    if (p.tts_est) meta.push("~" + p.tts_est + " voice calls");
    card.appendChild(el("div", "ogpc-meta", meta.join(" · ")));
    if (p.preview) card.appendChild(el("pre", null, p.preview));
    if (p.stage !== "await_approval") {
      card.appendChild(el("div", "ogpc-meta",
        "OG is writing the script — the approval lands here when it's ready."));
      card.hidden = false;
      return;
    }
    var btns = el("div", "ogpc-btns");
    var yes = el("button", null, "✅ Voice it");
    var no = el("button", "ogpc-no", "No — scrap it");
    yes.addEventListener("click", function () {
      yes.disabled = true;
      post("/podcast/approve").then(function () { poll(); });
    });
    no.addEventListener("click", function () {
      no.disabled = true;
      post("/podcast/decline").then(function () {
        dismissedPending = p.title || "pending"; hide();
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
      head("🎙️ Your podcast is ready");
      card.appendChild(el("div", null, j.title || "Podcast"));
      if (j.detail) card.appendChild(el("div", "ogpc-meta", j.detail));
      var audio = document.createElement("audio");
      audio.controls = true;
      audio.src = j.podcast_url || ("/podcast/download/" + j.id);
      audio.preload = "metadata";
      card.appendChild(audio);
      var btns = el("div", "ogpc-btns");
      var dl = el("a", "ogpc-dl", "⬇ Download");
      dl.href = audio.src; dl.setAttribute("download", "");
      btns.appendChild(dl);
      var save = el("button", "ogpc-no", j.saved ? "Saved ✓" : "Save to locker");
      save.disabled = !!j.saved;
      save.addEventListener("click", function () {
        save.disabled = true;
        post("/podcast/save/" + j.id).then(function (d) {
          save.textContent = d && d.ok ? "Saved ✓"
            : (d && d.error ? "Save — " + d.error : "Save failed");
          if (!(d && d.ok)) save.disabled = false;
        });
      });
      btns.appendChild(save);
      card.appendChild(btns);
      card.hidden = false;
      return;
    }
    if (j.state === "failed") {
      if (dismissedJob === j.id) return hide();
      head("🎙️ Podcast didn't finish");
      card.appendChild(el("div", "ogpc-err", j.detail || "That take didn't make it."));
      card.hidden = false;
      return;
    }
    head("🎙️ Podcast — in the booth");
    card.appendChild(el("div", null, j.title || "Podcast"));
    card.appendChild(el("div", "ogpc-meta",
      j.detail || "The hosts are recording…"));
    card.hidden = false;
  }

  var live = true;
  function poll() {
    if (!live) return;
    fetch("/podcast/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.enabled === false) { live = false; hide(); return; }
        if (d.pending) return renderPending(d.pending);
        if (d.job) return renderJob(d.job);
        hide();
      })
      .catch(function () { /* a failed poll just waits for the next */ });
  }
  poll();
  setInterval(poll, 4000);
})();

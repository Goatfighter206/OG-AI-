/* Generator 7c: the book card + reader — pending-outline
   approval (title, premise, chapter list, Approve/Decline
   doors riding POST /books/approve and /books/decline),
   chapter-by-chapter progress lines as chapters land
   ("Chapter 3 of 8 done"), and the done state with Read
   (the reader overlay: chapter list -> chapter text, from
   GET /books/<id>/read) + the .md download. Polls GET
   /books/status, exactly like the podcast card polls its
   own. Lives in its own asset: the page is at its push
   ceiling, so this loads via a loader stub in
   static/og_tokens.js (the 7a/7b pattern), and all of its
   CSS is injected from here. Library "Read" links
   (/books/<id>/read) are intercepted into the same reader. */
(function () {
  "use strict";
  var dismissedJob = "", dismissedPending = "";
  var css = document.createElement("style");
  css.textContent =
    "#ogBookCard{position:fixed;right:12px;bottom:92px;z-index:2147482000;" +
    "width:min(92vw,380px);background:#101010;color:#f5eeda;border:1px solid #c9a227;" +
    "border-radius:14px;padding:12px 14px;font:14px/1.45 system-ui,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)}" +
    "#ogBookCard h3{margin:0 24px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogBookCard .ogbk-meta{opacity:.85;font-size:12.5px;margin-bottom:6px}" +
    "#ogBookCard .ogbk-chaps{max-height:130px;overflow:auto;background:#000;" +
    "border-radius:8px;padding:8px;font-size:12.5px;margin:6px 0}" +
    "#ogBookCard .ogbk-btns{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}" +
    "#ogBookCard button,#ogBookCard a.ogbk-dl{background:#c9a227;color:#000;" +
    "border:0;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogBookCard button.ogbk-no{background:#2a2a2a;color:#f5eeda;" +
    "border:1px solid #555}" +
    "#ogBookCard .ogbk-x{position:absolute;top:6px;right:8px;background:none;" +
    "border:0;color:#f5eeda;font-size:16px;cursor:pointer;padding:4px}" +
    "#ogBookCard .ogbk-err{color:#ff9c9c;font-size:12.5px}" +
    "#ogBookReader{position:fixed;inset:0;z-index:2147483000;background:#0b0b0b;" +
    "color:#f5eeda;font:15px/1.6 system-ui,sans-serif;display:flex;flex-direction:column}" +
    "#ogBookReader .ogbkr-head{display:flex;align-items:center;gap:10px;" +
    "padding:12px 16px;border-bottom:1px solid #333}" +
    "#ogBookReader .ogbkr-head h3{margin:0;font-size:17px;color:#e8c766;flex:1}" +
    "#ogBookReader .ogbkr-body{display:flex;flex:1;min-height:0}" +
    "#ogBookReader .ogbkr-list{width:230px;overflow:auto;border-right:1px solid #333;padding:10px}" +
    "#ogBookReader .ogbkr-list button{display:block;width:100%;text-align:left;" +
    "background:none;border:0;color:#f5eeda;padding:7px 8px;border-radius:7px;cursor:pointer}" +
    "#ogBookReader .ogbkr-list button.is-on{background:#2a2a2a;color:#e8c766}" +
    "#ogBookReader .ogbkr-text{flex:1;overflow:auto;padding:16px 20px;white-space:pre-wrap}" +
    "#ogBookReader img.ogbkr-cover{max-width:180px;border-radius:8px;margin:0 0 12px}" +
    "#ogBookReader button.ogbkr-x{background:#2a2a2a;color:#f5eeda;border:1px solid #555;" +
    "border-radius:9px;padding:7px 12px;cursor:pointer;font-weight:700}";
  document.head.appendChild(css);

  var card = document.createElement("section");
  card.id = "ogBookCard";
  card.hidden = true;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Book");
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
    var x = el("button", "ogbk-x", "✕");
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

  /* --- Reader overlay --------------------------------------- */
  function openReader(id) {
    fetch("/books/" + id + "/read", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var old = document.getElementById("ogBookReader");
        if (old) old.remove();
        var wrap = el("section");
        wrap.id = "ogBookReader";
        var hd = el("div", "ogbkr-head");
        hd.appendChild(el("h3", null, d.title || "Book"));
        var dl = el("a", null, "⬇ .md");
        dl.href = "/books/" + id + "/download";
        dl.setAttribute("download", "");
        hd.appendChild(dl);
        var x = el("button", "ogbkr-x", "✕ Close");
        x.addEventListener("click", function () { wrap.remove(); });
        hd.appendChild(x);
        wrap.appendChild(hd);
        var body = el("div", "ogbkr-body");
        var list = el("div", "ogbkr-list");
        var text = el("div", "ogbkr-text");
        if (d.cover_url) {
          var img = document.createElement("img");
          img.className = "ogbkr-cover";
          img.src = d.cover_url;
          img.alt = "Cover";
          text.appendChild(img);
        }
        if (d.premise) text.appendChild(el("div", null, d.premise));
        (d.chapters || []).forEach(function (ch, i) {
          var b = el("button", i === 0 ? "is-on" : null,
            "Chapter " + ch.n + ": " + (ch.title || ""));
          b.addEventListener("click", function () {
            list.querySelectorAll("button").forEach(function (o) {
              o.classList.remove("is-on");
            });
            b.classList.add("is-on");
            text.textContent = ch.text || "";
          });
          list.appendChild(b);
        });
        if (d.chapters && d.chapters.length)
          text.textContent = d.chapters[0].text || "";
        body.appendChild(list);
        body.appendChild(text);
        wrap.appendChild(body);
        document.body.appendChild(wrap);
      })
      .catch(function () { /* reader failed — the download still works */ });
  }
  window.ogOpenBook = openReader;
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest
      ? e.target.closest('a[href^="/books/"]') : null;
    if (!a) return;
    var m = (a.getAttribute("href") || "").match(/^\/books\/([0-9a-f]+)\/read$/);
    if (!m) return;
    e.preventDefault();
    openReader(m[1]);
  });

  function renderPending(p) {
    card.setAttribute("data-pending", p.title || "pending");
    card.removeAttribute("data-job");
    if (dismissedPending === (p.title || "pending") &&
        p.stage === "await_approval") return hide();
    head("📖 Book — approve the outline");
    card.appendChild(el("div", null, p.title || "Untitled book"));
    if (p.premise)
      card.appendChild(el("div", "ogbk-meta", p.premise));
    if (p.stage !== "await_approval") {
      card.appendChild(el("div", "ogbk-meta",
        "OG is drafting the outline — the approval lands here when it's ready."));
      card.hidden = false;
      return;
    }
    var list = el("div", "ogbk-chaps");
    (p.chapters || []).forEach(function (ch) {
      list.appendChild(el("div", null,
        "Chapter " + ch.n + ": " + (ch.title || "") +
        (ch.beat ? " — " + ch.beat : "")));
    });
    card.appendChild(list);
    card.appendChild(el("div", "ogbk-meta",
      (p.chapters || []).length + " chapters · ~" +
      (p.chapter_words || 1000) + " words each"));
    var btns = el("div", "ogbk-btns");
    var yes = el("button", null, "✅ Write it");
    var no = el("button", "ogbk-no", "No — scrap it");
    yes.addEventListener("click", function () {
      yes.disabled = true;
      post("/books/approve").then(function () { poll(); });
    });
    no.addEventListener("click", function () {
      no.disabled = true;
      post("/books/decline").then(function () {
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
    if (j.status === "done") {
      if (dismissedJob === j.id) return hide();
      head("📖 Your book is ready");
      card.appendChild(el("div", null, j.title || "Book"));
      if (j.detail) card.appendChild(el("div", "ogbk-meta", j.detail));
      var btns = el("div", "ogbk-btns");
      var read = el("button", null, "📖 Read");
      read.addEventListener("click", function () { openReader(j.id); });
      btns.appendChild(read);
      var dl = el("a", "ogbk-dl", "⬇ Download .md");
      dl.href = "/books/" + j.id + "/download";
      dl.setAttribute("download", "");
      btns.appendChild(dl);
      card.appendChild(btns);
      card.hidden = false;
      return;
    }
    if (j.status === "failed") {
      if (dismissedJob === j.id) return hide();
      head("📖 Book didn't finish");
      card.appendChild(el("div", "ogbk-err",
        j.detail || "That run didn't make it."));
      var fbtns = el("div", "ogbk-btns");
      var cont = el("button", null, "Continue the book");
      cont.addEventListener("click", function () {
        cont.disabled = true;
        post("/books/" + j.id + "/continue").then(function () { poll(); });
      });
      fbtns.appendChild(cont);
      card.appendChild(fbtns);
      card.hidden = false;
      return;
    }
    if (j.status === "paused_caps") {
      head("📖 Book — paused");
      card.appendChild(el("div", null, j.title || "Book"));
      card.appendChild(el("div", "ogbk-meta", j.detail || ""));
      card.appendChild(el("div", "ogbk-meta",
        "Chapter " + (j.chapters_done || 0) + " of " +
        (j.chapters_total || 0) + " done — every finished " +
        "chapter is saved."));
      var pbtns = el("div", "ogbk-btns");
      var go = el("button", null, "Continue the book");
      go.addEventListener("click", function () {
        go.disabled = true;
        post("/books/" + j.id + "/continue").then(function () { poll(); });
      });
      pbtns.appendChild(go);
      card.appendChild(pbtns);
      card.hidden = false;
      return;
    }
    head("📖 Book — on the press");
    card.appendChild(el("div", null, j.title || "Book"));
    /* Progress line as chapters land: "Chapter 3 of 8 done". */
    card.appendChild(el("div", "ogbk-meta",
      j.detail || ("Chapter " + (j.chapters_done || 0) + " of " +
        (j.chapters_total || 0) + " done")));
    card.hidden = false;
  }

  var live = true;
  function poll() {
    if (!live) return;
    fetch("/books/status", { credentials: "same-origin" })
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

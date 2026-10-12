/* Generator 7f: the voice-lab card — clone flow (sample
   upload + voice name + the rights-attestation checkbox +
   Approve/Decline doors riding POST /voice-lab/approve and
   /voice-lab/decline), the cloned-voice list with delete,
   and the sfx flow (approval + a real <audio> player +
   download + locker save). Polls GET /voice-lab/status.
   While the lab is dark the status answers enabled:false
   and this card never appears. Lives in its own asset: the
   page is at its push ceiling, so this loads via a loader
   stub in static/og_tokens.js, and all CSS is injected. */
(function () {
  "use strict";
  var dismissedJob = "", dismissedPending = "";
  var css = document.createElement("style");
  css.textContent =
    "#ogVoiceCard{position:fixed;right:12px;bottom:92px;z-index:2147482000;" +
    "width:min(92vw,380px);background:#101010;color:#f5eeda;border:1px solid #c9a227;" +
    "border-radius:14px;padding:12px 14px;font:14px/1.45 system-ui,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)}" +
    "#ogVoiceCard h3{margin:0 54px 6px 0;font-size:15px;color:#e8c766}" +
    "#ogVoiceCard .ogvc-meta{opacity:.85;font-size:12.5px;margin-bottom:6px}" +
    "#ogVoiceCard audio{width:100%;margin:8px 0 4px}" +
    "#ogVoiceCard input[type=text]{width:100%;box-sizing:border-box;margin:6px 0;" +
    "background:#1c1c1c;color:#f5eeda;border:1px solid #555;border-radius:8px;padding:7px 9px}" +
    "#ogVoiceCard label.ogvc-att{display:flex;gap:8px;align-items:flex-start;" +
    "font-size:13px;margin:8px 0;cursor:pointer}" +
    "#ogVoiceCard .ogvc-btns{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}" +
    "#ogVoiceCard button,#ogVoiceCard a.ogvc-dl{background:#c9a227;color:#000;" +
    "border:0;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer;" +
    "text-decoration:none;font-size:13.5px}" +
    "#ogVoiceCard button.ogvc-no{background:#2a2a2a;color:#f5eeda;" +
    "border:1px solid #555}" +
    "#ogVoiceCard button:disabled{opacity:.45;cursor:default}" +
    "#ogVoiceCard .ogvc-x{position:absolute;top:8px;right:8px;width:42px;height:42px;" +
    "border-radius:50%;background:#1b1b1b;border:1px solid #333;color:#e8e8e8;" +
    "display:grid;place-items:center;cursor:pointer;padding:0}" +
    "#ogVoiceCard .ogvc-x svg{width:21px;height:21px}" +
    "#ogVoiceCard .ogvc-x:hover{border-color:rgba(255,193,7,.7);color:#ffc107}" +
    "#ogVoiceCard .ogvc-voice{display:flex;gap:8px;align-items:center;margin:4px 0}" +
    "#ogVoiceCard .ogvc-voice span{flex:1}" +
    /* Round 106: the locker save is an icon at the card's
       top right, left of the canonical x (Brent: icon only). */
    "##ogVoiceCard button.ogvc-save{position:absolute;top:8px;right:58px;" +
    "width:42px;height:42px;border-radius:50%;background:#1b1b1b;" +
    "border:1px solid #333;color:#e8e8e8;display:grid;place-items:center;" +
    "cursor:pointer;padding:0}" +
    "##ogVoiceCard .ogvc-save svg{width:21px;height:21px;fill:none;" +
    "stroke:currentColor;stroke-width:2;stroke-linecap:round;" +
    "stroke-linejoin:round}" +
    "##ogVoiceCard button.ogvc-save:hover{border-color:rgba(255,193,7,.7);" +
    "color:#ffc107}" +
    "##ogVoiceCard button.ogvc-save.is-saved{background:linear-gradient(" +
    "135deg,#ffc107,#ff8f00);color:#151515;border-color:transparent}" +
    "##ogVoiceCard button.ogvc-save.is-saved svg{fill:currentColor}" +
    "##ogVoiceCard .ogvc-savenote{color:#ff9c9c;font-size:12.5px;" +
    "align-self:center}"; +
    "#ogVoiceCard button.ogvc-save.is-saved{opacity:1}";
  document.head.appendChild(css);

  var card = document.createElement("section");
  card.id = "ogVoiceCard";
  card.hidden = true;
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "Voice lab");
  document.body.appendChild(card);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function post(path, body) {
    var opts = { method: "POST", credentials: "same-origin" };
    if (body !== undefined) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts)
      .then(function (r) { return r.json().catch(function () { return {}; }); });
  }
  function hide() { card.hidden = true; card.innerHTML = ""; }
  function head(title) {
    card.innerHTML = "";
    var x = el("button", "ogvc-x");
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

  function renderClone(p, voices) {
    var key = "clone:" + (p.name || "") + ":" + (p.samples || 0);
    card.setAttribute("data-pending", key);
    card.removeAttribute("data-job");
    if (dismissedPending === key) return hide();
    head("🎙️ Clone a voice");
    card.appendChild(el("div", "ogvc-meta",
      "Upload 1–3 short audio samples of the voice (clear " +
      "speech works best). Samples are deleted the moment " +
      "the clone is made — never kept."));
    var nameIn = document.createElement("input");
    nameIn.type = "text";
    nameIn.placeholder = "Voice name";
    nameIn.value = p.name || "";
    card.appendChild(nameIn);
    var fileIn = document.createElement("input");
    fileIn.type = "file";
    fileIn.accept = "audio/*";
    card.appendChild(fileIn);
    var upBtn = el("button", null, "Upload sample");
    upBtn.addEventListener("click", function () {
      if (!fileIn.files || !fileIn.files[0]) return;
      var fd = new FormData();
      fd.append("file", fileIn.files[0]);
      if (nameIn.value) fd.append("name", nameIn.value);
      upBtn.disabled = true;
      fetch("/voice-lab/samples", { method: "POST",
        credentials: "same-origin", body: fd })
        .then(function () { poll(); });
    });
    card.appendChild(upBtn);
    card.appendChild(el("div", "ogvc-meta",
      "Samples staged: " + (p.samples || 0) + " of " +
      (p.samples_max || 3)));
    // The rights gate: the exact attestation line, as a
    // checkbox — Approve stays disabled until it's ticked
    // AND at least one sample is staged.
    var att = document.createElement("label");
    att.className = "ogvc-att";
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!p.attested;
    att.appendChild(box);
    att.appendChild(el("span", null,
      p.attestation || "This is my voice or I have permission to clone it"));
    card.appendChild(att);
    var btns = el("div", "ogvc-btns");
    var yes = el("button", null, "✅ Clone it");
    var no = el("button", "ogvc-no", "No — scrap it");
    function sync() { yes.disabled = !(box.checked && (p.samples || 0) >= 1); }
    box.addEventListener("change", sync);
    sync();
    yes.addEventListener("click", function () {
      yes.disabled = true;
      post("/voice-lab/approve",
        { attestation: box.checked === true })
        .then(function () { poll(); });
    });
    no.addEventListener("click", function () {
      no.disabled = true;
      post("/voice-lab/decline").then(function () {
        dismissedPending = key; hide();
      });
    });
    btns.appendChild(yes); btns.appendChild(no);
    card.appendChild(btns);
    renderVoices(voices);
    card.hidden = false;
  }

  function renderVoices(voices) {
    if (!voices || !voices.length) return;
    card.appendChild(el("div", "ogvc-meta", "Your cloned voices:"));
    voices.forEach(function (v) {
      var row = el("div", "ogvc-voice");
      row.appendChild(el("span", null,
        (v.name || "Voice") + " — " + (v.option || "")));
      var del = el("button", "ogvc-no", "Delete");
      del.addEventListener("click", function () {
        del.disabled = true;
        post("/voice-lab/delete", { voice_id: v.voice_id })
          .then(function () { poll(); });
      });
      row.appendChild(del);
      card.appendChild(row);
    });
  }

  function renderSfx(p) {
    var key = "sfx:" + (p.description || "");
    card.setAttribute("data-pending", key);
    card.removeAttribute("data-job");
    if (dismissedPending === key && p.stage === "await_approval") return hide();
    head("🔊 Sound effect — approve it");
    card.appendChild(el("div", null, p.description || "Sound effect"));
    card.appendChild(el("div", "ogvc-meta",
      "About " + (p.duration || 5) + " seconds · counts as " +
      "one voice use when it finishes"));
    if (p.stage !== "await_approval") {
      card.appendChild(el("div", "ogvc-meta",
        "Tell OG what the effect should sound like — the " +
        "approval lands here when it's ready."));
      card.hidden = false;
      return;
    }
    var btns = el("div", "ogvc-btns");
    var yes = el("button", null, "✅ Make it");
    var no = el("button", "ogvc-no", "No — scrap it");
    yes.addEventListener("click", function () {
      yes.disabled = true;
      post("/voice-lab/approve", {}).then(function () { poll(); });
    });
    no.addEventListener("click", function () {
      no.disabled = true;
      post("/voice-lab/decline").then(function () {
        dismissedPending = key; hide();
      });
    });
    btns.appendChild(yes); btns.appendChild(no);
    card.appendChild(btns);
    card.hidden = false;
  }

  function renderJob(j, cloneState, voices) {
    if (cloneState && cloneState.state === "generating") {
      head("🎙️ Cloning your voice…");
      card.appendChild(el("div", "ogvc-meta",
        cloneState.detail || "In the studio…"));
      card.hidden = false;
      return;
    }
    if (cloneState && cloneState.state === "done" &&
        dismissedJob !== "clone:" + cloneState.voice_id) {
      card.setAttribute("data-job", "clone:" + cloneState.voice_id);
      head("🎙️ Your cloned voice is ready");
      card.appendChild(el("div", null,
        (cloneState.name || "Voice") + " — pick it as " +
        (cloneState.voice_id ? ("voice:" + cloneState.voice_id) : "")));
      renderVoices(voices);
      card.hidden = false;
      return;
    }
    if (!j) {
      if (voices && voices.length) {
        head("🎙️ Voice lab");
        renderVoices(voices);
        card.hidden = false;
        return;
      }
      return hide();
    }
    card.setAttribute("data-job", j.id || "");
    card.removeAttribute("data-pending");
    if (j.state === "done") {
      if (dismissedJob === j.id) return hide();
      head("🔊 Your sound effect is ready");
      card.appendChild(el("div", null, j.title || "Sound effect"));
      if (j.detail) card.appendChild(el("div", "ogvc-meta", j.detail));
      var audio = document.createElement("audio");
      audio.controls = true;
      audio.src = j.sfx_url || ("/voice-lab/download/" + j.id);
      audio.preload = "metadata";
      card.appendChild(audio);
      var btns = el("div", "ogvc-btns");
      var dl = el("a", "ogvc-dl", "⬇ Download");
      dl.href = audio.src;
      dl.setAttribute("download", "");
      btns.appendChild(dl);
      var snote = el("span", "ogvc-savenote", "");
      btns.appendChild(snote);
      card.appendChild(btns);
      if (!j.saved) {
        /* Round 106: the save rides the same POST, but it
           is an icon now — the folder glyph at the card's
           top right, left of the x. Saved = the glyph
           fills gold; a failure keeps the old words, on
           the note line + the icon's label. */
        var save = el("button", "ogvc-save");
        save.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
        save.setAttribute("aria-label", "Save to my locker");
        save.setAttribute("title", "Save to my locker");
        save.addEventListener("click", function () {
          save.disabled = true;
          post("/voice-lab/save/" + j.id).then(function (d) {
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
      head("🔊 Sound effect didn't finish");
      card.appendChild(el("div", "ogvc-meta",
        j.detail || "That render didn't make it — nothing was charged."));
      card.hidden = false;
      return;
    }
    head("🔊 Rendering your sound effect…");
    card.appendChild(el("div", "ogvc-meta",
      j.detail || "In the studio — rendering the sound effect…"));
    card.hidden = false;
  }

  function poll() {
    fetch("/voice-lab/status", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.enabled) return hide();
        if (d.pending && d.pending.flow === "clone") {
          return renderClone(d.pending, d.voices);
        }
        if (d.pending) return renderSfx(d.pending);
        return renderJob(d.job, d.clone_state, d.voices);
      })
      .catch(function () { /* card stays as-is */ });
  }
  poll();
  setInterval(poll, 5000);
})();

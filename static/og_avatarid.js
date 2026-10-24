/* Ticket E6 — avatar identity, the client half.
   Brent's spec (2026-10-21): take a picture of yourself
   and OG makes an avatar in his style that looks like
   you — "Nothing changes but the avatar" — and the name
   changes from the pen icon next to the avatar on the
   profile sheet.

   What this module does, and all it does:
   - On load, asks GET /avatar-identity/me (account-only;
     an anonymous or failed answer changes NOTHING — the
     page stays exactly the shipped default).
   - If the account has a name: paints it into the name
     spots (profile sheet, top bar — the menu name follows
     the top bar through the page's own observer — and the
     hero name span) and re-points the hero pill's rest
     face through the shell coordinator's ONE writer
     (OGShell.setDisplayName — never a second painter).
   - If the account has personalized art: points every
     avatar still (hero / top bar / profile / menu) at
     /avatar-identity/art. The life machinery — og_idle's
     breathing, og_mouth's voice-driven mouth, the E3
     expressions — is src-blind and rides along untouched.
   - Wires the profile sheet's pen panel: rename (POST
     /avatar-identity/rename), selfie take/upload with a
     client-side downscale (POST /avatar-identity/generate),
     and the revert door (POST /avatar-identity/revert).

   Defensive by contract (the og_mouth shape): feature-
   detected, try/caught, never throws; a failure anywhere
   leaves the default presentation standing.
   window.OGAVATARID exposes the live state for the gate,
   OGIDLE / OGMOUTH style. */
(function () {
  'use strict';
  if (window.OGAVATARID) return;

  var DEFAULT_ART = '/avatar/poster.webp';
  var state = { loaded: false, signedIn: false, name: 'OG',
                hasAvatar: false, artUrl: null, busy: false };
  window.OGAVATARID = {
    version: 1,
    state: function () {
      return { version: 1, loaded: state.loaded,
               signedIn: state.signedIn, name: state.name,
               hasAvatar: state.hasAvatar,
               artUrl: state.artUrl, busy: state.busy };
    }
  };

  function $(id) { return document.getElementById(id); }

  function paintName(name) {
    var prof = $('ogProfileName');
    if (prof) prof.textContent = name;
    var top = $('ogTopName');
    if (top) top.textContent = name;
    document.querySelectorAll('.og-hero-name').forEach(
      function (el) { el.textContent = name; });
    try {
      if (window.OGSHELL && window.OGSHELL.setDisplayName) {
        window.OGSHELL.setDisplayName(
          name === 'OG' ? null : name);
      }
    } catch (e) { }
  }

  function paintArt() {
    var src = (state.hasAvatar && state.artUrl)
      ? state.artUrl : DEFAULT_ART;
    document.querySelectorAll(
      'img.og-avatar, img.og-avatar-sm').forEach(
      function (img) { img.src = src; });
  }

  function apply(d) {
    if (!d || !d.ok) return;
    state.signedIn = true;
    state.name = (typeof d.name === 'string' && d.name)
      ? d.name : 'OG';
    state.hasAvatar = !!d.has_avatar;
    state.artUrl = d.art_url || null;
    paintName(state.name);
    paintArt();
    var rev = $('ogRevertBtn');
    if (rev) rev.hidden = !state.hasAvatar;
    var inp = $('ogNameInput');
    if (inp && document.activeElement !== inp) {
      inp.value = state.name;
    }
  }

  function load() {
    if (!window.fetch) { state.loaded = true; return; }
    fetch('/avatar-identity/me', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { apply(d); })
      .catch(function () { })
      .then(function () { state.loaded = true; });
  }

  function status(line) {
    var el = $('ogAvatarIdStatus');
    if (el) el.textContent = line || '';
  }

  function rename() {
    var inp = $('ogNameInput');
    if (!inp || state.busy) return;
    var name = (inp.value || '').trim();
    state.busy = true;
    status('Saving…');
    fetch('/avatar-identity/rename', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    }).then(function (r) {
      return r.json().then(function (d) {
        return { okHttp: r.ok, d: d };
      });
    }).then(function (res) {
      state.busy = false;
      if (res.d && res.d.ok) {
        state.name = res.d.name || name;
        paintName(state.name);
        status(res.d.response || 'Name saved.');
      } else {
        status((res.d && res.d.response) ||
               'That name didn\'t take — try another.');
      }
    }).catch(function () {
      state.busy = false;
      status('Name didn\'t save — connection hiccup. ' +
             'Try again.');
    });
  }

  function downscale(file, done) {
    /* Client-side shrink (longest side 1024, JPEG) so a
       phone photo rides light; the server re-checks and
       re-encodes regardless — this is courtesy, not the
       guarantee. Any failure sends the original file. */
    try {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        try {
          var w = img.naturalWidth || img.width;
          var h = img.naturalHeight || img.height;
          var scale = Math.min(1, 1024 / Math.max(w, h));
          if (scale >= 1) { done(file); return; }
          var c = document.createElement('canvas');
          c.width = Math.round(w * scale);
          c.height = Math.round(h * scale);
          c.getContext('2d').drawImage(img, 0, 0,
                                       c.width, c.height);
          URL.revokeObjectURL(url);
          if (c.toBlob) {
            c.toBlob(function (b) { done(b || file); },
                     'image/jpeg', 0.9);
          } else { done(file); }
        } catch (e) { done(file); }
      };
      img.onerror = function () { done(file); };
      img.src = url;
    } catch (e) { done(file); }
  }

  function generate(file) {
    if (!file || state.busy) return;
    state.busy = true;
    status('OG is drawing you — this takes a few seconds…');
    downscale(file, function (blob) {
      var fd = new FormData();
      fd.append('file', blob, 'selfie.jpg');
      fetch('/avatar-identity/generate', {
        method: 'POST', credentials: 'same-origin', body: fd
      }).then(function (r) {
        return r.json().then(function (d) {
          return { okHttp: r.ok, d: d };
        });
      }).then(function (res) {
        state.busy = false;
        if (res.d && res.d.ok) {
          apply(res.d);
          status(res.d.response ||
                 'Done — new face, same OG.');
        } else {
          status((res.d && res.d.response) ||
                 'The drawing didn\'t come out — try ' +
                 'another photo.');
        }
      }).catch(function () {
        state.busy = false;
        status('The drawing didn\'t land — connection ' +
               'hiccup. Try again.');
      });
    });
  }

  function revert() {
    if (state.busy) return;
    state.busy = true;
    status('Switching back…');
    fetch('/avatar-identity/revert', {
      method: 'POST', credentials: 'same-origin'
    }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (d) {
      state.busy = false;
      if (d && d.ok) {
        apply(d);
        status(d.response || 'Back to the original face.');
      } else {
        status('That didn\'t take — try again.');
      }
    }).catch(function () {
      state.busy = false;
      status('That didn\'t take — connection hiccup.');
    });
  }

  function wire() {
    var pen = $('profilePenBtn'), panel = $('ogAvatarIdPanel');
    function openPanel(focusName) {
      if (!panel) return;
      panel.hidden = false;
      var inp = $('ogNameInput');
      if (inp) {
        inp.value = state.name;
        if (focusName) {
          try { inp.focus(); } catch (e) { }
        }
      }
      try {
        panel.scrollIntoView({ block: 'nearest' });
      } catch (e) { }
    }
    if (pen && panel) {
      pen.addEventListener('click', function () {
        if (panel.hidden) { openPanel(true); }
        else { panel.hidden = true; }
      });
    }
    var lookBtn = $('ogLookLikeYouBtn');
    if (lookBtn && panel) {
      lookBtn.addEventListener('click', function () {
        openPanel(false);
      });
    }
    var save = $('ogNameSaveBtn');
    if (save) save.addEventListener('click', rename);
    var inp = $('ogNameInput');
    if (inp) {
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); rename(); }
      });
    }
    var selfieBtn = $('ogSelfieBtn'), selfieIn = $('ogSelfieInput');
    if (selfieBtn && selfieIn) {
      selfieBtn.addEventListener('click',
        function () { selfieIn.click(); });
      selfieIn.addEventListener('change', function () {
        if (selfieIn.files && selfieIn.files[0]) {
          generate(selfieIn.files[0]);
        }
        selfieIn.value = '';
      });
    }
    var upBtn = $('ogUploadBtn'), upIn = $('ogUploadInput');
    if (upBtn && upIn) {
      upBtn.addEventListener('click',
        function () { upIn.click(); });
      upIn.addEventListener('change', function () {
        if (upIn.files && upIn.files[0]) {
          generate(upIn.files[0]);
        }
        upIn.value = '';
      });
    }
    var rev = $('ogRevertBtn');
    if (rev) rev.addEventListener('click', revert);
  }

  function init() {
    try { wire(); } catch (e) { }
    try { load(); } catch (e) { state.loaded = true; }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();

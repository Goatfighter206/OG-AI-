/* Round 90: the Permissions folder + the chat maps
 * handoff (web).
 *
 * Brent (2026-10-12): "there should be a folder called
 * permission to run anything on their phone" — and the
 * Driving GPS folder goes: "OG will just use their phone
 * for that situation."
 *
 * PERMISSIONS — one folder (menu row -> full view on the
 * OG70 scaffold) that is the ONE home for everything OG
 * may run or use on the user's phone. The rows come from
 * GET /permissions (the per-account store in
 * og_permissions.py — the single source): Brent's list
 * (CarPlay, Maps, Calculator, Calendar, Health, Apple
 * Watch, Spotify, Pandora, GPS) plus what OG provably
 * touches on a phone (Microphone, Camera & Photos,
 * Notifications). A row paints only the server's answer;
 * a tap POSTs the flip to /permissions and paints what
 * comes back (the Round 73 discipline).
 *
 * The GPS row is special: it carries Round 73's ACCOUNT
 * LOCATION SWITCH itself — the same switch the app
 * flips and chat flips ("turn my location on"), bound
 * to GET/POST /gps/location exactly as it was on the
 * old Driving view. Only the switch's HOME moved; its
 * store, endpoints and semantics did not. The device
 * chip + position watch ride along: the watch runs only
 * while the switch is on, and the browser's own
 * permission stays the device's gate underneath it.
 *
 * THE HANDOFF MOVES INTO CHAT. The Driving view is
 * deleted (og_r66.js is Construction only now). When OG
 * works out a route in chat, the server stores it as
 * the account's active route (Round 68 — unchanged);
 * this asset watches the page's own addMessage — every
 * OG reply, streamed or classic, lands through it — and
 * when a FRESH route appears (created within the last
 * 600 s and not already shown this session) it appends
 * an "Open in Maps" button to that reply's bubble,
 * built from the stored route's ends: Apple Maps on
 * Apple devices, Google Maps otherwise (the Round 82
 * URL shapes). The button renders only while the Maps
 * permission reads ON in the store (GET /permissions,
 * cached 60 s; a failed read falls back to the store's
 * own default, ON — the server stays the source, this
 * consult is a render gate). Directions are asked in
 * chat, driven on the phone — no folder anywhere.
 *
 * Loaded via the og_tokens.js ride-along loader like
 * Rounds 62/63/65/66/84/85 — the page is at its push
 * ceiling.
 */
(function () {
  'use strict';

  var css = ''
    + '.og90-body{padding:4px 2px 14px;color:#ececec}'
    + '.og90-note{color:#999;font-size:.88em;padding:6px 0;margin:0;'
    + 'line-height:1.5}'
    + '.og90-row{padding:12px 0;border-top:1px solid #232323}'
    + '.og90-name{font-size:1.02em;font-weight:700}'
    + '.og90-btn{padding:10px 14px;margin:8px 4px 2px 0;border:none;'
    + 'border-radius:8px;background:#ffc107;color:#111;font-weight:700;'
    + 'font-family:inherit;cursor:pointer}'
    + '.og90-btn.dim{background:#262626;color:#ececec;font-weight:400}'
    + '.og90-chip{display:inline-block;padding:5px 12px;'
    + 'border-radius:999px;border:1px solid #3a3a3a;color:#d8d8d8;'
    + 'font-size:.85em;margin:4px 0}'
    + '.og90-chip.on{border-color:rgba(255,193,7,.65);color:#ffc107}'
    + '.og90-maps-btn{display:inline-block;margin-top:9px;'
    + 'padding:9px 18px;border-radius:999px;background:#ffc107;'
    + 'color:#111;font-weight:700;text-decoration:none;'
    + 'font-family:inherit}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    });
  }
  function post(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  }

  /* OG70 loads through the same stub chain; resolve it at
   * tap time so dynamic-script order can never break us. */
  function openView(opts) {
    if (window.OG70 && window.OG70.openView) {
      window.OG70.openView(opts);
      return;
    }
    var n = 0;
    var iv = setInterval(function () {
      n += 1;
      if (window.OG70 && window.OG70.openView) {
        clearInterval(iv);
        window.OG70.openView(opts);
      } else if (n > 30) {
        clearInterval(iv);
      }
    }, 100);
  }

  /* ---------------- The maps handoff (Round 82 shapes) ---------------- */
  function isAppleDevice() {
    return /iPhone|iPad|iPod|Macintosh|Mac OS X/.test(
      navigator.userAgent || '');
  }
  function mapsEnc(s) {
    // encode, but keep commas readable — both maps apps
    // take "lat,lon" and "City, ST" in plain form.
    return encodeURIComponent(s).replace(/%2C/g, ',');
  }
  function mapsUrl(origin, dest) {
    if (!dest) return '';
    if (isAppleDevice()) {
      var a = 'https://maps.apple.com/?daddr=' + mapsEnc(dest);
      if (origin) a += '&saddr=' + mapsEnc(origin);
      return a;
    }
    var g = 'https://www.google.com/maps/dir/?api=1&destination='
      + mapsEnc(dest);
    if (origin) g += '&origin=' + mapsEnc(origin);
    return g;
  }
  function coordText(end) {
    if (!end || end.lat == null || end.lon == null) return '';
    return end.lat + ',' + end.lon;
  }

  /* The Maps permission, consulted before the handoff
   * renders (the store is the source; this cache is a
   * render gate, refreshed on a 60 s cadence). */
  var permMaps = null;
  var permAt = 0;
  function mapsAllowed(cb) {
    if (permMaps !== null && Date.now() - permAt < 60000) {
      cb(permMaps);
      return;
    }
    api('/permissions').then(function (res) {
      var caps = (res.d && res.d.caps) || [];
      for (var i = 0; i < caps.length; i++) {
        if (caps[i].key === 'maps') {
          permMaps = !!caps[i].on;
          permAt = Date.now();
          cb(permMaps);
          return;
        }
      }
      cb(true); // no maps row in the answer: the store default
    }).catch(function () {
      cb(true); // failed read: the store's own default (ON)
    });
  }

  /* One button per route per page session. */
  var lastRouteCreated = 0;
  function maybeHandoff() {
    api('/gps/route/active').then(function (res) {
      var r = res.d && res.d.route;
      if (!r || !r.created) return;
      var created = Number(r.created);
      if (!(created > lastRouteCreated)) return;
      if (Date.now() / 1000 - created > 600) return;
      var dest = coordText(r.to);
      if (!dest) return;
      mapsAllowed(function (ok) {
        if (!ok) return;
        if (!(created > lastRouteCreated)) return;
        var bubbles = document.querySelectorAll(
          '#messagesArea .message.ai .message-content');
        var mc = bubbles[bubbles.length - 1];
        if (!mc || mc.querySelector('.og90-maps-btn')) return;
        var url = mapsUrl(coordText(r.from), dest);
        if (!url) return;
        lastRouteCreated = created;
        var a = el('a', 'og90-maps-btn', 'Open in Maps');
        a.setAttribute('href', url);
        a.target = '_blank';
        a.rel = 'noopener';
        mc.appendChild(a);
      });
    }).catch(function () { /* no route, no button */ });
  }

  /* Every OG reply lands through the page's addMessage —
   * wrap it (once) and check for a fresh route after each
   * AI reply renders. */
  var wrapTries = 0;
  var wrapIv = setInterval(function () {
    wrapTries += 1;
    if (typeof window.addMessage === 'function'
        && !window.__og90HandoffWrapped) {
      window.__og90HandoffWrapped = true;
      var orig = window.addMessage;
      window.addMessage = function () {
        var out = orig.apply(this, arguments);
        if (arguments[0] === 'ai') {
          try { maybeHandoff(); } catch (e) { /* never eat a reply */ }
        }
        return out;
      };
      clearInterval(wrapIv);
    }
    if (wrapTries > 600) clearInterval(wrapIv);
  }, 250);

  /* ---------------- The Permissions view ---------------- */
  function buildPermissions(body, view) {
    var wrap = el('div', 'og90-body');
    body.appendChild(wrap);
    wrap.appendChild(el('p', 'og90-note',
      'Everything OG can run on your phone lives here. '
      + 'OG checks this list before he uses any of it.'));
    var listBox = el('div');
    wrap.appendChild(listBox);
    listBox.appendChild(el('p', 'og90-note', 'Loading…'));

    var alive = true;
    view.track(function () { alive = false; });

    /* --- the GPS row: Round 73's location switch, new home --- */
    var loc = { chip: null, btn: null, on: null, watchId: null };
    function stopWatch() {
      if (loc.watchId !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(loc.watchId);
      }
      loc.watchId = null;
    }
    function startWatch() {
      if (loc.watchId !== null || !navigator.geolocation) return;
      loc.watchId = navigator.geolocation.watchPosition(function () {
        if (loc.chip) {
          loc.chip.textContent = 'Location: On';
          loc.chip.className = 'og90-chip on';
        }
      }, function () {
        if (loc.chip) {
          loc.chip.textContent = 'Location: Off — this browser is '
            + 'not sharing its position.';
          loc.chip.className = 'og90-chip';
        }
      }, { enableHighAccuracy: true });
    }
    view.track(stopWatch);
    function paintLocBtn() {
      if (!loc.btn) return;
      if (loc.on === null) {
        loc.btn.textContent = 'OG location: …';
        loc.btn.className = 'og90-btn dim';
        return;
      }
      loc.btn.textContent = loc.on
        ? 'OG location: On — tap to turn off'
        : 'OG location: Off — tap to turn on';
      loc.btn.className = loc.on ? 'og90-btn' : 'og90-btn dim';
    }
    function applyLocState(on) {
      loc.on = !!on;
      paintLocBtn();
      if (loc.on) {
        startWatch();
      } else {
        stopWatch();
        if (loc.chip) {
          loc.chip.textContent = 'Location: Off — OG\'s location '
            + 'switch is off.';
          loc.chip.className = 'og90-chip';
        }
      }
    }
    function buildGpsRow(cap) {
      var row = el('div', 'og90-row');
      row.appendChild(el('div', 'og90-name', cap.name));
      row.appendChild(el('p', 'og90-note', cap.note));
      loc.chip = el('span', 'og90-chip', 'Location: …');
      loc.chip.id = 'og66LocChip';
      row.appendChild(loc.chip);
      loc.btn = el('button', 'og90-btn dim', 'OG location: …');
      loc.btn.type = 'button';
      loc.btn.id = 'og66LocBtn';
      row.appendChild(loc.btn);
      if (!navigator.geolocation) {
        loc.chip.textContent = 'Location: Off — this browser has no GPS.';
      }
      api('/gps/location').then(function (res) {
        if (!alive) return;
        if (res.d && typeof res.d.on === 'boolean') {
          applyLocState(res.d.on);
        }
      }).catch(function () { /* button keeps its … state */ });
      loc.btn.addEventListener('click', function () {
        if (loc.on === null) return;
        loc.btn.textContent = 'OG location: …';
        post('/gps/location', { on: !loc.on }).then(function (res) {
          if (!alive) return;
          if (res.d && typeof res.d.on === 'boolean') {
            applyLocState(res.d.on);
          } else {
            paintLocBtn();
          }
        }).catch(function () {
          if (alive) paintLocBtn();
        });
      });
      return row;
    }

    /* --- every other row: a standing permission --- */
    function buildRow(cap) {
      var row = el('div', 'og90-row');
      row.appendChild(el('div', 'og90-name', cap.name));
      row.appendChild(el('p', 'og90-note', cap.note));
      var btn = el('button', 'og90-btn', '');
      btn.type = 'button';
      var cur = !!cap.on;
      function paint() {
        btn.textContent = cur ? 'On — tap to turn off'
                              : 'Off — tap to turn on';
        btn.className = cur ? 'og90-btn' : 'og90-btn dim';
      }
      paint();
      btn.addEventListener('click', function () {
        btn.textContent = '…';
        post('/permissions', { cap: cap.key, on: !cur })
          .then(function (res) {
            if (!alive) return;
            if (res.d && typeof res.d.on === 'boolean') {
              cur = res.d.on;
              if (cap.key === 'maps') {
                permMaps = cur;
                permAt = Date.now();
              }
              paint();
            } else {
              load();
            }
          }).catch(function () {
            if (alive) load();
          });
      });
      row.appendChild(btn);
      return row;
    }

    function load() {
      api('/permissions').then(function (res) {
        if (!alive) return;
        var caps = (res.d && res.d.caps) || null;
        listBox.innerHTML = '';
        if (!caps) {
          listBox.appendChild(el('p', 'og90-note',
            'The list didn\'t load — close this and open it again.'));
          return;
        }
        caps.forEach(function (cap) {
          listBox.appendChild(cap.key === 'gps'
            ? buildGpsRow(cap) : buildRow(cap));
        });
      }).catch(function () {
        if (!alive) return;
        listBox.innerHTML = '';
        listBox.appendChild(el('p', 'og90-note',
          'The list didn\'t load — close this and open it again.'));
      });
    }
    load();
  }

  function openPermissions() {
    openView({ title: 'Permissions', wide: true, onOpen: buildPermissions });
  }

  /* ---------------- Menu row ---------------- */
  var sec = null, head = null;
  function buildSection() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    head = el('h3', 'og-menu-sec', 'Phone');
    sec = el('nav', 'og-menu-card', '');
    sec.id = 'ogPermCard';
    var row = el('button', 'og-menu-item', '');
    row.type = 'button'; row.id = 'og90PermissionsRow';
    var ico = el('span', 'og-mi', '');
    ico.innerHTML = '<svg><use href="#i-check"/></svg>';
    row.appendChild(ico);
    row.appendChild(el('span', '', 'Permissions'));
    row.addEventListener('click', openPermissions);
    sec.appendChild(row);
    var gpsCard = $('ogGpsCard');
    var anchor = gpsCard || card;
    anchor.parentNode.insertBefore(head, anchor.nextSibling);
    anchor.parentNode.insertBefore(sec, head.nextSibling);
    sec.hidden = true;
    head.hidden = true;
    return true;
  }

  // Show/hide with the Account section (signed in), like R63/R65/R66.
  var tries = 0;
  var iv = setInterval(function () {
    tries += 1;
    if (buildSection()) {
      var acctSec = $('menuAccountSec');
      var signedIn = acctSec && !acctSec.hidden;
      sec.hidden = !signedIn;
      if (head) head.hidden = !signedIn;
    }
    if (tries > 600) clearInterval(iv);
  }, 1500);
})();

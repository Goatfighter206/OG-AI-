/* Round 66: GPS (web) — driving navigation + job-site GPS.
 *
 * One asset, zero page edits beyond the loader stub in
 * static/og_tokens.js (the R62/R63/R65 pattern; the page sits
 * at its push ceiling). A "GPS" section is injected into the
 * side menu right after the Account card, with two tabs:
 *
 *   Driving  — from/to fields (a place name, or "lat, lon"),
 *              "Use my location", POST /gps/route, steps list
 *              with per-step distances + totals, and a live
 *              "you are here" line from a geolocation watch.
 *   Job sites — the caller's saved points (GET /gps/points),
 *              add by manual lat/lon or "drop at my location",
 *              per point: Convert readout (UTM + WA state
 *              plane via GET /gps/convert), Stakeout view (big
 *              distance + bearing arrow, updated live from a
 *              geolocation watch through POST /gps/stakeout —
 *              the server is the single math source), a
 *              "distance to…" picker (POST /gps/between),
 *              delete, and an Export CSV button.
 *
 * All math comes from the server (og_gps) so the web and the
 * app agree digit for digit. The section shows only while the
 * Account section is shown (signed in), matching R63/R65.
 */
(function () {
  'use strict';

  var css = ''
    + '.og66-tabs{display:flex;gap:8px;padding:10px 12px}'
    + '.og66-tab{flex:1;padding:9px;border:1px solid #333;border-radius:10px;'
    + 'background:#161616;color:#ececec;font-family:inherit;cursor:pointer}'
    + '.og66-tab.on{border-color:#ffc107;color:#ffc107;font-weight:700}'
    + '.og66-body{padding:4px 14px 14px;color:#ececec}'
    + '.og66-body input,.og66-body select{width:100%;box-sizing:border-box;'
    + 'padding:10px;margin:5px 0;background:#111;border:1px solid #333;'
    + 'border-radius:8px;color:#ececec;font-family:inherit}'
    + '.og66-btn{padding:10px 14px;margin:5px 4px 5px 0;border:none;'
    + 'border-radius:8px;background:#ffc107;color:#111;font-weight:700;'
    + 'font-family:inherit;cursor:pointer}'
    + '.og66-btn.dim{background:#262626;color:#ececec;font-weight:400}'
    + '.og66-step{padding:7px 0;border-top:1px solid #232323;font-size:.95em}'
    + '.og66-step b{float:right;color:#ffc107}'
    + '.og66-pt{padding:9px 0;border-top:1px solid #232323}'
    + '.og66-pt b.name{font-size:1.02em}'
    + '.og66-small{color:#999;font-size:.86em}'
    + '.og66-big{font-size:1.9em;font-weight:800;text-align:center;padding:8px 0}'
    + '.og66-arrow{display:inline-block;color:#ffc107}'
    + '.og66-note{color:#999;font-size:.88em;padding:6px 0;margin:0}';
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
  function fmtDist(m) {
    var ft = m * 3.28084;
    if (ft < 500) return Math.round(ft) + ' ft';
    return (m / 1609.344).toFixed(2) + ' mi';
  }
  function fmtDur(s) {
    var mins = Math.round(s / 60);
    if (mins < 60) return mins + ' min';
    return Math.floor(mins / 60) + ' h ' + (mins % 60) + ' min';
  }
  // A field holds either a place name or "lat, lon".
  function endpoint(text) {
    var m = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text || '');
    if (m) return { lat: parseFloat(m[1]), lon: parseFloat(m[2]) };
    return { place: (text || '').trim() };
  }

  var sec = null, head = null, tab = 'drive';
  var myPos = null;              // {lat, lon} from the watch
  var driveWatch = null, stakeWatch = null;
  var stakeTarget = null;        // point id being staked out
  var points = [];

  /* ---------------- Driving ---------------- */
  function buildDrive(body) {
    body.innerHTML = '';
    var from = el('input'); from.id = 'og66From'; from.placeholder = 'From — place, or lat, lon';
    var to = el('input'); to.id = 'og66To'; to.placeholder = 'To — place, or lat, lon';
    var useMine = el('button', 'og66-btn dim', 'Use my location');
    useMine.type = 'button';
    useMine.addEventListener('click', function () {
      if (!navigator.geolocation) return;
      navigator.geolocation.getCurrentPosition(function (p) {
        from.value = p.coords.latitude.toFixed(6) + ', ' + p.coords.longitude.toFixed(6);
      });
    });
    var go = el('button', 'og66-btn', 'Get route');
    go.type = 'button';
    var here = el('p', 'og66-note', 'You are here: (location not shared yet)');
    here.id = 'og66Here';
    var out = el('div'); out.id = 'og66RouteOut';
    go.addEventListener('click', function () {
      out.innerHTML = '';
      out.appendChild(el('p', 'og66-note', 'Routing…'));
      post('/gps/route', { from: endpoint(from.value), to: endpoint(to.value) })
        .then(function (res) {
          out.innerHTML = '';
          if (!res.ok || !res.d || !res.d.steps) {
            out.appendChild(el('p', 'og66-note',
              (res.d && res.d.detail) || 'Route unavailable — nothing was made up.'));
            return;
          }
          out.appendChild(el('p', 'og66-note',
            'Total: ' + fmtDist(res.d.distance_m) + ' · ' + fmtDur(res.d.duration_s)));
          res.d.steps.forEach(function (s) {
            var row = el('div', 'og66-step', s.instruction);
            row.appendChild(el('b', '', fmtDist(s.distance_m)));
            out.appendChild(row);
          });
        })
        .catch(function () {
          out.innerHTML = '';
          out.appendChild(el('p', 'og66-note', 'Route unavailable — nothing was made up.'));
        });
    });
    body.appendChild(from); body.appendChild(to);
    body.appendChild(useMine); body.appendChild(go);
    body.appendChild(here); body.appendChild(out);
    if (navigator.geolocation && driveWatch === null) {
      driveWatch = navigator.geolocation.watchPosition(function (p) {
        myPos = { lat: p.coords.latitude, lon: p.coords.longitude };
        var h = $('og66Here');
        if (h) h.textContent = 'You are here: ' + myPos.lat.toFixed(5) + ', '
          + myPos.lon.toFixed(5) + ' (±' + Math.round(p.coords.accuracy || 0) + ' m)';
      }, function () { /* watch stays silent; fields still work */ },
        { enableHighAccuracy: true });
    }
  }

  /* ---------------- Job sites ---------------- */
  function refreshPoints(body) {
    api('/gps/points').then(function (res) {
      points = (res.d && res.d.points) || [];
      renderPoints(body);
    }).catch(function () { /* list stays as-is */ });
  }

  function renderPoints(body) {
    var list = $('og66PtList');
    if (!list) return;
    list.innerHTML = '';
    if (!points.length) {
      list.appendChild(el('p', 'og66-note', 'No points saved yet.'));
      return;
    }
    points.forEach(function (pt) {
      var row = el('div', 'og66-pt');
      var nm = el('b', 'name', pt.name); row.appendChild(nm);
      row.appendChild(el('div', 'og66-small',
        pt.lat.toFixed(6) + ', ' + pt.lon.toFixed(6) + (pt.note ? ' · ' + pt.note : '')));
      var readout = el('div', 'og66-small'); readout.id = 'og66Conv' + pt.id;
      var conv = el('button', 'og66-btn dim', 'Convert'); conv.type = 'button';
      conv.addEventListener('click', function () {
        api('/gps/convert?lat=' + pt.lat + '&lon=' + pt.lon).then(function (res) {
          var d = res.d || {}, t = '';
          if (d.utm) t += 'UTM ' + d.utm.zone + d.utm.hemisphere + '  E '
            + Math.round(d.utm.easting) + '  N ' + Math.round(d.utm.northing) + '  ';
          if (d.state_plane) t += '| State plane ' + d.state_plane.zone + ': E '
            + Math.round(d.state_plane.easting_ft) + ' ft  N '
            + Math.round(d.state_plane.northing_ft) + ' ft';
          else if (d.note) t += '| ' + d.note;
          readout.textContent = t;
        });
      });
      var stake = el('button', 'og66-btn dim', 'Stakeout'); stake.type = 'button';
      stake.addEventListener('click', function () { startStakeout(pt, body); });
      var del = el('button', 'og66-btn dim', 'Delete'); del.type = 'button';
      del.addEventListener('click', function () {
        post('/gps/points/delete', { id: pt.id }).then(function () { refreshPoints(body); });
      });
      var picker = el('select');
      picker.appendChild(el('option', '', 'Distance to…'));
      points.forEach(function (other) {
        if (other.id === pt.id) return;
        var o = el('option', '', other.name); o.value = other.id;
        picker.appendChild(o);
      });
      var distOut = el('div', 'og66-small');
      picker.addEventListener('change', function () {
        var other = points.filter(function (p) { return p.id === picker.value; })[0];
        if (!other) return;
        post('/gps/between', {
          a: { lat: pt.lat, lon: pt.lon }, b: { lat: other.lat, lon: other.lon }
        }).then(function (res) {
          if (res.d) distOut.textContent = fmtDist(res.d.distance_m)
            + ' at ' + Math.round(res.d.bearing_deg) + '° to ' + other.name;
        });
      });
      row.appendChild(readout);
      row.appendChild(conv); row.appendChild(stake); row.appendChild(del);
      row.appendChild(picker); row.appendChild(distOut);
      list.appendChild(row);
    });
  }

  function startStakeout(pt, body) {
    stakeTarget = pt.id;
    var box = $('og66Stake');
    if (!box) return;
    box.innerHTML = '';
    box.appendChild(el('div', 'og66-small', 'Stakeout: ' + pt.name));
    var big = el('div', 'og66-big'); big.id = 'og66StakeBig';
    big.textContent = 'waiting for GPS…';
    box.appendChild(big);
    if (!navigator.geolocation) {
      big.textContent = 'This device has no GPS.';
      return;
    }
    if (stakeWatch !== null) navigator.geolocation.clearWatch(stakeWatch);
    stakeWatch = navigator.geolocation.watchPosition(function (p) {
      post('/gps/stakeout', {
        target_id: pt.id,
        at: { lat: p.coords.latitude, lon: p.coords.longitude }
      }).then(function (res) {
        var b = $('og66StakeBig');
        if (!b || stakeTarget !== pt.id || !res.d) return;
        b.innerHTML = '';
        var arrow = el('span', 'og66-arrow', '➤');
        arrow.style.transform = 'rotate(' + (res.d.bearing_deg - 90) + 'deg)';
        b.appendChild(arrow);
        b.appendChild(document.createTextNode(' ' + fmtDist(res.d.distance_m)
          + ' · ' + Math.round(res.d.bearing_deg) + '°'));
      });
    }, function () { /* keep last reading */ }, { enableHighAccuracy: true });
  }

  function buildSites(body) {
    body.innerHTML = '';
    body.appendChild(el('p', 'og66-note',
      'Phone GPS is accurate to a few meters — good for finding '
      + 'and checking points, not a survey rover.'));
    var name = el('input'); name.id = 'og66PtName'; name.placeholder = 'Point name (e.g. NW corner)';
    var lat = el('input'); lat.id = 'og66PtLat'; lat.placeholder = 'Latitude';
    var lon = el('input'); lon.id = 'og66PtLon'; lon.placeholder = 'Longitude';
    var note = el('input'); note.id = 'og66PtNote'; note.placeholder = 'Note (optional)';
    var drop = el('button', 'og66-btn dim', 'Drop at my location'); drop.type = 'button';
    drop.addEventListener('click', function () {
      if (!navigator.geolocation) return;
      navigator.geolocation.getCurrentPosition(function (p) {
        lat.value = p.coords.latitude.toFixed(6);
        lon.value = p.coords.longitude.toFixed(6);
      }, function () { /* manual entry still works */ }, { enableHighAccuracy: true });
    });
    var add = el('button', 'og66-btn', 'Add point'); add.type = 'button';
    add.addEventListener('click', function () {
      post('/gps/points', {
        name: name.value, lat: parseFloat(lat.value),
        lon: parseFloat(lon.value), note: note.value
      }).then(function (res) {
        if (res.ok) { name.value = ''; note.value = ''; refreshPoints(body); }
      });
    });
    var exp = el('button', 'og66-btn dim', 'Export CSV'); exp.type = 'button';
    exp.addEventListener('click', function () {
      window.location.href = '/gps/points/export';
    });
    body.appendChild(name); body.appendChild(lat); body.appendChild(lon);
    body.appendChild(note); body.appendChild(drop); body.appendChild(add);
    body.appendChild(exp);
    var stakeBox = el('div'); stakeBox.id = 'og66Stake'; body.appendChild(stakeBox);
    var list = el('div'); list.id = 'og66PtList'; body.appendChild(list);
    refreshPoints(body);
  }

  /* ---------------- Section shell ---------------- */
  function renderTab() {
    var body = $('og66Body');
    if (!body) return;
    var dBtn = $('og66TabDrive'), sBtn = $('og66TabSites');
    if (dBtn) dBtn.className = 'og66-tab' + (tab === 'drive' ? ' on' : '');
    if (sBtn) sBtn.className = 'og66-tab' + (tab === 'sites' ? ' on' : '');
    if (tab === 'drive') buildDrive(body); else buildSites(body);
  }

  function buildSection() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    head = el('h3', 'og-menu-sec', 'GPS');
    sec = el('nav', 'og-menu-card', '');
    sec.id = 'ogGpsCard';
    var tabs = el('div', 'og66-tabs');
    var dBtn = el('button', 'og66-tab on', 'Driving'); dBtn.type = 'button'; dBtn.id = 'og66TabDrive';
    var sBtn = el('button', 'og66-tab', 'Job sites'); sBtn.type = 'button'; sBtn.id = 'og66TabSites';
    dBtn.addEventListener('click', function () { tab = 'drive'; renderTab(); });
    sBtn.addEventListener('click', function () { tab = 'sites'; renderTab(); });
    tabs.appendChild(dBtn); tabs.appendChild(sBtn);
    var body = el('div', 'og66-body'); body.id = 'og66Body';
    sec.appendChild(tabs); sec.appendChild(body);
    card.parentNode.insertBefore(head, card.nextSibling);
    card.parentNode.insertBefore(sec, head.nextSibling);
    sec.hidden = true;
    head.hidden = true;
    renderTab();
    return true;
  }

  // Show/hide with the Account section (signed in), like R63/R65.
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

/* Round 66: GPS (web). Round 70 reshape (Brent's folder
 * rule): GPS gets the app's shape — a menu row opening its
 * own full view.
 *
 * One asset, zero page edits beyond the loader stub in
 * static/og_tokens.js (the R62/R63/R65 pattern; the page
 * sits at its push ceiling). The row is injected into the
 * side menu right after the Account card, under a "GPS"
 * section header, and shows only while signed in (the
 * R63/R65 visibility sync).
 *
 *   Construction — the job-site folder: the caller's
 *             saved points (GET /gps/points) with add
 *             (manual or "Drop at my location"), Convert
 *             (UTM + WA state plane), Stakeout (live
 *             geolocation watch through POST /gps/
 *             stakeout), distance-to (POST /gps/between),
 *             Edit (POST /gps/points/update) and Delete;
 *             a CSV panel — Export (GET /gps/points/
 *             export) and Import (POST /gps/points/
 *             import, R67) showing the server's own
 *             added/skipped counts; a blueprint reader
 *             (image sheets with pan/zoom; a PDF gets the
 *             app's honest "export as PNG/JPG" line); and
 *             Procore / Bluebeam rows that open those
 *             sites in OG's browser via the seeded chat
 *             ask — the same machinery as typing it.
 *
 * Round 90 (Brent's phone-first order): the Driving view
 * that used to live here — the route summary page, the
 * from/to form, the "Open in Maps" button, and the
 * Round 73 account location switch the view carried —
 * is DELETED whole. Driving is asked in chat now: OG
 * works out the route (the server flow is unchanged)
 * and the chat answer itself carries the Open in Maps
 * handoff (og_r90.js), while the location switch's
 * home moved to the Permissions folder (also og_r90.js).
 * This file is Construction only; nothing in the
 * Construction section changed in this round.
 *
 * All math comes from the server (og_gps) so the web and
 * the app agree digit for digit.
 */
(function () {
  'use strict';

  var css = ''
    + '.og66-body{padding:4px 2px 14px;color:#ececec}'
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
    + '.og66-note{color:#999;font-size:.88em;padding:6px 0;margin:0}'
    + '.og66-sec{margin:22px 4px 8px;font-size:.74rem;font-weight:700;'
    + 'letter-spacing:1.6px;text-transform:uppercase;color:#8f8f8f}'
    + '.og66-sec:first-child{margin-top:2px}'
    + '.og66-chip{display:inline-block;padding:5px 12px;border-radius:999px;'
    + 'border:1px solid #3a3a3a;color:#d8d8d8;font-size:.85em;margin:4px 0}'
    + '.og66-chip.on{border-color:rgba(255,193,7,.65);color:#ffc107}'
    + '.og66-bp{position:relative;height:52vh;min-height:300px;'
    + 'overflow:hidden;border:1px solid #2c2c2c;border-radius:16px;'
    + 'background:#101010;touch-action:none;margin:6px 0}'
    + '.og66-bp img{position:absolute;left:0;top:0;max-width:none;'
    + 'transform-origin:0 0;user-select:none;-webkit-user-drag:none}'
    + '.og66-site{display:block;width:100%;box-sizing:border-box;'
    + 'padding:15px 16px;margin:6px 0;background:#171717;'
    + 'border:1px solid #262626;border-radius:16px;color:#ececec;'
    + 'font-family:inherit;font-size:1rem;text-align:left;cursor:pointer}'
    + '.og66-site:hover{background:#1f1f1f}'
    + '.og66-site .og66-small{display:block;margin-top:2px}'
    + 'label.og66-btn{display:inline-block}';
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

  var sec = null, head = null;
  var points = [];
  var stakeWatch = null;
  var stakeTarget = null;

  /* ---------------- Construction: job sites ---------------- */
  function refreshPoints() {
    api('/gps/points').then(function (res) {
      points = (res.d && res.d.points) || [];
      renderPoints();
    }).catch(function () { /* list stays as-is */ });
  }

  function renderPoints() {
    var list = $('og66PtList');
    if (!list) return;
    list.innerHTML = '';
    if (!points.length) {
      list.appendChild(el('p', 'og66-note', 'No points saved yet.'));
      return;
    }
    points.forEach(function (pt) {
      var row = el('div', 'og66-pt');
      row.appendChild(el('b', 'name', pt.name));
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
      stake.addEventListener('click', function () { startStakeout(pt); });
      var edit = el('button', 'og66-btn dim', 'Edit'); edit.type = 'button';
      var editBox = el('div');
      edit.addEventListener('click', function () {
        editBox.innerHTML = '';
        var nm = el('input'); nm.value = pt.name;
        var nt = el('input'); nt.value = pt.note || '';
        nt.placeholder = 'Note (optional)';
        var save = el('button', 'og66-btn', 'Save'); save.type = 'button';
        save.addEventListener('click', function () {
          post('/gps/points/update', {
            id: pt.id, name: nm.value, note: nt.value
          }).then(function (res) {
            if (res.ok) refreshPoints();
            else editBox.appendChild(el('div', 'og66-small',
              (res.d && res.d.detail) || 'That edit did not save.'));
          });
        });
        var cancel = el('button', 'og66-btn dim', 'Cancel'); cancel.type = 'button';
        cancel.addEventListener('click', function () { editBox.innerHTML = ''; });
        editBox.appendChild(nm); editBox.appendChild(nt);
        editBox.appendChild(save); editBox.appendChild(cancel);
      });
      var del = el('button', 'og66-btn dim', 'Delete'); del.type = 'button';
      del.addEventListener('click', function () {
        post('/gps/points/delete', { id: pt.id }).then(function () { refreshPoints(); });
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
            + ' at ' + Math.round(res.d.bearing_deg) + '° to ' + other.name
            + ' · back ' + Math.round(res.d.bearing_back_deg) + '°';
        });
      });
      row.appendChild(readout);
      row.appendChild(conv); row.appendChild(stake);
      row.appendChild(edit); row.appendChild(del);
      row.appendChild(picker); row.appendChild(distOut);
      row.appendChild(editBox);
      list.appendChild(row);
    });
  }

  function startStakeout(pt) {
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

  /* ---------------- Construction: CSV ---------------- */
  function buildCsv(wrap) {
    wrap.appendChild(el('h3', 'og66-sec', 'Point file (CSV)'));
    var exp = el('button', 'og66-btn dim', 'Export CSV'); exp.type = 'button';
    exp.addEventListener('click', function () {
      window.location.href = '/gps/points/export';
    });
    var lab = el('label', 'og66-btn dim', 'Import CSV');
    lab.setAttribute('for', 'og66CsvFile');
    var file = el('input'); file.type = 'file'; file.id = 'og66CsvFile';
    file.accept = '.csv,text/csv,text/plain';
    file.hidden = true;
    var out = el('p', 'og66-note',
      'Import takes the same file Export makes: name, lat, lon, note.');
    out.id = 'og66CsvOut';
    file.addEventListener('change', function () {
      var f = file.files && file.files[0];
      if (!f) return;
      out.textContent = 'Importing ' + f.name + '…';
      f.text().then(function (text) {
        return post('/gps/points/import', { csv: text });
      }).then(function (res) {
        if (res.ok && res.d && res.d.ok) {
          out.textContent = 'Imported ' + res.d.added + ' points, skipped '
            + res.d.skipped + ' rows.';
          refreshPoints();
        } else {
          out.textContent = (res.d && (res.d.detail || res.d.error))
            || 'That file did not import.';
        }
      }).catch(function () {
        out.textContent = 'That file did not import.';
      });
      file.value = '';
    });
    wrap.appendChild(exp); wrap.appendChild(lab);
    wrap.appendChild(file); wrap.appendChild(out);
  }

  /* ---------------- Construction: blueprint reader ---------------- */
  function buildBlueprint(wrap) {
    wrap.appendChild(el('h3', 'og66-sec', 'Blueprint reader'));
    var lab = el('label', 'og66-btn dim', 'Open a blueprint sheet');
    lab.setAttribute('for', 'og66BpFile');
    var file = el('input'); file.type = 'file'; file.id = 'og66BpFile';
    file.accept = 'image/*,.pdf';
    file.hidden = true;
    var msg = el('p', 'og66-note',
      'Open a blueprint as a picture (PNG or JPG) — drag to pan, '
      + 'zoom with the buttons.');
    msg.id = 'og66BpMsg';
    var viewer = el('div', 'og66-bp'); viewer.id = 'og66BpView';
    var img = el('img'); img.id = 'og66BpImg'; img.hidden = true;
    img.alt = 'Blueprint sheet';
    viewer.appendChild(img);
    var zOut = el('button', 'og66-btn dim', '−'); zOut.type = 'button';
    var zIn = el('button', 'og66-btn dim', '+'); zIn.type = 'button';
    var zReset = el('button', 'og66-btn dim', 'Reset view'); zReset.type = 'button';
    var scale = 1, tx = 0, ty = 0;
    function apply() {
      img.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    }
    zIn.addEventListener('click', function () { scale = Math.min(8, scale * 1.25); apply(); });
    zOut.addEventListener('click', function () { scale = Math.max(0.2, scale / 1.25); apply(); });
    zReset.addEventListener('click', function () { scale = 1; tx = 0; ty = 0; apply(); });
    var drag = null;
    viewer.addEventListener('pointerdown', function (e) {
      drag = { x: e.clientX, y: e.clientY };
    });
    viewer.addEventListener('pointermove', function (e) {
      if (!drag) return;
      tx += e.clientX - drag.x;
      ty += e.clientY - drag.y;
      drag = { x: e.clientX, y: e.clientY };
      apply();
    });
    viewer.addEventListener('pointerup', function () { drag = null; });
    viewer.addEventListener('pointercancel', function () { drag = null; });
    file.addEventListener('change', function () {
      var f = file.files && file.files[0];
      if (!f) return;
      var isPdf = f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
      if (isPdf) {
        img.hidden = true;
        msg.textContent = 'PDF sheets can\'t open in the reader yet — '
          + 'export the sheet as a PNG or JPG and open that.';
      } else if (f.type.indexOf('image/') !== 0) {
        img.hidden = true;
        msg.textContent = 'That file isn\'t an image — blueprints open '
          + 'here as a PNG or JPG picture.';
      } else {
        img.src = URL.createObjectURL(f);
        img.hidden = false;
        scale = 1; tx = 0; ty = 0; apply();
        msg.textContent = f.name + ' — drag to pan, + / − to zoom.';
      }
      file.value = '';
    });
    wrap.appendChild(lab); wrap.appendChild(file);
    wrap.appendChild(msg); wrap.appendChild(viewer);
    wrap.appendChild(zOut); wrap.appendChild(zIn); wrap.appendChild(zReset);
  }

  /* ---------------- Construction: open in OG's browser ---------------- */
  function openSite(host) {
    if (window.OG70) window.OG70.closeView();
    var mc = $('menuClose');
    if (mc) mc.click();
    var input = $('messageInput');
    if (input) {
      input.value = 'Open ' + host;
      input.focus();
    }
    if (typeof window.sendMessage === 'function') {
      window.sendMessage(true);
    }
  }

  function buildSites(wrap) {
    wrap.appendChild(el('p', 'og66-note',
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
        if (res.ok) { name.value = ''; note.value = ''; refreshPoints(); }
      });
    });
    wrap.appendChild(name); wrap.appendChild(lat); wrap.appendChild(lon);
    wrap.appendChild(note); wrap.appendChild(drop); wrap.appendChild(add);
    var stakeBox = el('div'); stakeBox.id = 'og66Stake'; wrap.appendChild(stakeBox);
    var list = el('div'); list.id = 'og66PtList'; wrap.appendChild(list);
    refreshPoints();
  }

  function buildConstruction(body, view) {
    var wrap = el('div', 'og66-body');
    body.appendChild(wrap);
    wrap.appendChild(el('h3', 'og66-sec', 'Job sites'));
    buildSites(wrap);
    buildCsv(wrap);
    buildBlueprint(wrap);
    wrap.appendChild(el('h3', 'og66-sec', "Open in OG's browser"));
    var pro = el('button', 'og66-site', 'Procore'); pro.type = 'button';
    pro.appendChild(el('span', 'og66-small', 'app.procore.com — opens in OG\'s browser'));
    pro.addEventListener('click', function () { openSite('app.procore.com'); });
    var blu = el('button', 'og66-site', 'Bluebeam'); blu.type = 'button';
    blu.appendChild(el('span', 'og66-small', 'bluebeam.com — opens in OG\'s browser'));
    blu.addEventListener('click', function () { openSite('bluebeam.com'); });
    wrap.appendChild(pro); wrap.appendChild(blu);
    view.track(function () {
      if (stakeWatch !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(stakeWatch);
        stakeWatch = null;
      }
      stakeTarget = null;
    });
  }

  function openConstruction() {
    openView({ title: 'Construction', wide: true, onOpen: buildConstruction });
  }

  /* ---------------- Menu rows ---------------- */
  function buildSection() {
    var card = $('menuAccountCard');
    if (!card || sec) return !!sec;
    head = el('h3', 'og-menu-sec', 'GPS');
    sec = el('nav', 'og-menu-card', '');
    sec.id = 'ogGpsCard';
    var cRow = el('button', 'og-menu-item', '');
    cRow.type = 'button'; cRow.id = 'og66ConstructionRow';
    var cIco = el('span', 'og-mi', '');
    cIco.innerHTML = '<svg><use href="#i-folder"/></svg>';
    cRow.appendChild(cIco);
    cRow.appendChild(el('span', '', 'Construction'));
    cRow.addEventListener('click', openConstruction);
    sec.appendChild(cRow);
    card.parentNode.insertBefore(head, card.nextSibling);
    card.parentNode.insertBefore(sec, head.nextSibling);
    sec.hidden = true;
    head.hidden = true;
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

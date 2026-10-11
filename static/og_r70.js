/* Round 70: shared full-view scaffold + hand-rolled OSM map (web).
 *
 * Brent (2026-10-11): the website gets the same folders the
 * app got — Driving and Construction as their own views, a
 * single Languages page — and every page carries an ✕ at the
 * top right (his standing rule). The Library overlay is the
 * visual model (fixed full-screen page, gold-on-black, title
 * + ✕ top bar, centered column); this asset is that scaffold
 * shared by og_r65 (Languages) and og_r66 (Driving /
 * Construction) so the three views can't drift apart.
 *
 * The map is the app's fix60/66 pattern ported to plain JS —
 * OpenStreetMap slippy tiles as <img>s, world-pixel math, an
 * SVG overlay for the route line and markers, a blue dot for
 * the visitor's own position. NO library, NO new dependency:
 * the front-end has never carried a map engine and this round
 * doesn't start. Web turn-by-turn is NOT here — the Driving
 * view shows map + route; live navigation is the app's job,
 * and the view says so.
 */
(function () {
  'use strict';
  if (window.OG70) return;

  var css = ''
    + '.og70-view{position:fixed;inset:0;z-index:9200;'
    + 'background:radial-gradient(120% 90% at 50% 0%,#171307 0%,#0b0b0b 55%);'
    + 'display:flex;flex-direction:column;'
    + 'padding:calc(14px + env(safe-area-inset-top)) 20px '
    + 'calc(20px + env(safe-area-inset-bottom));overflow:hidden}'
    + '.og70-view[hidden]{display:none!important}'
    + '.og70-view [hidden]{display:none!important}'
    + '.og70-top{width:min(680px,100%);margin:0 auto;display:flex;'
    + 'align-items:center;gap:14px;flex:0 0 auto}'
    + '.og70-top.wide{width:min(960px,100%)}'
    + '.og70-top h2{margin:0;font-size:1.55rem;line-height:1.1;'
    + 'color:#f5f5f5;flex:1 1 auto}'
    + '.og70-close{margin-left:auto}'
    + '.og70-scroll{width:min(680px,100%);margin:0 auto;overflow-y:auto;'
    + 'min-height:0;padding:4px 0 12px;flex:1 1 auto}'
    + '.og70-scroll.wide{width:min(960px,100%)}'
    + '@media (max-width:560px){.og70-view{padding-left:14px;'
    + 'padding-right:14px}.og70-top h2{font-size:1.38rem}}'
    /* the map */
    + '.og70-map{position:relative;overflow:hidden;border-radius:16px;'
    + 'border:1px solid #2c2c2c;background:#141414;touch-action:none;'
    + 'min-height:280px}'
    + '.og70-map img.tile{position:absolute;width:256px;height:256px;'
    + 'border:0;user-select:none;-webkit-user-drag:none}'
    + '.og70-map svg.overlay{position:absolute;inset:0;width:100%;'
    + 'height:100%;pointer-events:none}'
    + '.og70-map-attr{position:absolute;right:6px;bottom:4px;'
    + 'font-size:.62rem;color:#cfcfcf;background:rgba(0,0,0,.55);'
    + 'padding:1px 6px;border-radius:6px;pointer-events:none}'
    + '.og70-map-zoom{position:absolute;right:10px;top:10px;'
    + 'display:flex;flex-direction:column;gap:6px}'
    + '.og70-map-zoom button{width:38px;height:38px;border-radius:50%;'
    + 'border:1px solid #3a3a3a;background:rgba(17,17,17,.92);'
    + 'color:#ececec;font-size:1.25rem;font-family:inherit;'
    + 'cursor:pointer;line-height:1}'
    + '.og70-map-zoom button:hover{border-color:rgba(255,193,7,.7);'
    + 'color:#ffc107}';
  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  /* ---------------- full-view scaffold ---------------- */
  var current = null;   // {root, cleanups:[], closed}

  function closeView() {
    if (!current) return;
    var v = current;
    current = null;
    v.cleanups.forEach(function (fn) {
      try { fn(); } catch (e) { /* cleanup never throws */ }
    });
    if (v.root && v.root.parentNode) v.root.parentNode.removeChild(v.root);
    document.removeEventListener('keydown', onKey, true);
  }

  function onKey(e) {
    if (e.key === 'Escape') closeView();
  }

  /* openView({title, wide, onOpen(body, view), onClose}) —
   * body is the scroll column to fill; view.track(fn) registers
   * cleanup (watches, listeners) run when the view closes. */
  function openView(opts) {
    closeView();
    var root = el('section', 'og70-view');
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', opts.title || '');
    var top = el('div', 'og70-top' + (opts.wide ? ' wide' : ''));
    var closeBtn = el('button', 'og-circle-btn og70-close', '✕');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    top.appendChild(el('h2', '', opts.title || ''));
    top.appendChild(closeBtn);
    var body = el('div', 'og70-scroll' + (opts.wide ? ' wide' : ''));
    root.appendChild(top);
    root.appendChild(body);
    document.body.appendChild(root);
    var view = {
      root: root,
      body: body,
      cleanups: [],
      track: function (fn) { view.cleanups.push(fn); },
      close: closeView
    };
    if (opts.onClose) view.track(opts.onClose);
    closeBtn.addEventListener('click', closeView);
    current = view;
    document.addEventListener('keydown', onKey, true);
    if (opts.onOpen) opts.onOpen(body, view);
    return view;
  }

  /* ---------------- slippy-map math (fix60's, in JS) ---------------- */
  function worldPx(lat, lon, zoom) {
    var clamped = Math.max(-85.0511, Math.min(85.0511, lat));
    var n = Math.pow(2, zoom);
    var latRad = clamped * Math.PI / 180;
    return {
      x: (lon + 180) / 360 * n * 256,
      y: (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n * 256
    };
  }
  function latLonFromPx(px, py, zoom) {
    var n = Math.pow(2, zoom) * 256;
    var lon = px / n * 360 - 180;
    var latRad = Math.atan(Math.sinh(Math.PI * (1 - 2 * py / n)));
    return { lat: latRad * 180 / Math.PI, lon: lon };
  }

  /* ---------------- the map ----------------
   * createMap(container, {lat, lon, zoom}) — container is the
   * .og70-map box (caller sets its height). Methods: setCenter,
   * setZoom/zoomIn/zoomOut, setRoute({from,to}|null),
   * setPoints([{lat,lon}]), setUser({lat,lon}|null), refresh,
   * destroy. Drag pans; the wheel is left to the page scroll. */
  function createMap(container, opts) {
    opts = opts || {};
    var zoom = Math.max(11, Math.min(18, opts.zoom || 15));
    var center = (opts.lat != null && opts.lon != null)
      ? { lat: opts.lat, lon: opts.lon } : null;
    var route = null, points = [], user = null;
    var tiles = {};   // "z/x/y" -> img
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'overlay');
    var zoomBox = el('div', 'og70-map-zoom');
    var zin = el('button', '', '+'); zin.type = 'button';
    zin.setAttribute('aria-label', 'Zoom in');
    var zout = el('button', '', '−'); zout.type = 'button';
    zout.setAttribute('aria-label', 'Zoom out');
    zoomBox.appendChild(zin); zoomBox.appendChild(zout);
    var attr = el('div', 'og70-map-attr', '© OpenStreetMap contributors');
    container.appendChild(svg);
    container.appendChild(zoomBox);
    container.appendChild(attr);

    function screenPt(lat, lon, tl) {
      var p = worldPx(lat, lon, zoom);
      return { x: p.x - tl.x, y: p.y - tl.y };
    }

    function render() {
      if (!center) return;
      var w = container.clientWidth, h = container.clientHeight;
      if (!w || !h) return;
      var c = worldPx(center.lat, center.lon, zoom);
      var tl = { x: c.x - w / 2, y: c.y - h / 2 };
      var n = Math.pow(2, zoom);
      var x0 = Math.floor(tl.x / 256), x1 = Math.floor((tl.x + w) / 256);
      var y0 = Math.max(0, Math.floor(tl.y / 256));
      var y1 = Math.min(n - 1, Math.floor((tl.y + h) / 256));
      var want = {};
      for (var tx = x0; tx <= x1; tx++) {
        for (var ty = y0; ty <= y1; ty++) {
          var wx = ((tx % n) + n) % n;
          var key = zoom + '/' + wx + '/' + ty;
          want[key] = true;
          var img = tiles[key];
          if (!img) {
            img = document.createElement('img');
            img.className = 'tile';
            img.alt = '';
            img.src = 'https://tile.openstreetmap.org/' + key + '.png';
            img.onerror = function () { this.style.display = 'none'; };
            tiles[key] = img;
            container.insertBefore(img, svg);
          }
          img.style.left = (tx * 256 - tl.x) + 'px';
          img.style.top = (ty * 256 - tl.y) + 'px';
          img.style.display = '';
        }
      }
      Object.keys(tiles).forEach(function (k) {
        if (!want[k]) {
          container.removeChild(tiles[k]);
          delete tiles[k];
        }
      });
      /* overlay: route line, points, route ends, user dot */
      var parts = [];
      function dot(p, r, fill, stroke) {
        parts.push('<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1)
          + '" r="' + r + '" fill="' + fill + '" stroke="' + stroke
          + '" stroke-width="2"/>');
      }
      if (route && route.from && route.to) {
        var a = screenPt(route.from.lat, route.from.lon, tl);
        var b = screenPt(route.to.lat, route.to.lon, tl);
        parts.push('<line x1="' + a.x.toFixed(1) + '" y1="' + a.y.toFixed(1)
          + '" x2="' + b.x.toFixed(1) + '" y2="' + b.y.toFixed(1)
          + '" stroke="#3b82f6" stroke-width="4" stroke-linecap="round"'
          + ' opacity="0.9"/>');
      }
      points.forEach(function (pt) {
        dot(screenPt(pt.lat, pt.lon, tl), 4, '#d8d8d8', '#111');
      });
      if (route && route.from && route.to) {
        dot(screenPt(route.from.lat, route.from.lon, tl), 6, '#ffc107', '#111');
        dot(screenPt(route.to.lat, route.to.lon, tl), 6, '#ffc107', '#111');
      }
      if (user) {
        dot(screenPt(user.lat, user.lon, tl), 7, '#2f7bff', '#fff');
      }
      svg.innerHTML = parts.join('');
    }

    /* drag pan */
    var drag = null;
    function onDown(e) {
      drag = { x: e.clientX, y: e.clientY };
      container.setPointerCapture && e.pointerId != null &&
        container.setPointerCapture(e.pointerId);
    }
    function onMove(e) {
      if (!drag || !center) return;
      var c = worldPx(center.lat, center.lon, zoom);
      var moved = latLonFromPx(c.x - (e.clientX - drag.x),
        c.y - (e.clientY - drag.y), zoom);
      center = { lat: moved.lat, lon: moved.lon };
      drag = { x: e.clientX, y: e.clientY };
      render();
    }
    function onUp() { drag = null; }
    container.addEventListener('pointerdown', onDown);
    container.addEventListener('pointermove', onMove);
    container.addEventListener('pointerup', onUp);
    container.addEventListener('pointercancel', onUp);
    zin.addEventListener('click', function () { api.zoomIn(); });
    zout.addEventListener('click', function () { api.zoomOut(); });
    function onResize() { render(); }
    window.addEventListener('resize', onResize);

    var api = {
      setCenter: function (lat, lon) {
        center = { lat: lat, lon: lon };
        render();
      },
      center: function () { return center; },
      setZoom: function (z) {
        zoom = Math.max(11, Math.min(18, z));
        render();
      },
      zoomIn: function () { api.setZoom(zoom + 1); },
      zoomOut: function () { api.setZoom(zoom - 1); },
      setRoute: function (r) { route = r; render(); },
      setPoints: function (pts) { points = pts || []; render(); },
      setUser: function (u) { user = u; render(); },
      refresh: render,
      destroy: function () {
        window.removeEventListener('resize', onResize);
        container.removeEventListener('pointerdown', onDown);
        container.removeEventListener('pointermove', onMove);
        container.removeEventListener('pointerup', onUp);
        container.removeEventListener('pointercancel', onUp);
      }
    };
    render();
    return api;
  }

  window.OG70 = {
    openView: openView,
    closeView: closeView,
    createMap: createMap,
    _worldPx: worldPx,
    _latLonFromPx: latLonFromPx
  };
})();

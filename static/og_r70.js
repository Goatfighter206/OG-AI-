/* Round 70: shared full-view scaffold (web).
 *
 * Brent (2026-10-11): the website gets the same folders the
 * app got — Driving and Construction as their own views, a
 * single Languages page — and every page carries an ✕ at the
 * top right (his standing rule). The Library overlay is the
 * visual model (fixed full-screen page, gold-on-black, title
 * + ✕ top bar, centered column); this asset is that scaffold
 * shared by og_r65 (Languages) and og_r66 (Driving /
 * Construction) so the views can't drift apart.
 *
 * Round 82 removed the hand-rolled OSM map that used to
 * live here (Brent's phone-first rule: anything the phone
 * already does, OG hands off to the phone). The Driving
 * view is a route summary + an "Open in Maps" handoff to
 * the phone's maps app now; this file is scaffold only.
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
    + 'padding-right:14px}.og70-top h2{font-size:1.38rem}}';
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
    var closeBtn = el('button', 'og-circle-btn og70-close');
    // Round 87: the glyph is the canonical inline SVG (the
    // seat screens' 21px drawing), not a text '✕' — every
    // ✕ on the web face is the same button now.
    closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
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

  window.OG70 = {
    openView: openView,
    closeView: closeView
  };
})();

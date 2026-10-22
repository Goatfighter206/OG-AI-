/* OG Markets renderer — the Crypto Hub ('crypto') surface,
   registered with the scaffold (window.OGDS). Loaded after
   og_dashboards.js. The Financial Smart Agent is NOT here
   any more: since Ticket L4 it is a true separate page at
   /fin (og_finpage.py + static/og_finpage.js), and its
   renderFin surface is retired — there is exactly one way
   the Financial page exists.

   House rules baked in:
   - Real data only, from /cryptohub/*; when the
     server says unavailable, the surface shows the honest line.
   - The trade panel never trades: its one action closes the
     surface and prefills the chat composer, so the existing
     chat trade-sheet + approval flow owns the trade.
   - Scope cut (Brent, 2026-10-22): the hub is the
     user’s own money only — Your Coins, Bid Ranges,
     the trade hand-off, Games. There is NO live-prices
     board, NO paper-trading simulator, and NO
     watch-only address list on this surface; they
     were removed outright, not hidden.
   - Refresh runs only while a surface is open and stops when
     the surface body goes away / stays hidden. */
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
               "'": '&#39;' }[c];
    });
  }
  function money(v, dec) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return '$' + n.toLocaleString('en-US', {
      minimumFractionDigits: dec == null ? 2 : dec,
      maximumFractionDigits: dec == null ? 2 : dec });
  }
  function pct(v) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }
  function cls(v) { return Number(v) >= 0 ? 'ogm-up' : 'ogm-down'; }
  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json();
    });
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || ('http ' + r.status));
        return d;
      });
    });
  }

  /* Refresh-while-open: one timer per surface; the tick stops
     itself when the body element is gone or stays hidden. */
  var timers = {};
  function startTimer(name, bodyEl, fn) {
    stopTimer(name);
    var hiddenTicks = 0;
    timers[name] = setInterval(function () {
      if (!bodyEl.isConnected) { stopTimer(name); return; }
      if (bodyEl.offsetParent === null) {
        hiddenTicks += 1;
        if (hiddenTicks >= 2) { stopTimer(name); return; }
        return;
      }
      hiddenTicks = 0;
      fn();
    }, 60000);
  }
  function stopTimer(name) {
    if (timers[name]) { clearInterval(timers[name]); delete timers[name]; }
  }

  /* The trade hand-off: close the surface, drop the ask into the
     chat composer (#messageInput), focus it. The visitor sends
     it themselves; chat's approval flow takes it from there. */
  function handoffToChat(text) {
    try {
      if (window.OGDS && typeof window.OGDS.close === 'function') {
        window.OGDS.close();
      }
    } catch (e) { /* surface close is best-effort */ }
    var input = document.getElementById('messageInput');
    if (input) {
      input.value = text;
      try {
        input.dispatchEvent(new Event('input', { bubbles: true }));
      } catch (e) { /* older engines */ }
      input.focus();
    }
  }

  /* ================= CRYPTO HUB ================= */

  function renderCrypto(bodyEl) {
    stopTimer('crypto');
    bodyEl.innerHTML =
      '<div class="ogm-wrap">' +
      '<h2 class="ogm-title">Crypto Hub</h2>' +
      '<p class="ogm-sub">Your coins and what they\u2019re ' +
      'worth, and bid ranges you arm yourself. ' +
      'Real abilities only.</p>' +

      '<section class="ogm-card ogc-hub"><h3>Your Coins</h3>' +
      '<div data-m="holdings"><p class="ogm-note">Checking ' +
      'your Coinbase connection…</p></div>' +
      '<div class="ogm-asof" data-m="asof"></div></section>' +

      '<section class="ogm-card ogc-hub"><h3>Bid Ranges</h3>' +
      '<div data-m="ranges"><p class="ogm-note">Loading your ' +
      'bid ranges…</p></div></section>' +

      '<section class="ogm-card"><h3>Trade</h3>' +
      '<div data-m="trade"></div></section>' +

      '<section class="ogm-card"><h3>Games</h3>' +
      '<div class="ogm-soon"><span class="ogm-tag">COMING SOON</span>' +
      '<div class="ogm-metric"><span>Crypto Trading Simulator</span>' +
      '<b>COMING SOON</b></div>' +
      '<div class="ogm-metric"><span>Blockchain Puzzle Challenge</span>' +
      '<b>COMING SOON</b></div>' +
      '<p class="ogm-note">The games are not built yet, so there ' +
      'is nothing to play — and nothing here costs or pays real ' +
      'money.</p></div></section>' +
      '</div>';

    var hub = { prices: [] };

    function q(sel) { return bodyEl.querySelector('[data-m="' + sel + '"]'); }

    /* Ticket M: the hub leads with YOUR COINS — compact,
       holdings-only. The full core price list stays in the
       payload (hub.prices) as feed data for the trade
       hand-off's coin list and the bid-range form; since
       the 2026-10-22 scope cut it is not rendered as a
       board anywhere — there is no board. */
    function renderPrices(data) {
      hub.prices = data.prices || [];
      renderHoldings(data);
      q('asof').textContent = data.as_of
        ? ('As of ' + data.as_of) : '';
      renderTrade(data.portfolio || {});
    }

    function renderHoldings(data) {
      var pf = data.portfolio || {};
      var box = q('holdings');
      if (!box) return;
      if (pf.state !== 'connected') {
        var html = '<p class="ogm-honest">' +
          esc(pf.note || '') + '</p>';
        if (pf.state === 'disconnected') {
          html += '<p style="margin-top:10px"><a class="ogm-link" ' +
            'href="/auth/coinbase">Connect Coinbase</a> — the ' +
            'connect itself runs through Coinbase\u2019s own ' +
            'sign-in.</p>';
        }
        box.innerHTML = html;
        return;
      }
      var hv = pf.holdings || {};
      var rows = hv.rows || [];
      if (!rows.length) {
        box.innerHTML = '<p class="ogm-note">Coinbase reports ' +
          'no balances above zero — when you hold a coin, it ' +
          'shows here with its live price.</p>' +
          '<p class="ogm-note">' + esc(pf.note || '') + '</p>';
        return;
      }
      var html = '';
      if (hv.total_usd != null) {
        html += '<div class="ogm-metric ogc-total"><span>' +
          'Total balance</span><b>' + money(hv.total_usd) +
          '</b></div>';
        if (!hv.values_complete) {
          html += '<p class="ogm-note">Total counts only the ' +
            'coins with a live price right now — a coin whose ' +
            'feed is down keeps its balance row but adds ' +
            'nothing to the total.</p>';
        }
      }
      html += rows.map(function (h) {
        var priceTxt = h.price == null ? 'price unavailable'
          : money(h.price, h.price < 1 ? 4 : 2) +
            (h.change_24h == null ? ''
              : ' · ' + pct(h.change_24h));
        var valTxt = h.value_usd == null ? '—'
          : money(h.value_usd);
        return '<div class="ogm-metric"><span><b>' +
          esc(h.symbol) + '</b> · ' + priceTxt + '<br>' +
          '<span class="ogm-note">' + esc(h.balance) + ' ' +
          esc(h.symbol) + '</span></span><b>' + valTxt +
          '</b></div>';
      }).join('');
      html += '<p class="ogm-note">' + esc(pf.note || '') +
        '</p>';
      box.innerHTML = html;
    }

    /* Ticket M: bid ranges — arm / disarm / remove your
       own per-coin ranges, watch their live band state,
       and read trigger reports. The panel paints the
       server's execution note verbatim: while execution
       is not live, a trigger is a REPORT, never a trade,
       and this panel never pretends otherwise. */
    var ZONE_LABEL = {
      watching: 'Watching', buy_zone: 'In buy zone',
      sell_zone: 'In sell zone', no_price: 'No live price'
    };

    function rangeBoundsTxt(r) {
      var parts = [];
      if (r.buy_below != null) {
        parts.push('Buy \u2264 ' + money(r.buy_below));
      }
      if (r.sell_above != null) {
        parts.push('Sell \u2265 ' + money(r.sell_above));
      }
      return parts.join(' · ');
    }

    function renderRanges(d) {
      var box = q('ranges');
      if (!box) return;
      var ex = d.execution || {};
      var html = '<p class="ogm-honest">' +
        esc(ex.reason || '') + '</p>';
      var ranges = d.ranges || [];
      if (ranges.length) {
        html += ranges.map(function (r) {
          var priceTxt = r.price == null
            ? 'no live price' : money(r.price,
              r.price < 1 ? 4 : 2);
          var state = r.armed
            ? (ZONE_LABEL[r.zone] || r.zone) : 'Disarmed';
          return '<div class="ogc-range"><div>' +
            '<b>' + esc(r.symbol) + '</b> · ' + priceTxt +
            '<div class="ogm-note">' +
            esc(rangeBoundsTxt(r)) + ' — ' + esc(state) +
            '</div></div><div class="ogc-range-btns">' +
            (r.armed
              ? '<button class="ogm-btn-ghost" data-rdisarm="' +
                esc(r.symbol) + '">Disarm</button>'
              : '<button class="ogm-btn-ghost" data-rarm="' +
                esc(r.symbol) + '">Arm</button>') +
            '<button class="ogm-btn-ghost" data-rremove="' +
            esc(r.symbol) + '">Remove</button></div></div>';
        }).join('');
      } else {
        html += '<p class="ogm-note">No bid ranges yet. Arm ' +
          'one below: pick a coin, set a buy-at-or-below ' +
          'and/or a sell-at-or-above price, and OG watches ' +
          'the band for you.</p>';
      }
      var events = d.events || [];
      if (events.length) {
        html += '<h4 class="ogc-h">Trigger Reports</h4>' +
          events.map(function (e) {
            return '<div class="ogm-metric"><span>' +
              esc(e.symbol) + ' entered its ' + esc(e.side) +
              ' zone at ' + money(e.price) +
              '</span><b>' +
              (e.executed ? 'EXECUTED' : 'REPORTED ONLY') +
              '</b></div>';
          }).join('');
      }
      var syms = {};
      hub.prices.forEach(function (p) { syms[p.symbol] = 1; });
      ranges.forEach(function (r) { syms[r.symbol] = 1; });
      var pf = hub.portfolio || {};
      ((pf.holdings || {}).rows || []).forEach(function (h) {
        syms[h.symbol] = 1;
      });
      var opts = Object.keys(syms).sort().map(function (s) {
        return '<option value="' + esc(s) + '">' + esc(s) +
          '</option>';
      }).join('');
      html += '<div class="ogm-row" style="margin-top:10px">' +
        '<div><label class="ogm-label">Coin</label>' +
        (opts
          ? '<select class="ogm-select" data-m="r-sym">' + opts +
            '</select>'
          : '<input class="ogm-input" data-m="r-sym" ' +
            'maxlength="10" placeholder="e.g. BTC">') +
        '</div>' +
        '<div><label class="ogm-label">Buy at or below ' +
        '($)</label><input class="ogm-input" data-m="r-buy" ' +
        'type="number" min="0" step="any" placeholder="—"></div>' +
        '<div><label class="ogm-label">Sell at or above ' +
        '($)</label><input class="ogm-input" data-m="r-sell" ' +
        'type="number" min="0" step="any" placeholder="—"></div>' +
        '<button class="ogm-btn ogm-noflex" data-m="r-go">Arm ' +
        'range</button></div>' +
        '<p class="ogm-err" data-m="r-err"></p>';
      box.innerHTML = html;
      function wire(attr, fn) {
        box.querySelectorAll('[' + attr + ']').forEach(
          function (btn) {
            btn.addEventListener('click', function () {
              fn(btn.getAttribute(attr));
            });
          });
      }
      wire('data-rdisarm', function (sym) {
        rangePost({ action: 'disarm', symbol: sym });
      });
      wire('data-rremove', function (sym) {
        rangePost({ action: 'remove', symbol: sym });
      });
      wire('data-rarm', function (sym) {
        var rec = null;
        ranges.forEach(function (r) {
          if (r.symbol === sym) rec = r;
        });
        if (!rec) return;
        rangePost({ action: 'arm', symbol: sym,
          buy_below: rec.buy_below, sell_above: rec.sell_above });
      });
      var go = box.querySelector('[data-m="r-go"]');
      if (go) go.addEventListener('click', function () {
        var symEl = box.querySelector('[data-m="r-sym"]');
        var buyEl = box.querySelector('[data-m="r-buy"]');
        var sellEl = box.querySelector('[data-m="r-sell"]');
        var body = { action: 'arm',
          symbol: (symEl.value || '').trim().toUpperCase() };
        if (buyEl.value !== '') {
          body.buy_below = Number(buyEl.value);
        }
        if (sellEl.value !== '') {
          body.sell_above = Number(sellEl.value);
        }
        rangePost(body);
      });
    }

    function rangePost(body) {
      var err = q('r-err');
      postJSON('/cryptohub/range', body).then(renderRanges)
        .catch(function (e) {
          if (err) err.textContent = e.message;
        });
    }

    function loadRanges() {
      var box = q('ranges');
      if (!box) return;
      fetch('/cryptohub/range', { credentials: 'same-origin' })
        .then(function (r) {
          if (r.status === 401) {
            box.innerHTML = '<p class="ogm-honest">Sign in ' +
              'to arm bid ranges — a range is a standing ' +
              'instruction on your own account, so it lives ' +
              'behind your sign-in, and OG never shows it ' +
              'to anyone else.</p>';
            return null;
          }
          return r.ok ? r.json() : null;
        })
        .then(function (d) { if (d) renderRanges(d); })
        .catch(function () {
          box.innerHTML = '<p class="ogm-honest">Bid ranges ' +
            'are unavailable right now.</p>';
        });
    }

    function renderTrade(pf) {
      var box = q('trade');
      if (!box) return;
      if (!pf || pf.state !== 'connected') {
        box.innerHTML = '<p class="ogm-honest">' +
          esc((pf && pf.note) ||
            'Real trades run through chat, where OG prepares a ' +
            'trade sheet and you approve it before anything moves.') +
          '</p><p class="ogm-note">When you are ready, just ask in ' +
          'chat — e.g. \u201Cbuy $50 of BTC\u201D — and OG builds ' +
          'the preview there. Nothing trades from this panel.</p>';
        return;
      }
      var opts = hub.prices.map(function (p) {
        return '<option value="' + esc(p.symbol) + '">' +
          esc(p.symbol) + '</option>';
      }).join('');
      box.innerHTML =
        '<div class="ogm-row">' +
        '<div><label class="ogm-label">Side</label>' +
        '<select class="ogm-select" data-m="t-side">' +
        '<option value="buy">Buy</option>' +
        '<option value="sell">Sell</option></select></div>' +
        '<div><label class="ogm-label">Coin</label>' +
        '<select class="ogm-select" data-m="t-sym">' + opts +
        '</select></div>' +
        '<div><label class="ogm-label">Amount (USD)</label>' +
        '<input class="ogm-input" data-m="t-amt" type="number" ' +
        'min="1" step="any" placeholder="50"></div>' +
        '<button class="ogm-btn ogm-noflex" data-m="t-go">Ask OG ' +
        'in chat</button></div>' +
        '<p class="ogm-note">This hands the ask to chat: OG ' +
        'prepares a trade preview there and nothing moves ' +
        'without your approval on that preview.</p>';
      box.querySelector('[data-m="t-go"]').addEventListener('click',
        function () {
          var side = box.querySelector('[data-m="t-side"]').value;
          var sym = box.querySelector('[data-m="t-sym"]').value;
          var amt = box.querySelector('[data-m="t-amt"]').value;
          var n = Number(amt);
          if (!sym || !isFinite(n) || n <= 0) return;
          handoffToChat(side + ' $' + n + ' of ' + sym);
        });
    }

    function load() {
      getJSON('/cryptohub/data').then(function (data) {
        hub.portfolio = data.portfolio || {};
        renderPrices(data);
        loadRanges();
      }).catch(function () {
        q('holdings').innerHTML = '<p class="ogm-honest">' +
          'Your coins are unavailable right now — the ' +
          'hub gave nothing back, so OG shows no numbers ' +
          'rather than stale or made-up ones.</p>';
        renderTrade({});
        loadRanges();
      });
    }
    load();
    startTimer('crypto', bodyEl, load);
  }

  /* Registration with the scaffold — og_dashboards.js loads
     first; if it is somehow not there yet, retry briefly. */
  function register() {
    if (window.OGDS && typeof window.OGDS.registerSurface ===
        'function') {
      window.OGDS.registerSurface('crypto', renderCrypto);
      return true;
    }
    return false;
  }
  if (!register()) {
    var tries = 0;
    var iv = setInterval(function () {
      tries += 1;
      if (register() || tries > 20) clearInterval(iv);
    }, 250);
  }
})();

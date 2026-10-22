/* OG Markets renderers — Crypto Hub ('crypto') and Financial
   Smart Agent ('fin') surfaces, registered with Worker's A
   scaffold (window.OGDS). Loaded after og_dashboards.js.

   House rules baked in:
   - Real data only, from /cryptohub/* and /finagent/*; when the
     server says unavailable, the surface shows the honest line.
   - The trade panel never trades: its one action closes the
     surface and prefills the chat composer, so the existing
     chat trade-sheet + approval flow owns the trade.
   - The simulator is PAPER only, client-side, in localStorage.
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

  var paperKey = 'og_paper_sim_v1';
  function paperLoad() {
    try {
      var raw = localStorage.getItem(paperKey);
      if (raw) {
        var p = JSON.parse(raw);
        if (p && typeof p.cash === 'number' && p.holdings) return p;
      }
    } catch (e) { /* fall through to a fresh book */ }
    return { cash: 10000, holdings: {} };
  }
  function paperSave(p) {
    try { localStorage.setItem(paperKey, JSON.stringify(p)); }
    catch (e) { /* storage off — the sim still works this visit */ }
  }

  function renderCrypto(bodyEl) {
    stopTimer('crypto');
    bodyEl.innerHTML =
      '<div class="ogm-wrap">' +
      '<h2 class="ogm-title">Crypto Hub</h2>' +
      '<p class="ogm-sub">Live prices, your connected Coinbase ' +
      'portfolio, paper trading, and watch-only addresses. ' +
      'Real abilities only.</p>' +

      '<section class="ogm-card"><h3>Live Prices</h3>' +
      '<div data-m="prices"><p class="ogm-note">Loading live ' +
      'prices…</p></div><div class="ogm-asof" data-m="asof"></div>' +
      '</section>' +

      '<section class="ogm-card"><h3>Your Portfolio</h3>' +
      '<div data-m="portfolio"><p class="ogm-note">Checking ' +
      'your Coinbase connection…</p></div></section>' +

      '<section class="ogm-card"><h3>Trade</h3>' +
      '<div data-m="trade"></div></section>' +

      '<section class="ogm-card"><div class="ogm-paper-banner">' +
      'PAPER TRADING SIMULATOR — PAPER ONLY</div>' +
      '<p class="ogm-note">Virtual $10,000 PAPER cash. Nothing ' +
      'here is real money, nothing is connected to Coinbase, and ' +
      'no order ever leaves this page. Your paper book lives in ' +
      'this browser only.</p><div data-m="paper"></div></section>' +

      '<section class="ogm-card"><h3>Watch-Only Addresses</h3>' +
      '<p class="ogm-note">Public addresses you save just to ' +
      'watch. OG holds no keys for them and cannot move ' +
      'anything at them — balances are read from public ' +
      'explorers.</p><div data-m="watch"></div>' +
      '<div class="ogm-row" style="margin-top:10px">' +
      '<div><label class="ogm-label">Label</label>' +
      '<input class="ogm-input" data-m="w-label" maxlength="60" ' +
      'placeholder="e.g. Cold storage"></div>' +
      '<div><label class="ogm-label">Chain</label>' +
      '<select class="ogm-select" data-m="w-chain">' +
      '<option value="btc">BTC</option>' +
      '<option value="eth">ETH</option></select></div></div>' +
      '<div class="ogm-row" style="margin-top:8px">' +
      '<div><label class="ogm-label">Public address</label>' +
      '<input class="ogm-input" data-m="w-addr" ' +
      'placeholder="Paste a public address"></div>' +
      '<button class="ogm-btn ogm-noflex" data-m="w-add">Add</button>' +
      '</div><p class="ogm-err" data-m="w-err"></p></section>' +

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

    function renderPrices(data) {
      hub.prices = data.prices || [];
      var box = q('prices');
      if (!data.prices_available || !hub.prices.length) {
        box.innerHTML = '<p class="ogm-honest">Live prices are ' +
          'unavailable right now — the price feeds gave nothing ' +
          'back, so OG shows no numbers rather than stale or ' +
          'made-up ones.</p>';
      } else {
        var rows = hub.prices.map(function (p) {
          return '<tr><td><b>' + esc(p.symbol) + '</b> ' +
            esc(p.name) + '</td><td>' + money(p.usd, p.usd < 1 ? 4 : 2) +
            '</td><td class="' + cls(p.change_24h) + '">' +
            pct(p.change_24h) + '</td><td>' +
            (p.high == null ? '—' : money(p.high)) + '</td><td>' +
            (p.low == null ? '—' : money(p.low)) + '</td><td>' +
            esc(p.source) + '</td></tr>';
        }).join('');
        box.innerHTML = '<table class="ogm-table"><thead><tr>' +
          '<th>Coin</th><th>Price</th><th>24h</th><th>High</th>' +
          '<th>Low</th><th>Source</th></tr></thead><tbody>' +
          rows + '</tbody></table>';
      }
      q('asof').textContent = data.as_of
        ? ('As of ' + data.as_of) : '';
      renderTrade(data.portfolio || {});
      renderPaper();
    }

    function renderPortfolio(data) {
      var pf = data.portfolio || {};
      var box = q('portfolio');
      if (pf.state === 'connected') {
        var rows = (pf.accounts || []).map(function (a) {
          return '<div class="ogm-metric"><span>' +
            esc(a.name) + ' (' + esc(a.currency) + ')</span><b>' +
            esc(a.balance) + ' ' + esc(a.currency) + '</b></div>';
        }).join('');
        box.innerHTML = (rows ||
          '<p class="ogm-note">Coinbase reports no accounts.</p>') +
          '<p class="ogm-note">' + esc(pf.note || '') + '</p>';
      } else {
        var html = '<p class="ogm-honest">' + esc(pf.note || '') + '</p>';
        if (pf.state === 'disconnected') {
          html += '<p style="margin-top:10px"><a class="ogm-link" ' +
            'href="/auth/coinbase">Connect Coinbase</a> — the ' +
            'connect itself runs through Coinbase\u2019s own ' +
            'sign-in.</p>';
        }
        box.innerHTML = html;
      }
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

    function renderPaper() {
      var box = q('paper');
      if (!box) return;
      var book = paperLoad();
      var priceOf = {};
      hub.prices.forEach(function (p) { priceOf[p.symbol] = p.usd; });
      var held = Object.keys(book.holdings).filter(function (s) {
        return book.holdings[s] > 0;
      });
      var heldHtml = held.map(function (s) {
        var qty = book.holdings[s];
        var val = priceOf[s] == null ? null : qty * priceOf[s];
        return '<div class="ogm-metric"><span>' + esc(s) +
          ' — ' + qty.toFixed(8).replace(/0+$/, '').replace(/\.$/, '') +
          '</span><b>' + (val == null ? 'price unavailable'
            : money(val)) + '</b></div>';
      }).join('');
      var opts = hub.prices.map(function (p) {
        return '<option value="' + esc(p.symbol) + '">' +
          esc(p.symbol) + ' — ' + money(p.usd) + '</option>';
      }).join('');
      box.innerHTML =
        '<div class="ogm-metric"><span>PAPER cash</span><b>' +
        money(book.cash) + ' PAPER</b></div>' + heldHtml +
        '<div class="ogm-row" style="margin-top:10px">' +
        '<div><label class="ogm-label">Coin (live price)</label>' +
        '<select class="ogm-select" data-m="p-sym">' + opts +
        '</select></div>' +
        '<div><label class="ogm-label">PAPER USD amount</label>' +
        '<input class="ogm-input" data-m="p-amt" type="number" ' +
        'min="1" step="any" placeholder="100"></div>' +
        '<button class="ogm-btn ogm-noflex" data-m="p-buy">Buy ' +
        '(paper)</button>' +
        '<button class="ogm-btn-ghost ogm-noflex" data-m="p-sell">' +
        'Sell (paper)</button>' +
        '<button class="ogm-btn-ghost ogm-noflex" data-m="p-reset">' +
        'Reset paper book</button></div>' +
        '<p class="ogm-err" data-m="p-err"></p>';
      function tradePaper(isBuy) {
        var sym = box.querySelector('[data-m="p-sym"]').value;
        var amt = Number(box.querySelector('[data-m="p-amt"]').value);
        var err = box.querySelector('[data-m="p-err"]');
        var price = priceOf[sym];
        if (!sym || price == null) {
          err.textContent = 'No live price for that coin right now.';
          return;
        }
        if (!isFinite(amt) || amt <= 0) {
          err.textContent = 'Enter a PAPER amount first.';
          return;
        }
        var b = paperLoad();
        if (isBuy) {
          if (amt > b.cash) {
            err.textContent = 'Not enough PAPER cash.';
            return;
          }
          b.cash -= amt;
          b.holdings[sym] = (b.holdings[sym] || 0) + amt / price;
        } else {
          var qty = amt / price;
          if ((b.holdings[sym] || 0) < qty) {
            err.textContent = 'Not enough PAPER ' + sym + '.';
            return;
          }
          b.holdings[sym] -= qty;
          b.cash += amt;
        }
        paperSave(b);
        renderPaper();
      }
      box.querySelector('[data-m="p-buy"]').addEventListener(
        'click', function () { tradePaper(true); });
      box.querySelector('[data-m="p-sell"]').addEventListener(
        'click', function () { tradePaper(false); });
      box.querySelector('[data-m="p-reset"]').addEventListener(
        'click', function () {
          try { localStorage.removeItem(paperKey); } catch (e) {}
          renderPaper();
        });
    }

    function renderWatch(data) {
      var box = q('watch');
      var items = (data && data.items) || [];
      if (!items.length) {
        box.innerHTML = '<p class="ogm-note">No addresses saved ' +
          'yet.</p>';
        return;
      }
      box.innerHTML = items.map(function (it) {
        var bal = it.balance_available && it.balance
          ? esc(it.balance.balance) + ' ' + esc(it.balance.unit) +
            (it.balance.usd != null
              ? ' (' + money(it.balance.usd) + ')' : '') +
            (it.balance.unconfirmed != null
              ? ' · unconfirmed ' + esc(it.balance.unconfirmed) +
                ' ' + esc(it.balance.unit) : '') +
            ' · via ' + esc(it.balance.source)
          : 'balance unavailable right now';
        return '<div class="ogm-watch-item"><div>' +
          '<b>' + esc(it.label || it.chain.toUpperCase()) + '</b> ' +
          '<span class="ogm-note">' + esc(it.chain.toUpperCase()) +
          '</span><div class="ogm-addr">' + esc(it.address) +
          '</div><div class="ogm-note">' + bal + '</div></div>' +
          '<button class="ogm-btn-ghost" data-remove="' +
          esc(it.id) + '">Remove</button></div>';
      }).join('');
      box.querySelectorAll('[data-remove]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          postJSON('/cryptohub/watchlist', {
            action: 'remove', id: btn.getAttribute('data-remove')
          }).then(renderWatch).catch(function (e) {
            q('w-err').textContent = e.message;
          });
        });
      });
    }

    function loadWatch() {
      getJSON('/cryptohub/watchlist').then(renderWatch)
        .catch(function () {
          q('watch').innerHTML = '<p class="ogm-honest">Watch-only ' +
            'addresses are unavailable right now.</p>';
        });
    }

    q('w-add').addEventListener('click', function () {
      var err = q('w-err');
      err.textContent = '';
      postJSON('/cryptohub/watchlist', {
        action: 'add',
        chain: q('w-chain').value,
        address: q('w-addr').value.trim(),
        label: q('w-label').value.trim()
      }).then(function (d) {
        q('w-addr').value = '';
        q('w-label').value = '';
        renderWatch(d);
      }).catch(function (e) { err.textContent = e.message; });
    });

    function load() {
      getJSON('/cryptohub/data').then(function (data) {
        renderPrices(data);
        renderPortfolio(data);
      }).catch(function () {
        q('prices').innerHTML = '<p class="ogm-honest">Live ' +
          'prices are unavailable right now.</p>';
        q('portfolio').innerHTML = '<p class="ogm-honest">' +
          'Portfolio state is unavailable right now.</p>';
        renderTrade({});
      });
    }
    load();
    loadWatch();
    startTimer('crypto', bodyEl, load);
  }

  /* ================= FINANCIAL SMART AGENT ================= */

  function renderFin(bodyEl) {
    stopTimer('fin');
    bodyEl.innerHTML =
      '<div class="ogm-wrap">' +
      '<h2 class="ogm-title">Financial Smart Agent</h2>' +
      '<p class="ogm-sub">Your bank picture first — real ' +
      'balances, activity, and spending from your own ' +
      'connected bank, read-only. Then real quotes and a ' +
      'market mood computed from real index numbers. ' +
      'OG doesn\u2019t predict prices.</p>' +

      '<section class="ogm-card ogf-bank"><h3>Your Bank</h3>' +
      '<div data-m="bank"><p class="ogm-note">Loading your ' +
      'bank picture\u2026</p></div></section>' +

      '<section class="ogm-card"><h3>Stock Analysis</h3>' +
      '<div class="ogm-row">' +
      '<div><label class="ogm-label">Stock symbol</label>' +
      '<input class="ogm-input" data-m="s-sym" maxlength="12" ' +
      'placeholder="e.g. AAPL"></div>' +
      '<button class="ogm-btn ogm-noflex" data-m="s-go">Get ' +
      'quote</button></div>' +
      '<div data-m="s-out" style="margin-top:10px"></div>' +
      '<p class="ogm-honest" style="margin-top:10px">OG doesn\u2019t ' +
      'predict prices — no forecast, no target, and no buy/sell ' +
      'call is attached to a quote. What\u2019s above is the ' +
      'market\u2019s own real numbers, nothing added.</p></section>' +

      '<section class="ogm-card"><h3>Portfolio Status</h3>' +
      '<div data-m="pf-out"><p class="ogm-note">Checking…</p>' +
      '</div></section>' +

      '<section class="ogm-card"><h3>Market Sentiment</h3>' +
      '<div data-m="sent-out"><p class="ogm-note">Reading the ' +
      'live indexes…</p></div></section>' +

      '<section class="ogm-card"><h3>Learning &amp; Adaptation</h3>' +
      '<p class="ogm-honest">Not available — OG has no learning ' +
      'feed wired to this card, so there are no adaptation ' +
      'stats to show. Rather than invent numbers, this card ' +
      'stays honest until a real feed exists.</p></section>' +
      '</div>';

    function q(sel) { return bodyEl.querySelector('[data-m="' + sel + '"]'); }

    function metric(label, value, clsName) {
      return '<div class="ogm-metric"><span>' + esc(label) +
        '</span><b' + (clsName ? ' class="' + clsName + '"' : '') +
        '>' + esc(value) + '</b></div>';
    }

    /* --- Ticket L: your bank picture (GET /finagent/bank) ---
       Every state paints honestly: signed-out gets the
       sign-in door, no-bank gets the Plaid connect screen,
       connected gets ONLY the numbers the server read
       from the caller's own bank. Nothing is computed or
       filled in client-side. */
    function signedMoneyFmt(v) {
      var n = Number(v);
      if (!isFinite(n)) return '—';
      var s = money(Math.abs(n));
      return (n < 0 ? '-' : '+') + s;
    }

    function paintBankConnect(box, d, withButton) {
      var html = '<p class="ogm-honest">' +
        esc(d.reason || '') + '</p>';
      if (withButton) {
        html += '<p style="margin-top:10px"><button ' +
          'class="ogm-btn" data-m="bank-connect" ' +
          'type="button">Connect bank (Plaid)</button></p>' +
          '<p class="ogm-err" data-m="bank-err"></p>';
      }
      box.innerHTML = html;
      var btn = box.querySelector('[data-m="bank-connect"]');
      if (btn) btn.addEventListener('click', openPlaidLink);
    }

    function openPlaidLink() {
      var err = q('bank-err');
      if (typeof Plaid === 'undefined') {
        if (err) err.textContent = 'Plaid\u2019s connect ' +
          'window couldn\u2019t load here — you can also ' +
          'connect from the menu, under Connections.';
        return;
      }
      fetch('/plaid/link-token', { method: 'POST',
        credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.link_token) {
            if (err) err.textContent = 'Plaid didn\u2019t ' +
              'answer just now — try again in a moment.';
            return;
          }
          var handler = Plaid.create({
            token: d.link_token,
            onSuccess: function (publicToken) {
              fetch('/plaid/exchange', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type':
                  'application/json' },
                body: JSON.stringify({
                  public_token: publicToken })
              }).then(function () { loadBank(); })
                .catch(function () { loadBank(); });
            },
            onExit: function () { }
          });
          handler.open();
        })
        .catch(function () {
          if (err) err.textContent = 'Plaid didn\u2019t ' +
            'answer just now — try again in a moment.';
        });
    }

    function paintBank(d) {
      var box = q('bank');
      if (!box) return;
      if (!d || !d.state) {
        box.innerHTML = '<p class="ogm-honest">Your bank ' +
          'picture is unavailable right now.</p>';
        return;
      }
      if (d.state === 'unavailable' || d.state === 'error') {
        box.innerHTML = '<p class="ogm-honest">' +
          esc(d.reason || '') + '</p>';
        return;
      }
      if (d.state === 'connect' || d.state === 'reconnect') {
        paintBankConnect(box, d, true);
        return;
      }
      /* connected */
      var html = '';
      var who = d.institution || 'Your bank';
      html += '<p class="ogm-note">' + esc(who) +
        ' · read-only — OG can look, never move money.</p>';
      html += '<h4 class="ogf-h">Accounts</h4>';
      if ((d.accounts || []).length) {
        html += d.accounts.map(function (a) {
          var label = a.name + (a.mask
            ? ' (\u2026' + a.mask + ')' : '');
          var val = a.current == null ? '—'
            : money(a.current);
          var sub = a.available == null ? ''
            : '<div class="ogf-sub">Available ' +
              money(a.available) + '</div>';
          return '<div class="ogm-metric"><span>' +
            esc(label) + sub + '</span><b>' + esc(val) +
            '</b></div>';
        }).join('');
      } else {
        html += '<p class="ogm-note">Plaid reports no ' +
          'accounts on this connection.</p>';
      }
      html += '<h4 class="ogf-h">Recent Activity</h4>';
      if (d.transactions == null) {
        html += '<p class="ogm-honest">' +
          esc(d.transactions_note || '') + '</p>';
      } else if (!d.transactions.length) {
        html += '<p class="ogm-note">No transactions in ' +
          'the last 30 days.</p>';
      } else {
        html += d.transactions.map(function (t) {
          return '<div class="ogf-txn"><span>' +
            '<span class="ogf-txn-date">' + esc(t.date) +
            '</span> ' + esc(t.name) +
            (t.pending ? ' <span class="ogf-pend">' +
              'pending</span>' : '') +
            '</span><b>' + esc(signedMoneyFmt(t.amount)) +
            '</b></div>';
        }).join('');
      }
      if (d.spending) {
        var sp = d.spending;
        html += '<h4 class="ogf-h">Spending by Category' +
          ' · last ' + sp.window_days + ' days</h4>';
        html += metric('Money out', money(sp.money_out)) +
          metric('Money in', money(sp.money_in));
        html += (sp.by_category || []).map(function (c) {
          return metric(c.category, money(c.total));
        }).join('');
        html += '<p class="ogm-note">Counted from ' +
          sp.counted + ' settled transactions. ' +
          esc(sp.basis || '') + '</p>';
      }
      html += '<div class="ogm-asof">As of ' +
        esc(d.as_of || '') + '</div>';
      box.innerHTML = html;
    }

    function loadBank() {
      var box = q('bank');
      if (!box) return;
      fetch('/finagent/bank', { credentials: 'same-origin' })
        .then(function (r) {
          if (r.status === 401) {
            box.innerHTML = '<p class="ogm-honest">Sign ' +
              'in to see your bank picture — your balances, ' +
              'recent activity, and spending live behind ' +
              'your account, and OG never shows them to ' +
              'anyone else.</p>';
            return null;
          }
          return r.ok ? r.json() : null;
        })
        .then(function (d) { if (d) paintBank(d); })
        .catch(function () {
          box.innerHTML = '<p class="ogm-honest">Your ' +
            'bank picture is unavailable right now.</p>';
        });
    }

    function analyze() {
      var sym = q('s-sym').value.trim().toUpperCase();
      var out = q('s-out');
      if (!sym) { out.innerHTML = ''; return; }
      out.innerHTML = '<p class="ogm-note">Loading…</p>';
      getJSON('/finagent/quote?symbol=' + encodeURIComponent(sym))
        .then(function (d) {
          if (!d.available) {
            out.innerHTML = '<p class="ogm-honest">' +
              esc(d.reason || 'No live quote came back.') + '</p>';
            return;
          }
          out.innerHTML =
            metric('Price', money(d.price)) +
            metric('Change', (d.change == null ? '—'
              : (d.change >= 0 ? '+' : '') + Number(d.change).toFixed(2)) +
              ' (' + pct(d.change_pct) + ')', cls(d.change_pct)) +
            metric('Day open', d.open == null ? '—' : money(d.open)) +
            metric('Day high', d.day_high == null ? '—' : money(d.day_high)) +
            metric('Day low', d.day_low == null ? '—' : money(d.day_low)) +
            metric('Volume', d.volume == null ? '—'
              : Number(d.volume).toLocaleString('en-US')) +
            (d.market_cap == null ? ''
              : metric('Market cap', money(d.market_cap, 0))) +
            metric('Source', d.source || '—') +
            '<div class="ogm-asof">As of ' + esc(d.as_of || '') +
            '</div>';
        }).catch(function () {
          out.innerHTML = '<p class="ogm-honest">The quote feed ' +
            'is unavailable right now.</p>';
        });
    }
    q('s-go').addEventListener('click', analyze);
    q('s-sym').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') analyze();
    });

    getJSON('/finagent/portfolio').then(function (d) {
      q('pf-out').innerHTML = d.available
        ? '<p class="ogm-note">Portfolio connected.</p>'
        : '<p class="ogm-honest">' + esc(d.reason || '') + '</p>';
    }).catch(function () {
      q('pf-out').innerHTML = '<p class="ogm-honest">Portfolio ' +
        'state is unavailable right now.</p>';
    });

    function loadSentiment() {
      getJSON('/finagent/sentiment').then(function (d) {
        var out = q('sent-out');
        if (!out) return;
        if (!d.available) {
          out.innerHTML = '<p class="ogm-honest">' +
            esc(d.reason || 'Market mood unavailable.') + '</p>';
          return;
        }
        var rows = (d.indexes || []).map(function (ix) {
          return metric(ix.name + ' (' + ix.symbol + ')',
            money(ix.price) + ' · ' + pct(ix.change_pct),
            cls(ix.change_pct));
        }).join('');
        out.innerHTML = metric('Market mood', d.label) + rows +
          metric('Average index change', pct(d.average_change_pct),
            cls(d.average_change_pct)) +
          '<p class="ogm-note">' + esc(d.basis || '') + '</p>' +
          '<div class="ogm-asof">As of ' + esc(d.as_of || '') +
          '</div>';
      }).catch(function () {
        var out = q('sent-out');
        if (out) out.innerHTML = '<p class="ogm-honest">Market ' +
          'mood is unavailable right now.</p>';
      });
    }
    loadBank();
    loadSentiment();
    startTimer('fin', bodyEl, function () {
      loadBank();
      loadSentiment();
    });
  }

  /* Registration with the scaffold — og_dashboards.js loads
     first; if it is somehow not there yet, retry briefly. */
  function register() {
    if (window.OGDS && typeof window.OGDS.registerSurface ===
        'function') {
      window.OGDS.registerSurface('crypto', renderCrypto);
      window.OGDS.registerSurface('fin', renderFin);
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

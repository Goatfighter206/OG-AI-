/* Ticket L4 — the standalone Financial Smart Agent page
   (/fin). This script renders the page's THREE sections
   and nothing else (Ticket L3 scope law: bank accounts,
   the budget, money guard — stock analysis, portfolio
   status, market sentiment and filler do not exist
   here):
     Your Bank   <- GET /finagent/bank   (Ticket L)
     The Budget  <- GET /finagent/budget (og_finpage.py)
     Money Guard <- GET /finagent/guard  (og_finpage.py)
   The bank painter is the Ticket L painter, moved here
   from og_markets.js when the fin surface retired —
   same states, same copy, same Plaid connect door.
   House rules, unchanged: real data only; when the
   server says a state, the section paints that state
   honestly; nothing is computed or filled in client-
   side beyond formatting what the server read. */
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
  function signedMoneyFmt(v) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    var s = money(Math.abs(n));
    return (n < 0 ? '-' : '+') + s;
  }
  function metric(label, value) {
    return '<div class="ogm-metric"><span>' + esc(label) +
      '</span><b>' + esc(value) + '</b></div>';
  }
  function q(sel) {
    return document.querySelector('[data-m="' + sel + '"]');
  }
  /* One read, three outcomes: the payload, the 401
     sign-in door (the session died after the server
     let the page through — the door is a link home,
     where the sign-in + age flow lives), or null. */
  function loadJSON(url, box, signinLine) {
    return fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) {
          box.innerHTML = '<p class="ogm-honest">' +
            esc(signinLine) + '</p><p style="margin-top:10px">' +
            '<a class="ogm-link" href="/">Sign in on OG</a></p>';
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .catch(function () { return null; });
  }
  function stateLine(box, d, fallback) {
    if (!d || !d.state) {
      box.innerHTML = '<p class="ogm-honest">' + esc(fallback) + '</p>';
      return true;
    }
    if (d.state !== 'connected') {
      box.innerHTML = '<p class="ogm-honest">' +
        esc(d.reason || fallback) + '</p>';
      return true;
    }
    return false;
  }

  /* ================= YOUR BANK (Ticket L) ================= */

  function paintBankConnect(box, d, withButton) {
    var html = '<p class="ogm-honest">' + esc(d.reason || '') + '</p>';
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
    var err = document.querySelector('[data-m="bank-err"]');
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
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ public_token: publicToken })
            }).then(function () { loadAll(); })
              .catch(function () { loadAll(); });
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
        var val = a.current == null ? '—' : money(a.current);
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

  /* ================= THE BUDGET (Ticket L3) ================= */
  /* The workbook's shape: Category | Your estimate |
     Actual (bank, monthly) | Difference, grouped
     Income / Fixed / Variable, then THE BOTTOM LINE.
     Estimates ride the payload as null (no estimate
     store exists) — the cells read "not set", never a
     made-up figure. */

  function paintBudget(d) {
    var box = q('budget');
    if (!box) return;
    if (stateLine(box, d,
        'Your budget is unavailable right now.')) {
      if (d && (d.state === 'connect' ||
                d.state === 'reconnect')) {
        paintBankConnect(box, d, false);
      }
      return;
    }
    var hasEst = d.estimates != null;
    var html = '<table class="ogfp-table"><thead><tr>' +
      '<th>Category</th><th>Your estimate</th>' +
      '<th>Actual (bank ' + esc(d.window_days || 90) +
      '-day avg)</th><th>Difference</th></tr></thead><tbody>';
    (d.groups || []).forEach(function (g) {
      html += '<tr class="ogfp-group"><td colspan="4">' +
        esc(g.name) + '</td></tr>';
      (g.rows || []).forEach(function (r) {
        var est = hasEst && d.estimates[r.label] != null
          ? d.estimates[r.label] : null;
        html += '<tr><td>' + esc(r.label) + '</td><td>' +
          (est == null ? 'not set' : money(est)) +
          '</td><td>' + money(r.actual) + '</td><td>' +
          (est == null ? '—' : signedMoneyFmt(
            Math.round((r.actual - est) * 100) / 100)) +
          '</td></tr>';
      });
      html += '<tr class="ogfp-total"><td>Total ' +
        esc(g.name.toLowerCase()) + '</td><td></td><td>' +
        money(g.total) + '</td><td></td></tr>';
    });
    html += '</tbody></table>';
    html += '<div class="ogfp-bottom">' +
      '<h4 class="ogf-h">The Bottom Line (monthly)</h4>' +
      metric('Total income', money(d.total_income)) +
      metric('Total expenses', money(d.total_expenses)) +
      metric('Leftover each month', money(d.leftover)) +
      metric('Savings rate', d.savings_rate == null
        ? '—' : (Math.round(d.savings_rate * 1000) / 10) + '%') +
      '</div>';
    html += '<p class="ogm-note">Counted from ' +
      esc(d.counted) + ' settled transactions. ' +
      esc(d.basis || '') + '</p>';
    html += '<div class="ogm-asof">As of ' +
      esc(d.as_of || '') + '</div>';
    box.innerHTML = html;
  }

  /* ================= MONEY GUARD (Ticket L3) ================= */
  /* The Cash Guard shape: SAFE TO SPEND headline, IN
     THE BANK NOW, STILL RESERVED (named + dated),
     CLEARED SO FAR, PAYCHECK LANDED, DAY BY DAY. What
     OG has no record for reads as itself, never as a
     number. */

  function paintGuard(d) {
    var box = q('guard');
    if (!box) return;
    if (stateLine(box, d,
        'Your money guard is unavailable right now.')) {
      if (d && (d.state === 'connect' ||
                d.state === 'reconnect')) {
        paintBankConnect(box, d, false);
      }
      return;
    }
    var html = '';
    if (d.safe_to_spend != null) {
      html += '<div class="ogfp-big-label">Safe to spend</div>' +
        '<div class="ogfp-big">' + money(d.safe_to_spend) +
        '</div>';
    } else {
      html += '<div class="ogfp-big-label">In the bank now</div>' +
        '<div class="ogfp-big">' + money(d.in_bank_now) +
        '</div>' +
        '<p class="ogm-honest">' +
        esc((d.reserved && d.reserved.note) || '') + '</p>';
    }
    html += '<h4 class="ogf-h">Accounts</h4>';
    if ((d.accounts || []).length) {
      html += d.accounts.map(function (a) {
        var label = a.name + (a.mask
          ? ' (\u2026' + a.mask + ')' : '');
        return '<div class="ogm-metric"><span>' +
          esc(label) + '</span><b>' +
          esc(a.current == null ? '—' : money(a.current)) +
          '</b></div>';
      }).join('');
    } else {
      html += '<p class="ogm-note">Plaid reports no ' +
        'accounts on this connection.</p>';
    }
    if (d.safe_to_spend != null) {
      html += metric('In the bank now', money(d.in_bank_now));
    }
    html += '<h4 class="ogf-h">Still Reserved</h4>';
    if (d.reserved && d.reserved.available &&
        (d.reserved.bills || []).length) {
      html += d.reserved.bills.map(function (b) {
        return metric(b.name + (b.date
          ? ' · ' + b.date : ''), money(b.amount));
      }).join('');
    } else if (d.safe_to_spend != null) {
      html += '<p class="ogm-note">Nothing reserved.</p>';
    } else {
      html += '<p class="ogm-note">No bills reserved ' +
        'on file.</p>';
    }
    if (d.paycheck) {
      html += '<h4 class="ogf-h">Paycheck Landed</h4>' +
        '<div class="ogf-txn"><span>' +
        '<span class="ogf-txn-date">' +
        esc(d.paycheck.date) + '</span> ' +
        esc(d.paycheck.name) + '</span><b>' +
        esc(signedMoneyFmt(d.paycheck.amount)) +
        '</b></div>';
    }
    if (d.cleared) {
      html += '<h4 class="ogf-h">Cleared So Far</h4>' +
        metric('Cleared ' + (d.cleared.basis || ''),
          money(d.cleared.total)) +
        '<p class="ogm-note">' + esc(d.cleared.count) +
        ' settled charge' + (d.cleared.count === 1
          ? '' : 's') + '.</p>';
    }
    if (d.transactions_note) {
      html += '<p class="ogm-honest">' +
        esc(d.transactions_note) + '</p>';
    } else if ((d.days || []).length) {
      html += '<h4 class="ogf-h">Day by Day</h4>' +
        d.days.map(function (day) {
          return '<div class="ogf-txn"><span>' +
            '<span class="ogf-txn-date">' + esc(day.date) +
            '</span> ' + (day.net === 0
              ? 'no activity' : signedMoneyFmt(day.net)) +
            '</span><b>' + money(day.balance_after) +
            '</b></div>';
        }).join('') +
        '<p class="ogm-note">' + esc(d.basis || '') + '</p>';
    }
    html += '<div class="ogm-asof">As of ' +
      esc(d.as_of || '') + '</div>';
    box.innerHTML = html;
  }

  /* ================= loads ================= */

  function loadAll() {
    var bankBox = q('bank');
    if (bankBox) {
      loadJSON('/finagent/bank', bankBox,
        'Sign in to see your bank picture — your ' +
        'balances, recent activity, and spending live ' +
        'behind your account, and OG never shows them ' +
        'to anyone else.')
        .then(function (d) { if (d) paintBank(d); });
    }
    var budgetBox = q('budget');
    if (budgetBox) {
      loadJSON('/finagent/budget', budgetBox,
        'Sign in to see your budget — it is built from ' +
        'your own bank, behind your account.')
        .then(function (d) { if (d) paintBudget(d); });
    }
    var guardBox = q('guard');
    if (guardBox) {
      loadJSON('/finagent/guard', guardBox,
        'Sign in to see your money guard — it reads ' +
        'your own bank, behind your account.')
        .then(function (d) { if (d) paintGuard(d); });
    }
  }

  loadAll();
})();

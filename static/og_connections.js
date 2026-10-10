/* OG connections: the nine built-in connect/status/disconnect wirings,
   moved verbatim from index_epic.html, followed by the Connections
   folder behavior (Brent, 2026-10-09). */
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menuGoogleBtn'), info = $('menuGoogleInfo'),
emailEl = $('menuGoogleEmail'), disc = $('menuGoogleDisconnect');
if (!btn) return;
function refresh() {
fetch('/auth/google/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
emailEl.textContent = s.email ? ('Google: ' + s.email) : 'Google connected';
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
if (disc) disc.addEventListener('click', function () {
fetch('/auth/google/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menuSpotifyBtn'), info = $('menuSpotifyInfo'),
nameEl = $('menuSpotifyName'), disc = $('menuSpotifyDisconnect');
if (!btn) return;
function refresh() {
fetch('/auth/spotify/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
var who = s.name || s.email;
nameEl.textContent = who ? ('Spotify: ' + who) : 'Spotify connected';
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
if (disc) disc.addEventListener('click', function () {
fetch('/auth/spotify/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menuCoinbaseBtn'), info = $('menuCoinbaseInfo'),
nameEl = $('menuCoinbaseName'), disc = $('menuCoinbaseDisconnect');
if (!btn) return;
function refresh() {
fetch('/auth/coinbase/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
var who = s.name || s.email;
nameEl.textContent = who ? ('Coinbase: ' + who) : 'Coinbase connected';
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
if (disc) disc.addEventListener('click', function () {
fetch('/auth/coinbase/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menuGithubBtn'), info = $('menuGithubInfo'),
nameEl = $('menuGithubName'), disc = $('menuGithubDisconnect');
if (!btn) return;
function refresh() {
fetch('/auth/github/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
var who = s.login || s.name || s.email;
nameEl.textContent = who ? ('GitHub: ' + who) : 'GitHub connected';
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
if (disc) disc.addEventListener('click', function () {
fetch('/auth/github/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
[['Youtube', 'youtube', function (s) { return s.name || s.email; }, 'YouTube'],
['Discord', 'discord', function (s) { return s.username || s.name; }, 'Discord'],
['Twitch', 'twitch', function (s) { return s.login || s.name || s.email; }, 'Twitch'],
['Reddit', 'reddit', function (s) { return s.username || s.name; }, 'Reddit']
].forEach(function (cfg) {
var key = cfg[0], path = cfg[1], pick = cfg[2], label = cfg[3];
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menu' + key + 'Btn'), info = $('menu' + key + 'Info'),
nameEl = $('menu' + key + 'Name'), disc = $('menu' + key + 'Disconnect');
if (!btn) return;
function refresh() {
fetch('/auth/' + path + '/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
var who = pick(s);
nameEl.textContent = who ? (label + ': ' + who) : (label + ' connected');
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
if (disc) disc.addEventListener('click', function () {
fetch('/auth/' + path + '/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
});
(function () {
function $(id) { return document.getElementById(id); }
var btn = $('menuPlaidBtn'), info = $('menuPlaidInfo'),
nameEl = $('menuPlaidName'), disc = $('menuPlaidDisconnect');
if (!btn) return;
function refresh() {
fetch('/plaid/status').then(function (r) { return r.json(); }).then(function (s) {
if (!s.enabled) { btn.hidden = true; info.hidden = true; disc.hidden = true; return; }
if (s.connected) {
btn.hidden = true; info.hidden = false; disc.hidden = false;
nameEl.textContent = s.institution ? ('Bank: ' + s.institution) : 'Bank connected';
} else {
btn.hidden = false; info.hidden = true; disc.hidden = true;
}
}).catch(function () {  });
}
btn.addEventListener('click', function () {
if (typeof Plaid === 'undefined') return;
fetch('/plaid/link-token', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
if (!d.link_token) return;
var handler = Plaid.create({
token: d.link_token,
onSuccess: function (publicToken) {
fetch('/plaid/exchange', {
method: 'POST',
headers: { 'Content-Type': 'application/json' },
body: JSON.stringify({ public_token: publicToken })
}).then(refresh).catch(refresh);
},
onExit: function () {  }
});
handler.open();
}).catch(function () {  });
});
if (disc) disc.addEventListener('click', function () {
fetch('/plaid/disconnect', { method: 'POST' }).then(refresh).catch(refresh);
});
refresh();
})();
(function () {
function $(id) { return document.getElementById(id); }
var scrim = $('connectionsScrim'), panel = $('ogConnections'),
openBtn = $('menuConnectionsBtn'), closeBtn = $('connectionsCloseBtn'),
siteInput = $('browserConnectSite'), browserBtn = $('browserConnectBtn'),
list = $('browserLoginsList'), empty = $('browserLoginsEmpty'),
forgetAllBtn = $('forgetAllLoginsBtn'), msg = $('browserLoginsMessage');
function closeConnections() { if (scrim) scrim.hidden = true; if (panel) panel.hidden = true; }
function openConnections() {
var mc = $('menuClose'); if (mc) mc.click();
if (scrim) scrim.hidden = false;
if (panel) panel.hidden = false;
refreshLogins();
}
function paintLogins(logins) {
if (!list || !empty || !forgetAllBtn) return;
list.innerHTML = '';
empty.hidden = logins.length !== 0;
forgetAllBtn.hidden = logins.length === 0;
logins.forEach(function (item) {
var row = document.createElement('div');
row.className = 'og-connections-login-row';
var label = document.createElement('span');
var since = item.since ? String(item.since).slice(0, 10) : '';
label.textContent = since ? (item.site + ' — since ' + since) : item.site;
var btn = document.createElement('button');
btn.type = 'button';
btn.className = 'og-connections-forget-one';
btn.textContent = 'Forget';
btn.setAttribute('data-site', item.site);
btn.addEventListener('click', function () { forgetOne(item.site); });
row.appendChild(label);
row.appendChild(btn);
list.appendChild(row);
});
}
function refreshLogins() {
if (!list) return;
fetch('/watch/logins').then(function (r) { return r.json(); }).then(function (d) {
if (!d.enabled) {
list.innerHTML = '';
if (empty) { empty.hidden = false; empty.textContent = 'Browser logins are not switched on yet.'; }
if (forgetAllBtn) forgetAllBtn.hidden = true;
return;
}
if (empty) empty.textContent = 'No saved browser logins yet.';
paintLogins(d.logins || []);
}).catch(function () {  });
}
function forgetOne(site) {
if (!window.confirm('Forget your saved login for ' + site + '?')) return;
fetch('/watch/forget-site', {
method: 'POST', headers: { 'Content-Type': 'application/json' },
body: JSON.stringify({ site: site })
}).then(function (r) { return r.json(); }).then(function (d) {
if (msg) msg.textContent = d.body || d.title || '';
refreshLogins();
}).catch(function () {  });
}
function forgetAll() {
if (!window.confirm('Forget all your saved browser logins?')) return;
fetch('/watch/forget', { method: 'POST' }).then(function (r) { return r.json(); }).then(function (d) {
if (msg) msg.textContent = d.body || d.title || '';
refreshLogins();
}).catch(function () {  });
}
if (openBtn) openBtn.addEventListener('click', openConnections);
if (closeBtn) closeBtn.addEventListener('click', closeConnections);
if (scrim) scrim.addEventListener('click', closeConnections);
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeConnections(); });
if (forgetAllBtn) forgetAllBtn.addEventListener('click', forgetAll);
if (browserBtn) browserBtn.addEventListener('click', function () {
var site = siteInput ? siteInput.value.trim() : '';
if (!site) { if (siteInput) siteInput.focus(); return; }
closeConnections();
var input = $('messageInput');
if (!input) return;
input.value = 'Open ' + site;
if (typeof sendMessage === 'function') sendMessage(true);
});
window.ogOpenConnections = openConnections;
})();

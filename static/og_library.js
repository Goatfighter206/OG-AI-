/* OG Library (Brent, 2026-10-09): one shelf for everything OG
   made for this visitor — story videos, songs, saved images,
   locker files, Unity projects. Reads the read-only aggregate
   GET /library (og_library.py); every card's action rides the
   producers' own owner-only download routes. */
(function () {
function $(id) { return document.getElementById(id); }
var scrim = $('libraryScrim'), panel = $('ogLibrary'),
openBtn = $('menuLibraryBtn'), closeBtn = $('libraryCloseBtn'),
list = $('libraryList'), empty = $('libraryEmpty'),
storageLine = $('libraryStorageLine');
if (!panel || !list) return;
var ICONS = { video: '🎬', song: '🎵', image: '🖼️',
              file: '📄', project: '🎮' };
var STATE_LABEL = { queued: 'In line', drawing: 'Drawing',
  voicing: 'Voicing', stitching: 'Stitching',
  generating: 'Generating', failed: "Didn't finish",
  done: 'Done', ready: '' };
var TAB_LABEL = { video: 'Videos', song: 'Songs', image: 'Images',
                  file: 'Files', project: 'Projects' };
var items = [], filter = 'all';
function closeLibrary() {
if (scrim) scrim.hidden = true;
if (panel) panel.hidden = true;
}
function openLibrary() {
var mc = $('menuClose'); if (mc) mc.click();
if (scrim) scrim.hidden = false;
if (panel) panel.hidden = false;
refresh();
}
function fmtSize(n) {
n = Number(n || 0);
if (n < 1024) return n + ' B';
if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
return (n / 1073741824).toFixed(2) + ' GB';
}
function fmtDate(ts) {
var d = new Date(Number(ts || 0) * 1000);
if (isNaN(d.getTime())) return '';
return d.toLocaleDateString(undefined,
  { month: 'short', day: 'numeric', year: 'numeric' });
}
function subLine(it) {
var parts = [];
var when = fmtDate(it.created_at);
if (when) parts.push(when);
var st = STATE_LABEL[it.status];
if (st) parts.push(st);
else if (it.status && it.status !== 'ready'
         && it.status !== 'done') parts.push(it.status);
var meta = it.meta || {};
if (it.kind === 'file' || it.kind === 'image') {
if (meta.size_human) parts.push(meta.size_human);
} else if (meta.size) {
parts.push(fmtSize(meta.size));
}
if (it.kind === 'project' && meta.files) {
parts.push(meta.files + (meta.files === 1 ? ' file' : ' files'));
}
if (it.status === 'failed' && meta.detail) parts.push(meta.detail);
return parts.join(' · ');
}
function paint() {
list.innerHTML = '';
var shown = items.filter(function (it) {
return filter === 'all' || it.kind === filter;
});
shown.forEach(function (it) {
var card = document.createElement('div');
card.className = 'og-library-card';
var icon = document.createElement('span');
icon.className = 'og-library-icon';
icon.textContent = ICONS[it.kind] || '📄';
var main = document.createElement('div');
main.className = 'og-library-main';
var title = document.createElement('div');
title.className = 'og-library-title';
title.textContent = it.title || 'Untitled';
var sub = document.createElement('div');
sub.className = 'og-library-sub';
sub.textContent = subLine(it);
main.appendChild(title);
main.appendChild(sub);
card.appendChild(icon);
card.appendChild(main);
if (it.action_url) {
var a = document.createElement('a');
a.className = 'og-library-action';
a.href = it.action_url;
a.target = '_blank';
a.rel = 'noopener';
a.textContent = it.action || 'Open';
card.appendChild(a);
}
list.appendChild(card);
});
if (!empty) return;
if (items.length === 0) {
empty.hidden = false;
empty.textContent = 'Nothing here yet — ask OG to make you '
  + 'a story video.';
} else if (shown.length === 0) {
empty.hidden = false;
empty.textContent = 'Nothing in ' + (TAB_LABEL[filter] || 'here')
  + ' yet.';
} else {
empty.hidden = true;
}
}
function refresh() {
fetch('/library').then(function (r) { return r.json(); })
.then(function (d) {
items = d.items || [];
if (storageLine) {
if (d.storage) {
storageLine.hidden = false;
storageLine.textContent = 'Locker: ' + d.storage.used_human
  + ' of ' + d.storage.quota_human + ' used';
} else {
storageLine.hidden = true;
}
}
paint();
}).catch(function () {  });
}
var tabs = panel.querySelectorAll('.og-library-tab');
tabs.forEach(function (tab) {
tab.addEventListener('click', function () {
filter = tab.getAttribute('data-kind') || 'all';
tabs.forEach(function (t) {
t.classList.toggle('is-on', t === tab);
});
paint();
});
});
if (openBtn) openBtn.addEventListener('click', openLibrary);
if (closeBtn) closeBtn.addEventListener('click', closeLibrary);
if (scrim) scrim.addEventListener('click', closeLibrary);
document.addEventListener('keydown', function (e) {
if (e.key === 'Escape') closeLibrary();
});
window.ogOpenLibrary = openLibrary;
})();

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
var ICONS = { video: '🎬', song: '🎵', image: '🖼️', motion: '🎞️',
              file: '📄', project: '🎮', podcast: '🎙️', book: '📖',
              sfx: '🔊' };
var STATE_LABEL = { queued: 'In line', drawing: 'Drawing',
  voicing: 'Voicing', stitching: 'Stitching',
  generating: 'Writing', paused_caps: 'Paused — weekly cap',
  failed: "Didn't finish",
  done: 'Done', ready: '' };
var TAB_LABEL = { video: 'Videos', song: 'Songs', image: 'Images',
                  file: 'Files', project: 'Projects',
                  podcast: 'Podcasts', book: 'Books', motion: 'Motion',
                  sfx: 'Sound FX' };
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
if (it.kind === 'book' && meta.chapters_total) {
parts.push(meta.chapters_done + ' of ' + meta.chapters_total
  + ' chapters');
if (meta.words) parts.push(meta.words + ' words');
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
/* Generator 7a: the Podcasts tab is injected here — the tab
   buttons live in the page's static HTML, and the page is at
   its push ceiling, so the shelf grows its own tab. Items of
   kind "podcast" already ride GET /library and show under
   All; this tab is their filter. */
(function () {
var songsTab = panel.querySelector('.og-library-tab[data-kind="song"]');
if (songsTab && !panel.querySelector('.og-library-tab[data-kind="podcast"]')) {
var pt = document.createElement('button');
pt.className = 'og-library-tab';
pt.id = 'libraryTabPodcasts';
pt.type = 'button';
pt.setAttribute('data-kind', 'podcast');
pt.textContent = 'Podcasts';
songsTab.parentNode.insertBefore(pt, songsTab.nextSibling);
}
})();
/* Generator 7c: the Books tab is injected the same way —
   items of kind "book" already ride GET /library and show
   under All; this tab is their filter. Opening a book
   (action_url /books/<id>/read) is intercepted by
   static/og_books.js into the reader overlay. */
(function () {
var podTab = panel.querySelector('.og-library-tab[data-kind="podcast"]');
var anchor = podTab || panel.querySelector('.og-library-tab[data-kind="song"]');
if (anchor && !panel.querySelector('.og-library-tab[data-kind="book"]')) {
var bt = document.createElement('button');
bt.className = 'og-library-tab';
bt.id = 'libraryTabBooks';
bt.type = 'button';
bt.setAttribute('data-kind', 'book');
bt.textContent = 'Books';
anchor.parentNode.insertBefore(bt, anchor.nextSibling);
}
})();
/* Generator 7d: the Motion tab is injected the same way —
   items of kind "motion" already ride GET /library and show
   under All; this tab is their filter. */
(function () {
var bookTab = panel.querySelector('.og-library-tab[data-kind="book"]');
var anchor2 = bookTab || panel.querySelector('.og-library-tab[data-kind="podcast"]');
if (anchor2 && !panel.querySelector('.og-library-tab[data-kind="motion"]')) {
var mt = document.createElement('button');
mt.className = 'og-library-tab';
mt.id = 'libraryTabMotion';
mt.type = 'button';
mt.setAttribute('data-kind', 'motion');
mt.textContent = 'Motion';
anchor2.parentNode.insertBefore(mt, anchor2.nextSibling);
}
})();
/* Generator 7f: the Sound FX tab is injected the same way —
   items of kind "sfx" already ride GET /library and show
   under All; this tab is their filter. */
(function () {
var motionTab = panel.querySelector('.og-library-tab[data-kind="motion"]');
var anchor3 = motionTab || panel.querySelector('.og-library-tab[data-kind="book"]');
if (anchor3 && !panel.querySelector('.og-library-tab[data-kind="sfx"]')) {
var st = document.createElement('button');
st.className = 'og-library-tab';
st.id = 'libraryTabSfx';
st.type = 'button';
st.setAttribute('data-kind', 'sfx');
st.textContent = 'Sound FX';
anchor3.parentNode.insertBefore(st, anchor3.nextSibling);
}
})();
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

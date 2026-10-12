"""og_browser_cdp — the browser's raw CDP driver, split out
of og_browser (Round 107, the R46/R61 extraction pattern:
og_browser rides the GitHub push ceiling, so a
self-contained section moves behind an import and
og_browser keeps every name it ever had).

WHAT LIVES HERE. The synchronous Chrome DevTools client
(_Cdp), the page attach, the snapshot script + reader,
and the ready wait — leaf machinery: this module calls
nothing back in og_browser. The two seams the suites stub
stay home in og_browser on purpose: _steel_api (the Steel
REST seam) and _cdp_connect (the connection factory —
it needs _steel_key, which stays with the seam).

ROUND 107 — the CDP timeouts are named constants with
this file as their single home, all single-digit
seconds. The diagnosis (B7/B8 census + the start-path
audit): a wedged page or socket used to hold a chat
worker for 15 s (connect) + 25 s (per call) + 10 s
(ready wait) on top of the Steel create; every wait on
the path is now bounded at CDP_*_TIMEOUT_S.
"""

import json
import time
from typing import Dict, Optional

# Round 107: every CDP wait on the browser path, in
# seconds — single digits, one home (here).
CDP_CONNECT_TIMEOUT_S = 8
CDP_CALL_TIMEOUT_S = 8
CDP_READY_TIMEOUT_S = 8


class _DriveError(Exception):
    pass


class _Cdp:
    """Minimal synchronous CDP client: one command at a time,
    events drained while waiting for the matching response id."""

    def __init__(self, ws_url: str):
        from websockets.sync.client import connect as _ws_connect
        try:
            self._ws = _ws_connect(ws_url,
                                   open_timeout=CDP_CONNECT_TIMEOUT_S,
                                   close_timeout=5,
                                   max_size=16 * 1024 * 1024)
        except Exception as e:
            raise _DriveError(f"CDP connect failed ({e})")
        self._seq = 0
        self.session_id = None  # set by _attach_page (flatten mode)

    def call(self, method: str, params: Optional[Dict] = None,
             timeout: float = CDP_CALL_TIMEOUT_S) -> Dict:
        self._seq += 1
        mid = self._seq
        msg = {"id": mid, "method": method, "params": params or {}}
        if self.session_id:
            msg["sessionId"] = self.session_id
        self._ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._ws.recv(timeout=max(
                    0.1, deadline - time.time()))
            except Exception as e:
                raise _DriveError(f"CDP {method}: connection lost ({e})")
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") != mid:
                continue  # an event, not our response
            if "error" in msg:
                raise _DriveError(
                    f"CDP {method}: {msg['error'].get('message', 'error')}")
            result = msg.get("result")
            return result if isinstance(result, dict) else {}
        raise _DriveError(f"CDP {method}: timed out")

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def _attach_page(cdp: _Cdp) -> None:
    """Steel's websocketUrl is a BROWSER-level CDP endpoint: page
    commands (Page.navigate, Runtime.evaluate) have no target until
    one is attached — live drives failed fast on exactly that
    (2026-10-09: sessions created fine over REST and the panel went
    live, but no drive ever landed a page). Attach to the first
    page target in flatten mode; subsequent commands ride that
    target session. Best-effort by design: if attach is refused
    (endpoint already page-level), drive sessionless as before."""
    try:
        targets = cdp.call("Target.getTargets")
        page = None
        for t in targets.get("targetInfos") or []:
            if t.get("type") == "page":
                page = t
                break
        if not page:
            return
        res = cdp.call("Target.attachToTarget",
                       {"targetId": page.get("targetId"),
                        "flatten": True})
        sid = res.get("sessionId")
        if sid:
            cdp.session_id = sid
    except Exception:
        pass


_SNAPSHOT_JS = r"""
(function(){
  var out = {title: document.title || '', url: location.href,
             text: '', elements: []};
  try {
    out.text = (document.body ? document.body.innerText : '')
      .replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').slice(0, 3000);
  } catch (e) {}
  window.__ogEls = [];
  var nodes = document.querySelectorAll(
    'a[href],button,input,textarea,select,[role="button"],'
    + '[role="textbox"]');
  for (var i = 0; i < nodes.length && out.elements.length < 40; i++) {
    var el = nodes[i], tag = el.tagName.toLowerCase();
    var type = (el.getAttribute('type') || '').toLowerCase();
    var role = (el.getAttribute('role') || '').toLowerCase();
    if (tag === 'input' && (type === 'hidden' || type === 'password'
        || type === 'file')) continue;
    var r = el.getBoundingClientRect();
    var st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden'
        || (r.width === 0 && r.height === 0)) continue;
    var label = '';
    if (role === 'textbox') {
      // A composer (contenteditable): its innerText is the DRAFT
      // VALUE, which is never read — labels come from attributes
      // only, same as every other field.
      label = el.getAttribute('aria-label')
        || el.getAttribute('data-placeholder')
        || el.getAttribute('placeholder') || 'Text box';
    } else if (tag === 'input' || tag === 'textarea'
               || tag === 'select') {
      label = el.getAttribute('placeholder')
        || el.getAttribute('aria-label') || el.getAttribute('name')
        || type || tag;
    } else {
      label = (el.innerText || el.getAttribute('aria-label') || '')
        .trim().replace(/\s+/g, ' ').slice(0, 70);
    }
    if (!label && tag !== 'a') continue;
    var form = el.closest ? el.closest('form') : null;
    window.__ogEls.push(el);
    out.elements.push({
      i: window.__ogEls.length - 1, tag: tag, type: type,
      role: role,
      label: String(label).slice(0, 70),
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 200) : '',
      in_form: !!form});
  }
  return out;
})()
"""


def _eval(cdp: _Cdp, expression: str):
    result = cdp.call("Runtime.evaluate", {
        "expression": expression, "returnByValue": True,
        "awaitPromise": False})
    remote = result.get("result") or {}
    if result.get("exceptionDetails"):
        raise _DriveError("page script failed")
    return remote.get("value")


def _wait_ready(cdp: _Cdp, seconds: float = CDP_READY_TIMEOUT_S) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            state = _eval(cdp, "document.readyState")
        except _DriveError:
            return
        if state == "complete":
            return
        time.sleep(0.4)


def _snapshot(cdp: _Cdp) -> Dict:
    snap = _eval(cdp, _SNAPSHOT_JS)
    if not isinstance(snap, dict):
        raise _DriveError("could not read the page")
    return snap

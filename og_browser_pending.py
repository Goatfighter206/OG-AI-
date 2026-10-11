"""OG browser — the parked-approval map (Round 61 code motion).

Byte-faithful move out of og_browser.py (that file rides the
GitHub push ceiling — the Round 46 og_browser_claim extraction
was the same surgery). The map's store seams are injected by
og_browser at construction, so behavior is unchanged: reads
and writes go through the durable usage store (the session
records' mechanism), __getitem__ returns the store's LIVE
entry (diagnostics and suites poke `created` through it),
and clear() sweeps every parked key under the usage lock.
"""

from typing import Dict, Optional

PEND_KEY_PREFIX = "browser_pend:"


class PendingMap:
    """The parked approvals, as a mapping uid -> state dict,
    backed by the durable usage store. Production reads use
    og_browser._get_pending, which copies and applies TTL +
    the sweep; this map is the storage seam underneath it.

    with_store(fn, save=False) runs fn(store) under the usage
    lock over the live store dict and saves afterwards when
    asked — exactly og_browser's _deps access, injected."""

    def __init__(self, usage_get, usage_set, with_store):
        self._usage_get = usage_get
        self._usage_set = usage_set
        self._with_store = with_store

    def _key(self, uid: str) -> str:
        return f"{PEND_KEY_PREFIX}{uid}"

    def get(self, uid: str) -> Optional[Dict]:
        return self._usage_get(self._key(uid))

    def __getitem__(self, uid: str) -> Dict:
        entry = self._with_store(lambda store: store.get(self._key(uid)))
        if not isinstance(entry, dict):
            raise KeyError(uid)
        return entry

    def __setitem__(self, uid: str, state: Dict) -> None:
        self._usage_set(self._key(uid), state)

    def __delitem__(self, uid: str) -> None:
        self._usage_set(self._key(uid), None)

    def __contains__(self, uid: str) -> bool:
        return self._usage_get(self._key(uid)) is not None

    def pop(self, uid: str, default=None):
        state = self._usage_get(self._key(uid))
        if state is None:
            return default
        self._usage_set(self._key(uid), None)
        return state

    def clear(self) -> None:
        def sweep(store):
            for key in [k for k in store
                        if k.startswith(PEND_KEY_PREFIX)]:
                store.pop(key, None)
        self._with_store(sweep, save=True)

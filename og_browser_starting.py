"""OG browser — the starting marker (Round 61 code motion).

Byte-faithful move out of og_browser.py: that file rides the
GitHub push ceiling (the Round 46 og_browser_claim extraction
was the same surgery), so Round 61's marker logic lives here
and og_browser keeps thin wrappers under the names the suites
use (_set_starting / _get_starting / _clear_starting).

Brent's open order (2026-10-10): the browser comes UP first,
then the website opens inside it. A start stamps this marker
in the durable usage store BEFORE the Steel create runs, so
/browser/status can report phase "starting" (with the
destination host) while the session is still spinning up —
clients raise the window and its loading face on the marker
alone; no session record or frame needs to exist yet.

The freshness TTL is enforced here, on read: a start that
died mid-create (crash, kill) leaves a marker behind, and a
stale marker must read as NO start at all, so a visitor can
never be stuck on "starting" forever.
"""

import time
from typing import Dict, Optional

STARTING_TTL_S = 90.0

KEY_PREFIX = "browser_starting:"


def key_for(uid: str) -> str:
    return f"{KEY_PREFIX}{uid}"


def stamp_value(url: str) -> Dict:
    """The marker record for a start toward `url`, stamped now."""
    return {"url": url or "", "ts": time.time()}


def fresh(mark: Optional[Dict],
          now: Optional[float] = None) -> Optional[Dict]:
    """The marker itself when it is still fresh, else None.

    A malformed timestamp reads as stale — never as fresh."""
    if not mark:
        return None
    try:
        if (time.time() if now is None else now) \
                - float(mark.get("ts") or 0) > STARTING_TTL_S:
            return None
    except Exception:
        return None
    return mark

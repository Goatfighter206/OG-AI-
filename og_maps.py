"""
OG maps & places (Round 9).

The self-contained half of the maps feature: OG answers place and
route questions like a local — "where's the nearest coffee shop",
"find a hardware store near Tacoma", "how far is Sea-Tac from
downtown Seattle", "how do I get to Pike Place". Everything is
KEYLESS, off the public OpenStreetMap stack:

  - Geocoding: Nominatim (a named place -> coordinates + locality).
  - Nearby search: the Overpass API (real OSM elements by category,
    sorted by true distance from the reference point).
  - Routes: OSRM (driving distance + duration between two points).

Wiring is the same app-layer seam as Rounds 3/4/6: install_maps_tools
wraps the agent's (already lookup/file-wrapped) detect_intent +
web_search hooks. A place/route intent is parsed from the visitor's
raw message at detect time; when the search hook fires, maps gets
first crack — a hit returns grounded results in the {title, body,
href} shape both chat paths already format into model context, a
miss falls through to the previous search untouched (Round 4 data
pack, then Round 3's web lookup chain). Persona files untouched.

Grounding rules: an answer only ever contains what the sources
returned — name, address/area, true distance, opening hours ONLY
when OSM carries them, never a rating (OSM has none worth trusting)
— plus a tappable Google Maps link per place (or a directions link
for a route). When the visitor names no location, maps returns a
note that makes OG ask ONE short clarifying question instead of
guessing.

Round 11 adds GPS "near me": when the visitor opts in on the chat
page (their browser's own permission prompt — one getCurrentPosition
per opt-in), the page attaches {lat, lon} to that ONE /chat request
as a separate field. app.py hands it to note_request_coords() at
the start of the exchange; the seam below reads it back for that
exchange only. Coordinates are used to center a nearby search or
to origin a route, and for nothing else: they are NEVER stored,
NEVER logged (not even rounded), NEVER written to the visitor
store or the conversation history — the slot is overwritten by
every exchange and cleared the moment the seam consumes it. Even
the directions link for a from-here route carries no coordinates
(it opens from the visitor's own location in Google Maps), so
nothing OG can quote back contains them. A NAMED place in the
message always wins over shared coordinates; stale/absurd
coordinates (out of range, (0, 0)) are ignored and the normal
clarifying flow runs.

Budget: place/route answers share the Round 3 lookup budget — one
unit of og's per-tier "lookup" cap (app.py's _consume_lookup) is
consumed per maps answer, exactly like a Round 4 data answer. The
fetches are keyless and bill no upstream tokens, so nothing is added
to the lookup token stash; the exchange meters as ordinary chat,
capped by OG_LOOKUP_METER_CAP like every lookup. A miss or an
upstream failure consumes nothing — the fall-through lookup spends
its own unit, so an exchange never double-charges.

Failure behaviour: every fetch has a hard timeout (Overpass gets one
mirror fallback — it is the slow, flaky one; the default pair is the
OSM-FR instance + kumi, because overpass-api.de was 504ing on even
light queries when this round was built — OG_OVERPASS_URLS repoints
the pair without a code change) and every failure mode degrades to
the normal chat flow. Maps never raises into a chat path, never
500s, never hangs the exchange. Nearby queries stay deliberately
light (nodes only, 1.5 km first pass, 5 km retry) — heavier QL
makes the public instances time out, dense downtowns most of all.

Round 78 adds the ONE-TIME location permission ask (Brent: "Only
ask one time then it stay on until they turn it off"). When a
directions ask reaches the route seam with a destination but no
named start and no shared coordinates, and the account's Round 73
location switch is OFF and the account has NEVER been asked, the
start-point clarifying note is replaced — once — by a permission
ask: OG asks to use their location to navigate them where they
want to go, saying a yes turns location ON and it STAYS on until
they turn it off. The asked flag + pending ask live in og_gps
(gpslocask:<uid>); the flag is set the moment the ask is made, so
yes, no or ignored, OG never initiates it again. A YES (strict
whole-message parse, deliberately NOT garble-tolerated — a typo'd
yes never flips a persistent switch) flips the SAME R73 switch
via og_gps.set_location_on and OG confirms + asks the visitor to
say the destination once more, so the route can compute from the
fix the client attaches on that next ask; a NO falls back to the
existing start-point clarify, byte-identical. Coordinates are
never invented or stored server-side; chat text never contains
coordinates (the Round 68 rule stands).

Caches: geocodes 5 min, place lists + routes ~4 min (public data,
shared across visitors — same as the Round 4 data pack caches; no
visitor data is ever keyed or stored here).
"""

import logging
import math
import os
import re
import threading
import time
import urllib.parse

import og_garble

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "OG-AI/1.0 (+https://og-ai-service.onrender.com)"}

NOMINATIM_URL = os.getenv(
    "OG_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
OSRM_URL = os.getenv(
    "OG_OSRM_URL", "https://router.project-osrm.org/route/v1/driving/")
_OVERPASS_DEFAULTS = ("https://overpass.openstreetmap.fr/api/interpreter",
                      "https://overpass.kumi.systems/api/interpreter")
OVERPASS_URLS = tuple(
    u.strip() for u in os.getenv("OG_OVERPASS_URLS", "").split(",")
    if u.strip()) or _OVERPASS_DEFAULTS

NOMINATIM_TIMEOUT = 10.0
OVERPASS_TIMEOUT = 22.0        # client-side; the QL timeout sits below it
OVERPASS_QL_TIMEOUT = 18
OSRM_TIMEOUT = 12.0
RADIUS_NEAR_M = 1500           # first pass around the reference point
RADIUS_WIDE_M = 5000           # one wider retry when <2 named results
GEOCODE_TTL = 300
RESULT_TTL = 240
MAX_PLACES = 5

# ---------------------------------------------------------------------------
# Small TTL cache (public geo data only, keyed by query text/coords)
# ---------------------------------------------------------------------------

_cache = {}
_cache_lock = threading.Lock()


def _cached(key):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def _store(key, value, ttl):
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)
    return value


# ---------------------------------------------------------------------------
# HTTP helpers (sync httpx, like the Round 4 data pack)
# ---------------------------------------------------------------------------

def _get_json(url, params=None, timeout=10.0):
    import httpx
    try:
        with httpx.Client(timeout=timeout, headers=_UA,
                          follow_redirects=True) as client:
            r = client.get(url, params=params)
        if r.status_code != 200:
            logger.warning(f"Maps GET {url} -> {r.status_code}")
            return None
        return r.json()
    except Exception as e:
        logger.warning(f"Maps GET {url} failed: {e}")
        return None


def _overpass(query):
    """POST one Overpass QL query; primary host, one mirror fallback.
    Returns the parsed JSON or None when both hosts fail."""
    import httpx
    for url in OVERPASS_URLS:
        try:
            with httpx.Client(timeout=OVERPASS_TIMEOUT,
                              headers=_UA) as client:
                r = client.post(url, data={"data": query})
            if r.status_code != 200:
                logger.warning(f"Overpass {url} -> {r.status_code}")
                continue
            return r.json()
        except Exception as e:
            logger.warning(f"Overpass {url} failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Geo primitives
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _fmt_dist(meters):
    mi = meters / 1609.344
    if mi < 0.1:
        return f"{int(round(meters * 3.28084))} ft"
    return f"{mi:.1f} mi"


def _fmt_duration(seconds):
    mins = int(round(seconds / 60.0))
    if mins < 1:
        return "under a minute"
    if mins < 60:
        return f"about {mins} min"
    h, m = divmod(mins, 60)
    return f"about {h} h {m:02d} min" if m else f"about {h} h"


def _maps_link(name, area):
    q = urllib.parse.quote_plus(f"{name} {area}".strip())
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def _dir_link(origin_label, dest_label):
    o = urllib.parse.quote_plus(origin_label)
    d = urllib.parse.quote_plus(dest_label)
    return ("https://www.google.com/maps/dir/?api=1"
            f"&origin={o}&destination={d}")


def _dir_link_from_here(dest_label):
    """Directions link with NO origin baked in: Google Maps opens
    it with the visitor's own current location as the start. The
    Round 11 route answer uses this so the coordinates the visitor
    shared ride NO text that could be quoted back or saved — the
    link works from wherever they actually are."""
    d = urllib.parse.quote_plus(dest_label)
    return ("https://www.google.com/maps/dir/?api=1"
            f"&destination={d}")


# ---------------------------------------------------------------------------
# Round 11: per-request visitor coordinates ("near me")
# ---------------------------------------------------------------------------

# The current exchange's shared coordinates, noted by app.py under
# _memory_lock (alongside its _current_uid slot) and consumed by the
# maps seam. Single slot is safe for the same reason app.py's own
# slots are: chat processing is serialized under that lock.
_request_coords = {"uid": None, "coords": None}


def _valid_coords(raw):
    """A raw coords payload -> (lat, lon) floats, or None. Rejects
    non-dicts, missing/non-numeric fields, out-of-range values and
    the (0, 0) null-island placeholder browsers/tests love to send."""
    if not isinstance(raw, dict):
        return None
    try:
        lat = float(raw.get("lat"))
        lon = float(raw.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return (lat, lon)


def note_request_coords(raw, uid):
    """app.py binding: note this exchange's coordinates (validated)
    in the per-request slot. Called for EVERY chat exchange — with
    None when the request carried none — and again with None when
    the exchange ends, so a value can never leak into another
    visitor's answer. Nothing here is persisted or logged."""
    _request_coords["uid"] = uid
    _request_coords["coords"] = _valid_coords(raw)


def _take_request_coords(uid):
    """Read + clear the slot for the exchange being processed. Only
    the uid the slot was noted for may consume it; a mismatched or
    empty slot reads as 'no location shared'."""
    noted_uid = _request_coords.get("uid")
    coords = _request_coords.get("coords")
    _request_coords["uid"] = None
    _request_coords["coords"] = None
    if coords is None or noted_uid != uid:
        return None
    return coords


# ---------------------------------------------------------------------------
# Fetchers: geocode (Nominatim), nearby (Overpass), route (OSRM)
# ---------------------------------------------------------------------------

def geocode(place):
    """One place name -> {"lat", "lon", "label", "locality"} or None.
    Cached 5 min; label is the source's own display name (trimmed),
    locality the city/town the point sits in."""
    q = " ".join(str(place or "").split()).strip()
    if not q:
        return None
    key = "geo:" + q.lower()
    hit = _cached(key)
    if hit is not None:
        return hit or None
    data = _get_json(NOMINATIM_URL, params={
        "q": q, "format": "jsonv2", "limit": 1, "addressdetails": 1,
    }, timeout=NOMINATIM_TIMEOUT)
    out = None
    if data:
        try:
            top = data[0]
            addr = top.get("address") or {}
            locality = (addr.get("city") or addr.get("town")
                        or addr.get("village") or addr.get("hamlet")
                        or addr.get("county") or "")
            display = (top.get("display_name") or q)
            out = {
                "lat": float(top["lat"]), "lon": float(top["lon"]),
                "label": display.split(",")[0].strip() or q,
                "locality": locality,
                "display": display,
            }
        except Exception as e:
            logger.warning(f"Geocode parse failed for {q!r}: {e}")
            out = None
    _store(key, out or False, GEOCODE_TTL)
    return out


def _element_point(el):
    if el.get("lat") is not None and el.get("lon") is not None:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center") or {}
    if c.get("lat") is not None and c.get("lon") is not None:
        return float(c["lat"]), float(c["lon"])
    return None


def _element_address(tags, locality):
    num = (tags.get("addr:housenumber") or "").strip()
    street = (tags.get("addr:street") or "").strip()
    city = (tags.get("addr:city") or "").strip() or locality
    line = " ".join(p for p in (num, street) if p)
    if line and city:
        return f"{line}, {city}"
    if line:
        return line
    hood = (tags.get("addr:suburb") or tags.get("addr:neighbourhood")
            or "").strip()
    if hood and city:
        return f"{hood}, {city}"
    return city or ""


def nearby_places(lat, lon, category, locality=""):
    """Real OSM places of `category` around (lat, lon), nearest first:
    [{"name", "address", "dist_m", "hours", "link"}]. [] on a clean
    zero, None when Overpass is unreachable (caller falls through)."""
    groups = _CATEGORIES[category]["groups"]
    key = (f"places:{category}:{round(lat, 3)}:{round(lon, 3)}")
    hit = _cached(key)
    if hit is not None:
        return hit
    places = None
    for radius in (RADIUS_NEAR_M, RADIUS_WIDE_M):
        clauses = []
        for group in groups:
            filt = "".join(group)
            # NODES ONLY, deliberately: way clauses force Overpass to
            # compute polygon centers for every match and timed out
            # (>30 s) on the public instances in testing, while the
            # same node-only query answers in ~5–15 s. The trade-off
            # is real — a place mapped only as a building outline is
            # missed — but urban POIs are overwhelmingly nodes, and a
            # fast answer beats a complete one that never arrives.
            clauses.append(
                f"node{filt}(around:{radius},{lat:.6f},{lon:.6f});")
        query = (f"[out:json][timeout:{OVERPASS_QL_TIMEOUT}];\n(\n"
                 + "\n".join(clauses) + "\n);\nout center 40;")
        data = _overpass(query)
        if data is None:
            return None
        found, seen = [], set()
        for el in data.get("elements") or []:
            tags = el.get("tags") or {}
            name = (tags.get("name") or "").strip()
            if not name:
                continue
            pt = _element_point(el)
            if pt is None:
                continue
            dedupe = (name.lower(), round(pt[0], 4), round(pt[1], 4))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            dist = _haversine_m(lat, lon, pt[0], pt[1])
            address = _element_address(tags, locality)
            found.append({
                "name": name,
                "address": address,
                "dist_m": dist,
                "hours": (tags.get("opening_hours") or "").strip(),
                "link": _maps_link(name, address or locality),
            })
        found.sort(key=lambda p: p["dist_m"])
        places = found
        if len(found) >= 2:
            break
    return _store(key, (places or [])[:MAX_PLACES], RESULT_TTL)


def route_between(origin, destination):
    """Driving route between two geocode() results -> {"dist_m",
    "secs"} or None. Cached ~4 min."""
    key = (f"route:{round(origin['lat'], 4)}:{round(origin['lon'], 4)}"
           f":{round(destination['lat'], 4)}"
           f":{round(destination['lon'], 4)}")
    hit = _cached(key)
    if hit is not None:
        return hit or None
    url = (f"{OSRM_URL}{origin['lon']:.6f},{origin['lat']:.6f};"
           f"{destination['lon']:.6f},{destination['lat']:.6f}")
    data = _get_json(url, params={"overview": "false"},
                     timeout=OSRM_TIMEOUT)
    out = None
    if data and data.get("code") == "Ok" and data.get("routes"):
        try:
            r0 = data["routes"][0]
            out = {"dist_m": float(r0["distance"]),
                   "secs": float(r0["duration"])}
        except Exception as e:
            logger.warning(f"OSRM parse failed: {e}")
            out = None
    _store(key, out or False, RESULT_TTL)
    return out


# ---------------------------------------------------------------------------
# Categories (scan order matters: specific before generic)
# ---------------------------------------------------------------------------

def _cat(label, groups, triggers):
    return {"label": label, "groups": groups,
            "re": re.compile(triggers, re.IGNORECASE)}


_CATEGORIES = {
    "ev_charging": _cat("EV charging stations",
        [['["amenity"="charging_station"]']],
        r"\bev charging\b|\bcharging stations?\b|\bsuperchargers?\b"),
    "pizza": _cat("pizza places",
        [['["amenity"~"^(restaurant|fast_food)$"]["cuisine"~"pizza",i]']],
        r"\bpizza\b|\bpizzeria\b"),
    "sushi": _cat("sushi places",
        [['["amenity"~"^(restaurant|fast_food)$"]["cuisine"~"sushi",i]']],
        r"\bsushi\b"),
    "mexican": _cat("Mexican food",
        [['["amenity"~"^(restaurant|fast_food)$"]'
          '["cuisine"~"mexican|taco",i]']],
        r"\bmexican (food|restaurant)\b|\btacos?\b|\bburritos?\b"),
    "chinese": _cat("Chinese food",
        [['["amenity"~"^(restaurant|fast_food)$"]["cuisine"~"chinese",i]']],
        r"\bchinese (food|restaurant)\b"),
    "barber": _cat("barbers & hair salons",
        [['["shop"="hairdresser"]']],
        r"\bbarbers?\b|\bbarbershop\b|\bhair salon\b|\bhaircut\b"),
    "hardware": _cat("hardware stores",
        [['["shop"="hardware"]'], ['["shop"="doityourself"]']],
        r"\bhardware stores?\b|\bhome improvement\b|\bhome depot\b"
        r"|\blowe'?s\b"),
    "parking": _cat("parking",
        [['["amenity"="parking"]']],
        r"\bparking\b|\bwhere (to|can i) park\b"),
    "atm": _cat("ATMs",
        [['["amenity"="atm"]']],
        r"\batms?\b|\bcash machine\b|\bcashpoint\b"),
    "gas": _cat("gas stations",
        [['["amenity"="fuel"]']],
        r"\bgas stations?\b|\bfuel stations?\b|\bpetrol stations?\b"
        r"|\bwhere can i get gas\b|\bgas\b"),
    "pharmacy": _cat("pharmacies",
        [['["amenity"="pharmacy"]']],
        r"\bpharmacy\b|\bpharmacies\b|\bdrug ?stores?\b"),
    "hospital": _cat("hospitals",
        [['["amenity"="hospital"]']],
        r"\bhospitals?\b|\bemergency room\b"),
    "clinic": _cat("clinics & dentists",
        [['["amenity"="clinic"]'], ['["amenity"="dentists"]'],
         ['["amenity"="doctors"]']],
        r"\burgent care\b|\bclinics?\b|\bdentists?\b"
        r"|\bdoctor'?s office\b"),
    "hotel": _cat("hotels & places to stay",
        [['["tourism"="hotel"]'], ['["tourism"="motel"]'],
         ['["tourism"="hostel"]'], ['["tourism"="guest_house"]']],
        r"\bhotels?\b|\bmotels?\b|\bplaces? to stay\b|\blodging\b"),
    "grocery": _cat("grocery stores",
        [['["shop"="supermarket"]'], ['["shop"="grocery"]']],
        r"\bgrocery stores?\b|\bsupermarkets?\b|\bgroceries\b"),
    "gym": _cat("gyms",
        [['["leisure"="fitness_centre"]']],
        r"\bgyms?\b|\bfitness cent(er|re)\b|\bhealth club\b"),
    "bank": _cat("banks",
        [['["amenity"="bank"]']],
        r"\bbanks?\b"),
    "library": _cat("libraries",
        [['["amenity"="library"]']],
        r"\blibrary\b|\blibraries\b"),
    "post_office": _cat("post offices",
        [['["amenity"="post_office"]']],
        r"\bpost offices?\b"),
    "bakery": _cat("bakeries",
        [['["shop"="bakery"]']],
        r"\bbakery\b|\bbakeries\b"),
    "liquor": _cat("liquor stores",
        [['["shop"="alcohol"]']],
        r"\bliquor stores?\b|\bliquor\b"),
    "books": _cat("bookstores",
        [['["shop"="books"]']],
        r"\bbook ?stores?\b|\bbook ?shops?\b"),
    "pet": _cat("pet stores",
        [['["shop"="pet"]']],
        r"\bpet stores?\b|\bpet shops?\b"),
    "laundry": _cat("laundromats",
        [['["shop"="laundry"]']],
        r"\blaundromats?\b|\blaundry\b"),
    "car_repair": _cat("auto repair shops",
        [['["shop"="car_repair"]'], ['["shop"="tyres"]']],
        r"\bmechanic\b|\bauto repair\b|\bcar repair\b|\btire shops?\b"
        r"|\boil change\b"),
    "cinema": _cat("movie theaters",
        [['["amenity"="cinema"]']],
        r"\bmovie theat(er|re)s?\b|\bcinema\b"),
    "museum": _cat("museums",
        [['["tourism"="museum"]']],
        r"\bmuseums?\b"),
    "church": _cat("places of worship",
        [['["amenity"="place_of_worship"]']],
        r"\bchurch(es)?\b|\bplace of worship\b"),
    "bar": _cat("bars & pubs",
        [['["amenity"="bar"]'], ['["amenity"="pub"]']],
        r"\bbars?\b|\bpubs?\b|\bbrewer(y|ies)\b|\btavern\b"),
    "park": _cat("parks",
        [['["leisure"="park"]']],
        r"\bparks?\b"),
    "fast_food": _cat("fast food",
        [['["amenity"="fast_food"]']],
        r"\bfast food\b|\bburger joint\b|\bburger place\b"
        r"|\bdrive[- ]?thru\b"),
    "restaurant": _cat("restaurants",
        [['["amenity"="restaurant"]']],
        r"\brestaurants?\b|\beatery\b|\beateries\b|\bdiner\b"
        r"|\bplaces? to eat\b"),
    "cafe": _cat("coffee shops",
        [['["amenity"="cafe"]'], ['["shop"="coffee"]']],
        r"\bcoffee shops?\b|\bcoffeehouse\b|\bcafé\b|\bcafe\b"
        r"|\bespresso\b|\bstarbucks\b|\bcoffee\b"),
}

# ---------------------------------------------------------------------------
# Intent parsing (the visitor's raw message -> a maps job, or None)
# ---------------------------------------------------------------------------

_FILLER_TAIL = re.compile(
    r"\s+(please|right now|today|tonight|asap|thanks|thank you|for me"
    r"|near me|around here)$", re.IGNORECASE)
_LOC_PREP = re.compile(
    r"\b(near|around|close to|in|at|by)\s+(.+)$", re.IGNORECASE)
_PLACE_VERBS = re.compile(
    r"\b(where('s| is| are| can i)|find|show me|search|look(ing)? for"
    r"|locate|get me|give me|any|some|nearest|closest|nearby)\b",
    re.IGNORECASE)

_ROUTE_FROM_TO = re.compile(
    r"\bfrom\s+(.+?)\s+to\s+(.+)$", re.IGNORECASE)
_ROUTE_FAR_FROM = re.compile(
    r"\bhow far\s+(?:is\s+(?:it\s+|that\s+)?)?(.+?)\s+from\s+(.+)$",
    re.IGNORECASE)
_ROUTE_FAR_TO = re.compile(
    r"\bhow far\s+(?:is\s+(?:it|that)\s+)?to\s+(.+)$", re.IGNORECASE)
_ROUTE_DISTANCE = re.compile(
    r"\bdistance\s+(?:from\s+(.+?)\s+to\s+(.+)|between\s+(.+?)\s+and\s+(.+))$",
    re.IGNORECASE)
_ROUTE_HOW = re.compile(
    r"\b(?:directions|how\s+(?:do|can|would)\s+i\s+get|how\s+to\s+get"
    r"|how\s+long\s+(?:does\s+it\s+take|is\s+the\s+drive|to\s+drive)?"
    r"|drive|driving|get)\s+(?:from\s+(.+?)\s+)?to\s+(.+)$",
    re.IGNORECASE)
_ROUTE_KEYWORD = re.compile(
    r"\b(how far|distance|directions|how (do|can) i get|how to get"
    r"|how long)\b", re.IGNORECASE)


def _clean_place(text):
    """Trim a captured place phrase down to the geocodable core."""
    t = " ".join(str(text or "").split()).strip(" .?!,;:")
    t = re.sub(r"^(the|a|an)\s+", "", t, flags=re.IGNORECASE)
    for _ in range(3):
        t2 = _FILLER_TAIL.sub("", t).strip(" .?!,;:")
        if t2 == t:
            break
        t = t2
    t = re.sub(r"\s+(by car|driving|on foot|from here)$", "", t,
               flags=re.IGNORECASE).strip(" .?!,;:")
    return t


def _parse_route(low, original):
    """Route intent -> {"kind": "route", "origin", "destination"}.
    Either endpoint may be None (the caller asks about the missing
    one); returns None when this isn't a route question at all."""
    if not _ROUTE_KEYWORD.search(low):
        return None
    origin = destination = None
    m = _ROUTE_FAR_FROM.search(low)
    if m and "how far" in low:
        destination, origin = m.group(1), m.group(2)
    if destination is None:
        m = _ROUTE_FROM_TO.search(low)
        if m:
            origin, destination = m.group(1), m.group(2)
    if destination is None:
        m = _ROUTE_DISTANCE.search(low)
        if m:
            origin = m.group(1) or m.group(3)
            destination = m.group(2) or m.group(4)
    if destination is None:
        m = _ROUTE_FAR_TO.search(low)
        if m:
            destination = m.group(1)
    if destination is None:
        m = _ROUTE_HOW.search(low)
        if m:
            origin, destination = m.group(1), m.group(2)
    if origin is None and destination is None:
        return None
    origin = _clean_place(origin) if origin else None
    destination = _clean_place(destination) if destination else None
    if origin and origin.lower() in ("here", "me", "my location"):
        origin = None
    if destination and destination.lower() in ("here", "me"):
        destination = None
    if not origin and not destination:
        return None
    return {"kind": "route", "origin": origin,
            "destination": destination}


_CREATIVE_ASK = re.compile(
    r"\b(tell|write|make|sing|give|show)\b[^.!?]*"
    r"\b(story|stories|joke|poem|song|tale|essay)\b", re.IGNORECASE)


def _parse_places(low, original):
    """Category-near-place intent -> {"kind": "places", "category",
    "location"}; location None means 'ask where'. None when the
    message names a category without any place-seeking shape, or no
    category at all (normal chat / other tools' turf)."""
    if "how much" in low or "price" in low:
        return None  # a price question — Round 3/4 turf, not places
    if _CREATIVE_ASK.search(low):
        return None  # "tell me a story about a bar…" is a story ask
    category = None
    for key, spec in _CATEGORIES.items():
        if spec["re"].search(low):
            category = key
            break
    if category is None:
        return None
    prep = None
    for m in _LOC_PREP.finditer(low):
        prep = m  # keep the last preposition phrase
    has_shape = bool(_PLACE_VERBS.search(low)) or prep is not None
    if not has_shape:
        return None
    location = None
    if prep is not None:
        loc = _clean_place(prep.group(2))
        if loc and loc.lower() not in ("me", "here", "this area",
                                       "town", "the area"):
            location = loc
    return {"kind": "places", "category": category,
            "location": location}


# Round 40: bare-place / landmark lookup. A bare famous-place name
# ("Pike Place") or "where is <place>?" has no category and no route
# keyword, so it used to parse to nothing at all. A small table of
# well-known landmarks canonicalizes the name (bare "Pike Place"
# means Pike Place Market, Seattle); the answer is grounded in the
# geocoder's own display name, nothing invented.
_LANDMARKS = {
    "pike place": "Pike Place Market, Seattle",
    "pike place market": "Pike Place Market, Seattle",
    "space needle": "Space Needle, Seattle",
    "eiffel tower": "Eiffel Tower, Paris",
    "statue of liberty": "Statue of Liberty, New York",
    "golden gate bridge": "Golden Gate Bridge, San Francisco",
    "times square": "Times Square, New York",
    "grand canyon": "Grand Canyon",
    "yellowstone": "Yellowstone National Park",
    "mount rainier": "Mount Rainier",
    "central park": "Central Park, New York",
    "hollywood sign": "Hollywood Sign, Los Angeles",
    "las vegas strip": "Las Vegas Strip, Las Vegas",
    "niagara falls": "Niagara Falls",
    "white house": "White House, Washington DC",
}
_LANDMARK_ASK = re.compile(
    r"^(?:where(?:'s| is)|find|locate|show me)?\s*(.+?)\s*\??$",
    re.IGNORECASE)


def _parse_landmark(low, original):
    """Bare landmark name / 'where is X?' -> {"kind": "landmark",
    "name": canonical}. Only table entries resolve — an arbitrary
    place name stays normal chat."""
    m = _LANDMARK_ASK.match(low.strip())
    if not m:
        return None
    name = " ".join(m.group(1).split()).strip(" .?!,;:")
    name = re.sub(r"^(the)\s+", "", name)
    canonical = _LANDMARKS.get(name)
    if not canonical:
        return None
    return {"kind": "landmark", "name": canonical}


def parse_maps_intent(message):
    """Parse a maps job out of a chat message, or None. Route shapes
    win over category shapes ('how far is the coffee shop from…' is a
    route question)."""
    if not message:
        return None
    low = " ".join(str(message).lower().split())
    return _parse_route(low, message) or _parse_places(low, message) \
        or _parse_landmark(low, message)



# ---------------------------------------------------------------------------
# Location-switch asks (Round 73): "turn my location on" / "location off"
# ---------------------------------------------------------------------------
# The switch itself lives in og_gps (gpsloc:<uid>) — the same one
# GET/POST /gps/location serves and both clients read. These asks
# are matched STRICTLY: after trimming a short polite lead/trail,
# the WHOLE message must be the ask, so a phrase that merely
# MENTIONS location — "weather at my location", "directions from
# my location to ..." — never flips anything. OG never turns the
# switch on by himself: the only way here is the visitor's own
# words, in the shapes below.

_LOC_LEAD = re.compile(
    r"^(hey og|okay og|ok og|og|please)[\s,]+", re.IGNORECASE)
_LOC_TOGGLE = re.compile(
    r"^(?:"
    r"(?:turn|switch|flip|set)\s+(?:my\s+|the\s+)?location\s+(on|off)"
    r"|(?:turn|switch)\s+(on|off)\s+(?:my\s+|the\s+)?location"
    r"|(?:my\s+)?location\s+(on|off)"
    r"|location\s+sharing\s+(on|off)"
    r"|(enable|disable)\s+(?:my\s+|the\s+)?location"
    r"|(start|stop)\s+sharing\s+(?:my\s+)?location"
    r")$",
    re.IGNORECASE)


def parse_location_toggle(message):
    """'on' / 'off' when the message IS a location-switch ask,
    else None. Whole-message match only (see above)."""
    if not message:
        return None
    low = " ".join(str(message).lower().split()).strip(" .!?")
    for _ in range(2):
        trimmed = _LOC_LEAD.sub("", low).strip(" ,")
        if trimmed == low:
            break
        low = trimmed
    low = re.sub(r"[\s,]+please$", "", low).strip(" ,")
    m = _LOC_TOGGLE.match(low)
    if not m:
        return None
    for group in m.groups():
        if group:
            return "on" if group.lower() in ("on", "enable",
                                             "start") else "off"
    return None


def _location_toggle_results(uid, want):
    """Flip the account's location switch (og_gps) and hand OG a
    grounded note to confirm from — the Round 68 note pattern, so
    the confirmation rides the normal reply/register path. The
    flip has already happened server-side when the note is
    written; the note reports the state actually stored. No uid
    (or a store failure) -> None, and the ask falls through to
    normal chat with nothing flipped."""
    if not uid:
        return None
    try:
        import og_gps
        on = og_gps.set_location_on(uid, want == "on")
    except Exception as e:
        logger.warning(f"Location switch flip failed: {e}")
        return None
    if on:
        body = (
            "(Location note for OG: the visitor just asked you "
            "to turn their location ON, and it is DONE — their "
            "account location switch is ON now and stays on "
            "until they turn it off. Confirm it plainly in your "
            "own voice: location is on. This switch is OG's "
            "permission to use their location; their phone or "
            "browser still has its own location permission "
            "prompt, which is separate — do NOT claim you "
            "changed the device's own settings.)")
    else:
        body = (
            "(Location note for OG: the visitor just asked you "
            "to turn their location OFF, and it is DONE — their "
            "account location switch is OFF now and stays off "
            "until they turn it on. Confirm it plainly in your "
            "own voice: location is off, and you will not use "
            "their location while it is off.)")
    return [{"title": "Location switch", "body": body, "href": ""}]


# ---------------------------------------------------------------------------
# One-time location permission ask (Round 78)
# ---------------------------------------------------------------------------
# Brent's rule: "Only ask one time then it stay on until they
# turn it off." When a directions ask reaches the route seam
# with a destination but no named start and no shared
# coordinates, the switch OFF and the account never asked,
# OG asks permission ONE TIME instead of the start-point
# clarify: use your location to navigate you there? A yes
# turns location ON and it STAYS on until they turn it off.
# The asked flag + the pending ask live in og_gps
# (gpslocask:<uid>) — set the moment the ask is made, so
# yes, no or ignored, the ask is never initiated again
# (turning the switch on manually also marks it asked —
# see og_gps.set_location_on). The answer parse is STRICT
# whole-message, in the R73 toggle's style, and Round 75's
# garble repair is deliberately NOT applied to it: a
# garbled "yse" must never flip a persistent switch. A
# yes/no with no pending ask (or an expired one) changes
# nothing at all.

_LOC_ASK_YES = re.compile(
    r"^(?:yes|yeah|yep|yup|ok|okay|sure|yes please|please do"
    r"|go ahead|do it|turn it on)$",
    re.IGNORECASE)
_LOC_ASK_NO = re.compile(
    r"^(?:no|nope|nah|no thanks|no thank you|not now|no way"
    r"|don'?t|do not)$",
    re.IGNORECASE)


def parse_location_ask_answer(message):
    """'yes' / 'no' when the WHOLE message is a plain answer
    to the one-time location permission ask, else None.
    Strict on purpose (see above): the same polite
    lead/trail trim as the R73 toggle, then an anchored
    full match — compound replies are not answers."""
    if not message:
        return None
    low = " ".join(str(message).lower().split()).strip(" .!?")
    for _ in range(2):
        trimmed = _LOC_LEAD.sub("", low).strip(" ,")
        if trimmed == low:
            break
        low = trimmed
    low = re.sub(r"[\s,]+please$", "", low).strip(" ,")
    if _LOC_ASK_YES.match(low):
        return "yes"
    if _LOC_ASK_NO.match(low):
        return "no"
    return None


def _loc_ask_pending(uid):
    """The account's live pending one-time ask, or None.
    Store failures read as no pending — the exchange then
    just runs as normal chat, nothing flipped, nothing
    asked."""
    if not uid:
        return None
    try:
        import og_gps
        return og_gps.location_pending_ask(uid)
    except Exception as e:
        logger.warning(f"Location ask pending read failed: {e}")
        return None


def _loc_permission_ask_results(parsed, uid, original_ask=""):
    """The one-time permission ask, or None when it does not
    apply (no uid, the switch already ON, or the account
    already asked) — the caller then returns the start-point
    clarify exactly as before. When it applies, the asked
    flag + pending ask are stored BEFORE the question is
    handed to OG: the flag is set the moment the ask is
    made, whatever the answer turns out to be."""
    if not uid:
        return None
    try:
        import og_gps
        if og_gps.location_on_for(uid):
            return None  # already on — the R68 path, unchanged
        if og_gps.location_asked_for(uid):
            return None  # asked once already — never again
        dest = parsed.get("destination") or ""
        if not og_gps.set_location_pending_ask(
                uid, dest,
                original_ask or f"directions to {dest}"):
            return None
    except Exception as e:
        logger.warning(f"Location permission ask failed: {e}")
        return None
    return _clarify_result(
        f"(Maps note for OG: the visitor asked for "
        f"directions to {dest} but did not say where they "
        f"are starting from, and their location switch is "
        f"OFF. This is the ONE time you ever ask this — do "
        f"NOT just ask where they are starting from. Ask "
        f"them, plainly and in your own voice, for "
        f"permission to use their location to navigate "
        f"them to {dest}: a yes turns their location ON, "
        f"and it STAYS on until they turn it off (the "
        f"Location switch in the Driving map, or telling "
        f"you 'turn my location off'). If they say no, "
        f"that is fine — you will ask where they are "
        f"starting from instead. Ask the question and wait "
        f"for their yes or no. Do NOT invent a starting "
        f"point, a route or a distance, and do NOT claim "
        f"you have their location — you do not, yet.)")


def _loc_ask_answer_results(uid, answer):
    """Answer the pending one-time ask. YES flips the SAME
    R73 switch (og_gps.set_location_on — no parallel store)
    and OG confirms from a grounded note + asks for the
    destination once more: the device fix only rides along
    once the client sees the switch on, and coordinates
    are never invented or stored server-side. NO keeps the
    switch off and falls back to the existing start-point
    clarify for the stored destination, byte-identical to
    the no-origin path. No live pending ask -> None (the
    message was just chat; nothing flips)."""
    pending = _loc_ask_pending(uid)
    if not pending:
        return None
    try:
        import og_gps
        og_gps.clear_location_pending_ask(uid)
        if answer == "yes":
            on = og_gps.set_location_on(uid, True)
            if not on:
                return None  # the flip did not land — fall through
            dest = pending.get("destination") or ""
            body = (
                "(Location note for OG: the visitor just "
                "said YES to your one-time ask, and it is "
                "DONE — their account location switch is ON "
                "now and STAYS on until they turn it off. "
                "Confirm it plainly in your own voice: "
                "location is on, and it stays on until they "
                "turn it off (the Location switch in the "
                "Driving map, or telling you 'turn my "
                f"location off'). Then ask them to say "
                f"their destination once more — "
                f"'{dest}' is what they asked for — so you "
                f"can route from where they are standing: "
                f"their phone attaches where they are to "
                f"that next ask now that the switch is on. "
                f"Do NOT invent coordinates, a route or a "
                f"distance yet, and do NOT claim you can "
                f"see where they are right this second — "
                f"the switch is permission, and their phone "
                f"or browser still has its own location "
                f"permission prompt, which is separate.)")
            return [{"title": "Location switch", "body": body,
                     "href": ""}]
    except Exception as e:
        logger.warning(f"Location ask answer failed: {e}")
        return None
    # answer == "no": the switch stays OFF and the stored
    # ask gets the existing start-point clarifying behavior
    # — the very results the no-origin path returns today.
    return _route_results({
        "kind": "route", "origin": None,
        "destination": pending.get("destination") or None})


# ---------------------------------------------------------------------------
# Result building (rides the web_search seam, {title, body, href})
# ---------------------------------------------------------------------------

def _clarify_result(text):
    return [{"title": "Maps — need one detail", "body": text,
             "href": ""}]


def _places_results(parsed):
    label = _CATEGORIES[parsed["category"]]["label"]
    location = parsed["location"]
    if not location:
        return _clarify_result(
            f"(Maps note for OG: the visitor asked for {label} but "
            "did NOT name a city or area, and no location was "
            "shared for this question. Do NOT invent a place or "
            "any results. Ask them ONE short question in your own "
            "voice: what city or neighborhood should you look in? "
            "They can also tap the 📍 location button in the chat "
            "to share where they are for one answer.)")
    ref = geocode(location)
    if ref is None:
        return None  # couldn't pin the place — fall through
    places = nearby_places(ref["lat"], ref["lon"], parsed["category"],
                            ref["locality"])
    if places is None:
        return None  # Overpass down — fall through to the lookup chain
    area = ref["locality"] or ref["label"] or location
    if not places:
        return [{
            "title": f"Places near {area} (OpenStreetMap, live)",
            "body": (f"(Maps note for OG: OpenStreetMap has NO "
                     f"{label} listed near {area} right now. Tell "
                     f"the visitor straight that nothing came up "
                     f"near {area} — do NOT invent places.)"),
            "href": f"https://www.openstreetmap.org/#map=14/"
                    f"{ref['lat']:.5f}/{ref['lon']:.5f}",
        }]
    ref_label = ref["label"] or area
    lines = []
    for p in places:
        line = f"- {p['name']}"
        if p["address"]:
            line += f" — {p['address']}"
        line += f" · {_fmt_dist(p['dist_m'])} from {ref_label}"
        if p["hours"]:
            line += f" · Hours: {p['hours']}"
        line += f" · Map: {p['link']}"
        lines.append(line)
    body = (
        f"The visitor asked for {label} near {area}. These are REAL "
        f"places from OpenStreetMap, nearest first — give them in "
        f"your own voice and include the map links:\n"
        + "\n".join(lines)
        + "\n(Only the details listed here are known — do NOT invent "
          "hours, ratings, reviews or phone numbers.)")
    return [
        {"title": f"{label} near {area} (OpenStreetMap, live)",
         "body": body[:1800], "href": places[0]["link"]},
        {"title": f"{label} near {area} — source",
         "body": f"https://www.openstreetmap.org/#map=14/"
                 f"{ref['lat']:.5f}/{ref['lon']:.5f}",
         "href": f"https://www.openstreetmap.org/#map=14/"
                 f"{ref['lat']:.5f}/{ref['lon']:.5f}"},
    ]


def _places_near_coords(parsed, coords):
    """Round 11: places around the visitor's shared coordinates.
    Distances are computed from the supplied point itself (never a
    geocoded stand-in); the coordinates appear in NO result text —
    only the places' own data is stated, framed as 'from you'."""
    lat, lon = coords
    label = _CATEGORIES[parsed["category"]]["label"]
    places = nearby_places(lat, lon, parsed["category"], "")
    if places is None:
        return None  # Overpass down — fall through to the lookup chain
    if not places:
        return [{
            "title": f"{label} near your location (OpenStreetMap, live)",
            "body": (f"(Maps note for OG: the visitor shared their "
                     f"location for THIS answer only, and "
                     f"OpenStreetMap has NO {label} listed near "
                     f"them right now. Tell them straight that "
                     f"nothing came up nearby — do NOT invent "
                     f"places, and do NOT state or guess where they "
                     f"are.)"),
            "href": f"https://www.openstreetmap.org/#map=14/"
                    f"{lat:.5f}/{lon:.5f}",
        }]
    lines = []
    for p in places:
        line = f"- {p['name']}"
        if p["address"]:
            line += f" — {p['address']}"
        line += f" · {_fmt_dist(p['dist_m'])} from the visitor"
        if p["hours"]:
            line += f" · Hours: {p['hours']}"
        line += f" · Map: {p['link']}"
        lines.append(line)
    body = (
        f"The visitor asked for {label} near them and shared their "
        f"location for this answer only. These are REAL places from "
        f"OpenStreetMap, nearest first — give them in your own "
        f"voice and include the map links:\n"
        + "\n".join(lines)
        + "\n(Only the details listed here are known — do NOT invent "
          "hours, ratings, reviews or phone numbers, and do NOT "
          "state or guess the visitor's location or coordinates.)")
    return [{
        "title": f"{label} near your location (OpenStreetMap, live)",
        "body": body[:1800], "href": places[0]["link"],
    }]


def _route_results(parsed, coords=None):
    origin_q, dest_q = parsed["origin"], parsed["destination"]
    if not dest_q:
        return _clarify_result(
            "(Maps note for OG: the visitor wants route/directions "
            "info but didn't say WHERE they're headed. Do NOT invent "
            "a destination. Ask them ONE short question in your own "
            "voice: where are they trying to get to?)")
    # A NAMED origin always wins; shared coordinates only stand in
    # when the message named no starting point ("from here…").
    origin_coords = None if origin_q else coords
    if not origin_q and not origin_coords:
        return _clarify_result(
            f"(Maps note for OG: the visitor wants a route to "
            f"{dest_q} but didn't say where they're starting from, "
            "and no location was shared for this question. Do NOT "
            "invent a starting point or a distance. Ask them ONE "
            "short question in your own voice: where are they "
            "starting from? They can also tap the 📍 location "
            "button in the chat to share where they are for one "
            "answer.)")
    b = geocode(dest_q)
    if b is None:
        return None  # couldn't pin the destination — fall through
    b_label = b["label"] or dest_q
    if origin_q:
        a = geocode(origin_q)
        if a is None:
            return None  # couldn't pin the origin — fall through
        a_label = a["label"] or origin_q
        link = _dir_link(a_label, b_label)
        from_desc = a_label
    else:
        a = {"lat": origin_coords[0], "lon": origin_coords[1]}
        a_label = "your location"
        link = _dir_link_from_here(b_label)
        from_desc = ("where the visitor is right now (they shared "
                     "their location for this answer only)")
    route = route_between(a, b)
    if route is None:
        return None  # OSRM down — fall through
    dist = _fmt_dist(route["dist_m"])
    dur = _fmt_duration(route["secs"])
    if origin_q:
        body = (
            f"Real driving route (OSRM, live): {a_label} to "
            f"{b_label} is {dist}, {dur} by car. Directions: "
            f"{link}. State it plainly in your own voice — do NOT "
            f"invent traffic conditions, road names or alternate "
            f"routes.")
        title = f"Route: {a_label} → {b_label} (OSRM, live)"
    else:
        body = (
            f"Real driving route (OSRM, live): from {from_desc} to "
            f"{b_label} is {dist}, {dur} by car. Directions: "
            f"{link} (the link carries no start point — it opens "
            f"directions from the visitor's own location when they "
            f"tap it). State it plainly in your own voice — do NOT "
            f"invent traffic conditions, road names or alternate "
            f"routes, and do NOT state or guess the visitor's "
            f"coordinates.")
        title = f"Route: your location → {b_label} (OSRM, live)"
    return [
        {"title": title, "body": body[:1800], "href": link},
        {"title": "Route — source", "body": link, "href": link},
    ]


def _place_not_found_results(destination):
    """Round 68: the honest 'couldn't find that place' answer
    for a directions ask — a grounded note for OG in the
    clarifying results' shape (an early answer, no route, no
    distance, nothing invented)."""
    return [{
        "title": "Maps — place not found",
        "body": (
            f"(Maps note for OG: the visitor asked for "
            f"directions to {destination}, but that place "
            f"could NOT be found on the map — no match came "
            f"back at all. Tell them straight, in your own "
            f"voice, that you couldn't find that place, and "
            f"ask them to check the spelling or add the "
            f"city. Do NOT invent the place, a route, a "
            f"distance or directions, and do NOT claim "
            f"anything was loaded into their GPS.)"),
        "href": "",
    }]


def _store_chat_route(parsed, uid, coords, results):
    """Round 68: a directions answer ALSO loads the route
    into the visitor's GPS — og_gps' active route, the one
    the app's Driving map opens on. The step route is
    computed from the same ends the text answer used: the
    exchange's shared coordinates when the message named
    no start (the coordinates themselves are never quoted
    into the answer — only the stored route's distance /
    duration / step count joins it), else the named origin
    place. ANY failure here leaves the plain answer
    standing: storing is a side-effect of a good answer,
    never a reason to break one."""
    try:
        import og_gps
        if coords:
            from_raw = {"lat": coords[0], "lon": coords[1]}
            from_label = "your location"
        else:
            from_raw = {"place": parsed.get("origin")}
            from_label = ""
        to_raw = {"place": parsed.get("destination")}
        route = og_gps.route_driving(from_raw, to_raw)
        if not route:
            return results
        stored = og_gps.store_route(
            uid, route, from_raw, to_raw,
            from_label=from_label,
            to_label=parsed.get("destination") or "")
        if not stored:
            return results
        line = (
            "\n(Maps note for OG: that route — "
            + _fmt_dist(stored["distance_m"]) + ", "
            + _fmt_duration(stored["duration_s"]) + ", "
            + f"{len(stored['steps'])} steps — is loaded "
            "in the visitor's GPS map now. Tell them it's "
            "on their map: the Driving map in the app shows "
            "it, and Start navigation on the map will guide "
            "it.)")
        first = dict(results[0])
        first["body"] = ((first.get("body") or "") + line)[:1800]
        return [first] + list(results[1:])
    except Exception as e:
        logger.warning(f"Chat route store failed: {e}")
        return results


def _landmark_results(parsed):
    """Ground a landmark answer in the geocoder's own record:
    its display name (which carries the city/area) + a map link.
    Nothing beyond that record is stated."""
    name = parsed["name"]
    ref = geocode(name)
    if ref is None:
        return None  # couldn't pin it — fall through
    display = ref.get("display") or name
    link = (f"https://www.openstreetmap.org/#map=15/"
            f"{ref['lat']:.5f}/{ref['lon']:.5f}")
    body = (
        f"The visitor asked about {name}. OpenStreetMap places it "
        f"here: {display}. Map: {link}. State where it is plainly, "
        f"in your own voice, from that record only — do NOT invent "
        f"history, hours, ticket prices or reviews.")
    return [{"title": f"{name} (OpenStreetMap, live)",
             "body": body[:1800], "href": link}]


def maps_search_results(parsed, uid, consume_lookup, coords=None):
    """Run one parsed maps job. Returns web_search-shaped results on
    a hit (or the clarifying-question note when a detail is missing),
    None on a miss (unknown place / upstream down) so the caller
    falls through to the previous search. One unit of the shared
    lookup budget is consumed per REAL answer — never for a
    clarifying ask, never for a miss.

    coords (Round 11) is the exchange's validated (lat, lon) or
    None. It only ever fills a gap the message left open — a place
    search with no named location, a route with no named origin. A
    named place in the message always wins over the coordinates."""
    if not parsed:
        return None
    if coords:
        # Accept the seam's validated (lat, lon) tuple or a raw
        # {"lat", "lon"} payload; either way it is (re)validated.
        if not isinstance(coords, dict):
            try:
                coords = {"lat": coords[0], "lon": coords[1]}
            except Exception:
                coords = None
        coords = _valid_coords(coords) if coords else None
    else:
        coords = None
    if parsed["kind"] == "landmark":
        results = _landmark_results(parsed)
    elif parsed["kind"] == "places":
        if parsed.get("location"):
            results = _places_results(parsed)   # named place wins
        elif coords:
            results = _places_near_coords(parsed, coords)
        else:
            return _places_results(parsed)  # clarifying ask, no spend
    elif parsed["kind"] == "route":
        if not parsed.get("destination"):
            return _route_results(parsed)   # clarifying ask, no spend
        if not parsed.get("origin") and not coords:
            # Round 78: the ONE-TIME location permission ask
            # replaces the start-point clarify when the
            # account's switch is off and it has never been
            # asked; otherwise this is the clarifying ask,
            # byte-identical, no spend either way.
            ask = _loc_permission_ask_results(parsed, uid)
            if ask:
                return ask
            return _route_results(parsed)   # clarifying ask, no spend
        if geocode(parsed["destination"]) is None:
            # Round 68: a complete directions ask (a named
            # start or a shared location) whose destination
            # cannot be found gets the honest answer HERE —
            # not a fall-through to web results OG would
            # have to dress up as directions. Nothing is
            # stored and no lookup is spent (the clarifying
            # shape above).
            return _place_not_found_results(parsed["destination"])
        results = _route_results(parsed, coords)
        if results and uid:
            # Round 68: the answered route is also loaded
            # into the visitor's GPS map (og_gps' active
            # route). A store failure never breaks the
            # answer — the helper degrades to the plain
            # results on its own.
            results = _store_chat_route(parsed, uid, coords,
                                        results)
    else:
        return None
    if not results:
        return None
    if uid and consume_lookup is not None:
        try:
            if not consume_lookup(uid):
                logger.info("Maps answer skipped: visitor at daily "
                            "lookup cap")
                return None
        except Exception as e:
            logger.warning(f"Maps budget consume failed: {e}")
            return None
    return results


# ---------------------------------------------------------------------------
# Garble tolerance (Round 75): the second chance for this seam
# ---------------------------------------------------------------------------
# The exact parsers above get first crack, always. Only when
# they say no does the repair run: tokens one small typo from
# this seam's vocabulary (route keywords, the place-category
# trigger words, landmark names, the R73 toggle's words) are
# rewritten, then the EXACT parsers re-run on the reading. The
# toggle keeps its whole-message anchor — repair fixes WORDS,
# never the shape — so compound asks still flip nothing, and
# place names in the ask are never in the vocabulary, so they
# reach the geocoder verbatim (it does its own fuzz).

_GARBLE_VOCAB = (
    # route shapes
    ["directions", "direction", "how", "far", "distance",
     "drive", "driving", "get", "to", "from", "between", "long",
     "take", "takes"]
    # place-seeking shapes
    + ["where", "find", "show", "search", "looking", "look",
       "locate", "give", "nearest", "closest", "nearby", "near",
       "around", "close", "in", "at", "by", "any", "some"]
    # category trigger words (the _CATEGORIES regex vocabulary)
    + ["coffee", "cafe", "espresso", "starbucks", "restaurant",
       "restaurants", "eatery", "eateries", "diner", "fast",
       "food", "burger", "joint", "drive-thru", "pizza",
       "pizzeria", "sushi", "mexican", "tacos", "burritos",
       "chinese", "bar", "bars", "pub", "pubs", "brewery",
       "breweries", "tavern", "park", "parks", "gas", "fuel",
       "petrol", "charging", "station", "stations", "pharmacy",
       "pharmacies", "drug", "hospital", "hospitals", "clinic",
       "clinics", "emergency", "urgent", "care", "hotel",
       "hotels", "motel", "motels", "lodging", "grocery",
       "groceries", "supermarket", "supermarkets", "parking",
       "atm", "cashpoint", "cash", "machine", "gym", "gyms",
       "fitness", "center", "centre", "health", "club", "bank",
       "banks", "library", "libraries", "post", "office",
       "offices", "bakery", "bakeries", "liquor", "book",
       "bookstore", "bookstores", "bookshop", "pet", "stores",
       "store", "shops", "shop", "laundromat", "laundromats",
       "laundry", "mechanic", "auto", "repair", "car", "tire",
       "oil", "change", "movie", "theater", "theaters",
       "theatre", "cinema", "museum", "museums", "church",
       "churches", "worship", "barber", "barbers", "barbershop",
       "salon", "hair", "haircut", "hardware", "home", "depot",
       "dentist", "dentists", "doctor", "stay", "room", "eat",
       "places", "place"]
    # landmark names (phrases resolve through _LANDMARKS)
    + list(_LANDMARKS)
    # the R73 location toggle's words
    + ["turn", "switch", "flip", "set", "location", "sharing",
       "enable", "disable", "start", "stop", "on", "off", "my",
       "the"]
)
_GARBLE_HOMOPHONES = {"too": "to"}


def _garble_parse(message):
    """(toggle, parsed, info) — this seam's garble second
    chance. (None, None, None) when the repair changed nothing
    or the reading still parses to nothing."""
    repaired, info = og_garble.repair_info(
        message, _GARBLE_VOCAB, _GARBLE_HOMOPHONES)
    if info is None:
        return None, None, None
    toggle = parse_location_toggle(repaired)
    if toggle:
        return toggle, None, info
    parsed = parse_maps_intent(repaired)
    if parsed:
        return None, parsed, info
    return None, None, None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6)
# ---------------------------------------------------------------------------

# The maps job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
# "loctoggle" (Round 73) rides the same slot discipline: the
# location-switch ask parsed for this exchange, if any.
# "garble" (Round 75) rides it too: the repair's fixes, when the
# exchange's job was claimed through the second chance.
# "locask" (Round 78) rides it too: the strict yes/no answer to
# the one-time location permission ask, when one is pending.
_pending = {"parsed": None, "loctoggle": None, "garble": None,
            "locask": None}


def install_maps_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file-wrapped) detect_intent +
    web_search hooks so place/route questions try og_maps FIRST and
    fall through to the previous search (data pack, then web lookup)
    on a miss. get_uid is a zero-arg callable returning the current
    visitor's uid; consume_lookup(uid) spends one unit of the shared
    Round 3 lookup budget and returns False at the cap. Persona files
    are never touched."""
    if getattr(agent_instance, "_og_maps_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["parsed"] = None
        _pending["loctoggle"] = None
        _pending["garble"] = None
        _pending["locask"] = None
        try:
            toggle = parse_location_toggle(str(message))
            if toggle:
                # A location-switch ask wins the exchange: it
                # is a settings flip, not a maps question.
                _pending["loctoggle"] = toggle
            else:
                parsed = parse_maps_intent(str(message))
                if parsed:
                    _pending["parsed"] = parsed
                else:
                    # Round 78: a strict yes/no answers the
                    # one-time location permission ask — but
                    # ONLY while one is actually pending for
                    # this account; a bare yes/no in normal
                    # chat stays untouched. Exact message
                    # only: the garble second chance below
                    # never gets to manufacture an answer.
                    answer = parse_location_ask_answer(
                        str(message))
                    if answer and _loc_ask_pending(get_uid()):
                        _pending["locask"] = answer
                    else:
                        # Round 75: the garble second chance —
                        # the exact parsers said no; re-run
                        # them on the repaired reading.
                        toggle, parsed, garble = _garble_parse(
                            str(message))
                        if toggle:
                            _pending["loctoggle"] = toggle
                        if parsed:
                            _pending["parsed"] = parsed
                        _pending["garble"] = garble
            if (toggle or _pending["parsed"]
                    or _pending["locask"]) \
                    and isinstance(intent, dict) \
                    and not intent.get("needs_web_search"):
                intent["needs_web_search"] = True
                intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Maps trigger check failed: {e}")
        return intent

    def search_wrapped(query, num_results=5):
        toggle = _pending.get("loctoggle")
        _pending["loctoggle"] = None
        garble = _pending.get("garble")
        _pending["garble"] = None
        locask = _pending.get("locask")
        _pending["locask"] = None
        if locask:
            # Round 78: the answer to the one-time location
            # permission ask — handled before everything
            # else, like the toggle. The handler re-reads
            # the pending ask from the store; if it has
            # expired since detect, it returns None and the
            # exchange falls through to normal chat.
            try:
                results = _loc_ask_answer_results(
                    get_uid(), locask)
            except Exception as e:
                logger.warning(f"Location ask answer failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        if toggle:
            try:
                results = _location_toggle_results(get_uid(), toggle)
            except Exception as e:
                logger.warning(f"Location toggle failed: {e}")
                results = None
            if results:
                if garble:
                    results = og_garble.annotate(
                        results, garble, prepend=True)
                return results[: max(1, num_results)]
        parsed = _pending.get("parsed")
        _pending["parsed"] = None
        if parsed:
            try:
                uid = get_uid()
                # Round 11: consume this exchange's shared
                # coordinates (if any) — read once, cleared here,
                # used for this answer only, never stored/logged.
                coords = _take_request_coords(uid)
                results = maps_search_results(
                    parsed, uid, consume_lookup, coords)
            except Exception as e:
                logger.warning(f"Maps search failed: {e}")
                results = None
            if results:
                if garble:
                    results = og_garble.annotate(
                        results, garble, prepend=True)
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_maps_installed = True

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
guessing; there is no visitor geolocation in this app, on purpose.

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
        r"|\bwhere can i get gas\b"),
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
    r"\b(where('s| is| are)|find|show me|search|look(ing)? for|locate"
    r"|get me|give me|any|some|nearest|closest|nearby)\b", re.IGNORECASE)

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
    if "how much" in low:
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


def parse_maps_intent(message):
    """Parse a maps job out of a chat message, or None. Route shapes
    win over category shapes ('how far is the coffee shop from…' is a
    route question)."""
    if not message:
        return None
    low = " ".join(str(message).lower().split())
    return _parse_route(low, message) or _parse_places(low, message)


def message_needs_maps(message):
    return parse_maps_intent(message) is not None


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
            "did NOT name a city or area, and you can't see their "
            "live location. Do NOT invent a place or any results. "
            "Ask them ONE short question in your own voice: what "
            "city or neighborhood should you look in?)")
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


def _route_results(parsed):
    origin_q, dest_q = parsed["origin"], parsed["destination"]
    if not dest_q:
        return _clarify_result(
            "(Maps note for OG: the visitor wants route/directions "
            "info but didn't say WHERE they're headed. Do NOT invent "
            "a destination. Ask them ONE short question in your own "
            "voice: where are they trying to get to?)")
    if not origin_q:
        return _clarify_result(
            f"(Maps note for OG: the visitor wants a route to "
            f"{dest_q} but didn't say where they're starting from, "
            "and you can't see their live location. Do NOT invent a "
            "starting point or a distance. Ask them ONE short "
            "question in your own voice: where are they starting "
            "from?)")
    a, b = geocode(origin_q), geocode(dest_q)
    if a is None or b is None:
        return None  # couldn't pin an endpoint — fall through
    route = route_between(a, b)
    if route is None:
        return None  # OSRM down — fall through
    a_label = a["label"] or origin_q
    b_label = b["label"] or dest_q
    link = _dir_link(a_label, b_label)
    body = (
        f"Real driving route (OSRM, live): {a_label} to {b_label} "
        f"is {_fmt_dist(route['dist_m'])}, {_fmt_duration(route['secs'])} "
        f"by car. Directions: {link}. State it plainly in your own "
        f"voice — do NOT invent traffic conditions, road names or "
        f"alternate routes.")
    return [
        {"title": f"Route: {a_label} → {b_label} (OSRM, live)",
         "body": body[:1800], "href": link},
        {"title": "Route — source", "body": link, "href": link},
    ]


def maps_search_results(parsed, uid, consume_lookup):
    """Run one parsed maps job. Returns web_search-shaped results on
    a hit (or the clarifying-question note when a detail is missing),
    None on a miss (unknown place / upstream down) so the caller
    falls through to the previous search. One unit of the shared
    lookup budget is consumed per REAL answer — never for a
    clarifying ask, never for a miss."""
    if not parsed:
        return None
    if parsed["kind"] == "places":
        if not parsed.get("location"):
            return _places_results(parsed)  # clarifying ask, no spend
        results = _places_results(parsed)
    elif parsed["kind"] == "route":
        if not parsed.get("origin") or not parsed.get("destination"):
            return _route_results(parsed)   # clarifying ask, no spend
        results = _route_results(parsed)
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
# The seam (app.py installs this on the agent, after Rounds 3/4/6)
# ---------------------------------------------------------------------------

# The maps job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
_pending = {"parsed": None}


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
        try:
            parsed = parse_maps_intent(str(message))
            if parsed:
                _pending["parsed"] = parsed
                if isinstance(intent, dict) \
                        and not intent.get("needs_web_search"):
                    intent["needs_web_search"] = True
                    intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Maps trigger check failed: {e}")
        return intent

    def search_wrapped(query, num_results=5):
        parsed = _pending.get("parsed")
        _pending["parsed"] = None
        if parsed:
            try:
                results = maps_search_results(
                    parsed, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Maps search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_maps_installed = True

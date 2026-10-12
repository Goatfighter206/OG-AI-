"""GPS for OG (Round 66): driving navigation + job-site GPS.

WHAT THIS IS. Extends og_maps (which already geocodes via
Nominatim and routes distance/duration via OSRM, but has no
turn steps and no saved places) with the pieces a phone in the
menu needs:

  SITE POINTS — durable per-uid named points (the og_reactions /
  og_language store form: Postgres table og_gps_data when
  OG_MEMORY_DB_URL is set, else a JSON file, OG_GPS_STORE):
    GET  /gps/points          -> {"points": [point, ...]}
    POST /gps/points          {name, lat, lon, note?}
    POST /gps/points/delete   {id}
    POST /gps/points/update   {id, name?, note?}
    GET  /gps/points/export   -> CSV download (name,lat,lon,note)
    POST /gps/points/import   {csv} -> {ok, added, skipped}:
         the export format read back in (rows add_point refuses
         count as skipped; no dedupe — the store's own rules)

  MATH — pure stdlib, the SINGLE SOURCE every client (web +
  app) shares, so a stakeout number on the phone is the number
  the server would give:
    haversine_m / initial_bearing_deg between two coords
    latlon_to_utm / utm_to_latlon (WGS84, USGS series formulas)
    latlon_to_state_plane_wa: NAD83 Washington zones, Lambert
    conformal conic (EPSG:32148 North / 32149 South parameters,
    computed in metres, reported in US survey feet). Coordinates
    outside Washington get state_plane=None plus an honest note
    — never a wrong number from the wrong zone.
    GET  /gps/convert?lat=&lon=
    POST /gps/between  {a:{lat,lon}, b:{lat,lon}}
    POST /gps/stakeout {target_id|{lat,lon}, at:{lat,lon}}

  DRIVING — POST /gps/route {from, to} where each end is
  {lat, lon} or {place} (geocoded via og_maps). Steps come from
  OSRM with steps=true (og_maps' route_between asks for
  overview=false and returns no steps, so this module makes its
  own OSRM call through og_maps' HTTP helper + OG_OSRM_URL).
  OSRM unreachable / no route -> 502 with an honest detail;
  NO route is ever invented.

  ACTIVE ROUTE (Round 68) — the last route computed for an
  account is also stored as its ACTIVE route, in this same
  store under "gpsroute:"+uid: the /gps/route answer plus its
  resolved ends and a stored-at stamp:
    {distance_m, duration_s, steps,
     from: {lat, lon, label}, to: {lat, lon, label}, created}
    GET  /gps/route/active -> {"ok", "route": <stored|null>}
  POST /gps/route stores it (signed-in callers) and carries it
  on the answer additively as "route"; og_maps' chat seam
  stores it too when a directions ask is answered, so telling
  OG a place loads the same route object the map reads. There
  is exactly ONE active-route shape and ONE store for it.

  LOCATION SWITCH (Round 73) — ONE per-account toggle in
  this same store under "gpsloc:"+uid as {"on": bool}:
    GET  /gps/location        -> {"ok": true, "on": bool}
    POST /gps/location {on}   -> {"ok": true, "on": bool}
  Brent's rule: once on it STAYS on — across screens and
  restarts — until the owner taps it off again or asks OG
  in chat; OG never turns it on by himself; default OFF.
  Both faces (the web Driving view + the Unity app) read
  and flip THIS switch and nothing else, and og_maps' chat
  seam flips it for "turn my location on/off" asks — one
  switch, one source, the faces can never disagree. The
  switch is OG's permission to use location; the device's
  own OS permission prompt stays the phone/browser's gate
  underneath it.

  ONE-TIME PERMISSION ASK (Round 78) — Brent's rule: "Only
  ask one time then it stay on until they turn it off."
  When a chat directions ask reaches og_maps with no
  starting point, the switch OFF and the account never
  asked, OG asks permission ONE TIME to use their location
  to navigate them where they want to go; a yes flips THIS
  switch on (same set_location_on — no parallel store) and
  it stays on until they turn it off. The asked state and
  the pending ask live in this same store under
  "gpslocask:"+uid as {"asked": bool, "pending": {...}}:
  the flag is set the moment the ask is made (yes, no or
  ignored, OG never initiates the ask again) and ALSO when
  the owner turns the switch on themselves (set_location_on
  marks it — they already decided); the pending ask
  (destination + the ask text + a timestamp, ~10-minute
  expiry) is what a later yes/no in chat answers. Account
  purge clears the record with the rest of the GPS data.

GATE. None of these paths is in og_accounts.PUBLIC_PATHS, so
the Round 44 sign-in gate covers them by construction. A
request with no uid cookie reads/writes nothing.
"""

import csv
import io
import json
import logging
import math
import os
import threading
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_GPS_STORE", "gps_store.json")
MAX_POINTS = 500
MAX_ROUTE_STEPS = 200
MAX_CSV_CHARS = 2000000
NAME_CAP = 120
NOTE_CAP = 500

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# ===========================================================================
# Durable store (og_reactions pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_gps_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_file_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"GPS store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"GPS store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_gps_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"GPS DB load failed: {e}")
            raise StoreError("gps store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_gps_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GPS DB save failed: {e}")
            raise StoreError("gps store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's points, its
    stored active route, its location switch and its
    one-time-ask record (Round 78)."""
    if uid:
        _put("gps:" + uid, {"points": []})
        _put("gpsroute:" + uid, None)
        _put("gpsloc:" + uid, None)
        _put("gpslocask:" + uid, None)


# ===========================================================================
# Validation + points CRUD
# ===========================================================================


def valid_latlon(lat, lon):
    """(lat, lon) floats or None. Ranges only: -90..90 /
    -180..180, both numeric (bools are not numbers here)."""
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def _coords_of(raw):
    if not isinstance(raw, dict):
        return None
    return valid_latlon(raw.get("lat"), raw.get("lon"))


def _clean_point(raw):
    if not isinstance(raw, dict):
        return None
    try:
        lat = float(raw.get("lat"))
        lon = float(raw.get("lon"))
    except (TypeError, ValueError):
        return None
    return {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or ""),
        "lat": lat, "lon": lon,
        "note": str(raw.get("note") or ""),
        "created": float(raw.get("created") or 0),
    }


def points_for(uid: str) -> list:
    data = _get("gps:" + uid)
    if not isinstance(data, dict):
        return []
    out = []
    for raw in data.get("points") or []:
        pt = _clean_point(raw)
        if pt and pt["id"]:
            out.append(pt)
    return out


def add_point(uid: str, name, lat, lon, note=""):
    """Create a point for uid. Returns the point, or None when
    the input fails validation (name required, ranges)."""
    if not uid or not isinstance(name, str):
        return None
    name = name.strip()
    if not 1 <= len(name) <= NAME_CAP:
        return None
    coords = valid_latlon(lat, lon)
    if coords is None:
        return None
    if not isinstance(note, str):
        note = str(note or "")
    pt = {"id": uuid.uuid4().hex[:12], "name": name,
          "lat": coords[0], "lon": coords[1],
          "note": note[:NOTE_CAP], "created": time.time()}
    with _store_lock:
        pts = points_for(uid)
        pts.append(pt)
        _put("gps:" + uid, {"points": pts[-MAX_POINTS:]})
    return pt


def delete_point(uid: str, point_id) -> bool:
    if not uid or not isinstance(point_id, str) or not point_id:
        return False
    with _store_lock:
        pts = points_for(uid)
        kept = [p for p in pts if p["id"] != point_id]
        if len(kept) == len(pts):
            return False
        _put("gps:" + uid, {"points": kept})
    return True


def update_point(uid: str, point_id, name=None, note=None):
    """Rename / re-note one of the caller's points. Returns the
    updated point, None when not found, False on bad input."""
    if not uid or not isinstance(point_id, str) or not point_id:
        return False
    if name is not None:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_CAP:
            return False
        name = name.strip()
    if note is not None:
        if not isinstance(note, str):
            return False
        note = note[:NOTE_CAP]
    with _store_lock:
        pts = points_for(uid)
        for pt in pts:
            if pt["id"] == point_id:
                if name is not None:
                    pt["name"] = name
                if note is not None:
                    pt["note"] = note
                _put("gps:" + uid, {"points": pts})
                return pt
    return None


def points_csv(uid: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "lat", "lon", "note"])
    for pt in points_for(uid):
        w.writerow([pt["name"], pt["lat"], pt["lon"], pt["note"]])
    return buf.getvalue()


def import_points_csv(uid: str, csv_text):
    """Import points from CSV text in points_csv()'s format
    (header name,lat,lon,note). Returns {"added", "skipped"},
    or None when the input is not usable CSV text at all (no
    uid, not a string, oversized, unparsable). Every data row
    goes through add_point, so validation, the name/note caps
    and the per-uid store stay the single source; a row
    add_point refuses (bad name / coords out of range) — and
    any row whose column count is not the export's 3-or-4 —
    counts as skipped. Blank lines are ignored, not counted.
    A first row matching the export header is the header, not
    a point; a file with no header imports from row one.
    Nothing is deduplicated: importing the same file twice
    adds the points twice, exactly like adding them by hand."""
    if not uid or not isinstance(csv_text, str):
        return None
    if len(csv_text) > MAX_CSV_CHARS:
        return None
    try:
        rows = list(csv.reader(io.StringIO(csv_text)))
    except Exception as e:
        logger.warning(f"GPS CSV import parse failed: {e}")
        return None
    added = 0
    skipped = 0
    first = True
    for row in rows:
        if not row or all(not (c or "").strip() for c in row):
            continue
        if first:
            first = False
            if [c.strip().lower() for c in row] == \
                    ["name", "lat", "lon", "note"]:
                continue
        if len(row) not in (3, 4):
            skipped += 1
            continue
        note = row[3] if len(row) == 4 else ""
        pt = add_point(uid, row[0], row[1], row[2], note)
        if pt is None:
            skipped += 1
        else:
            added += 1
    return {"added": added, "skipped": skipped}


# ===========================================================================
# Math — haversine + bearings (spherical, R = mean earth radius)
# ===========================================================================

EARTH_R_M = 6371000.0
M_TO_FT = 3.280839895013123


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Initial great-circle bearing from point 1 to point 2,
    degrees clockwise from true north, 0..360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = (math.cos(p1) * math.sin(p2)
         - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def between(a_lat, a_lon, b_lat, b_lon) -> dict:
    d = haversine_m(a_lat, a_lon, b_lat, b_lon)
    brg = initial_bearing_deg(a_lat, a_lon, b_lat, b_lon)
    return {"distance_m": round(d, 3),
            "distance_ft": round(d * M_TO_FT, 2),
            "bearing_deg": round(brg, 2),
            "bearing_back_deg": round((brg + 180.0) % 360.0, 2)}


# ===========================================================================
# Math — UTM (WGS84 ellipsoid, standard USGS/Snyder series)
# ===========================================================================

_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_UTM_K0 = 0.9996


def utm_zone_for(lon) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    # Norway / Svalbard exceptions (standard UTM zone rules).
    return min(60, max(1, zone))


def latlon_to_utm(lat, lon) -> dict:
    """lat/lon -> {"zone", "hemisphere", "easting", "northing"}
    in metres. Raises ValueError outside UTM's latitude band
    (-80..84)."""
    if not (-80.0 <= lat <= 84.0):
        raise ValueError("latitude outside UTM band (-80..84)")
    a, f = _WGS84_A, _WGS84_F
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    zone = utm_zone_for(lon)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = math.radians(lat)
    lam = math.radians(lon)
    sinp, cosp, tanp = math.sin(phi), math.cos(phi), math.tan(phi)
    n_ = a / math.sqrt(1 - e2 * sinp ** 2)
    t = tanp ** 2
    c = ep2 * cosp ** 2
    a_ = (lam - lon0) * cosp
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024)
             * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024)
             * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    easting = _UTM_K0 * n_ * (
        a_ + (1 - t + c) * a_ ** 3 / 6
        + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * a_ ** 5 / 120
    ) + 500000.0
    northing = _UTM_K0 * (
        m + n_ * tanp * (
            a_ ** 2 / 2
            + (5 - t + 9 * c + 4 * c ** 2) * a_ ** 4 / 24
            + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2)
            * a_ ** 6 / 720))
    hemisphere = "N" if lat >= 0 else "S"
    if lat < 0:
        northing += 10000000.0
    return {"zone": zone, "hemisphere": hemisphere,
            "easting": round(easting, 3), "northing": round(northing, 3)}


def utm_to_latlon(zone, hemisphere, easting, northing):
    """UTM metres -> (lat, lon) degrees. The inverse series."""
    a, f = _WGS84_A, _WGS84_F
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = easting - 500000.0
    y = northing
    if str(hemisphere).upper().startswith("S"):
        y -= 10000000.0
    m = y / _UTM_K0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    j1, j2, j3, j4 = (3 * e1 / 2 - 27 * e1 ** 3 / 32,
                      21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32,
                      151 * e1 ** 3 / 96, 1097 * e1 ** 4 / 512)
    fp = (mu + j1 * math.sin(2 * mu) + j2 * math.sin(4 * mu)
          + j3 * math.sin(6 * mu) + j4 * math.sin(8 * mu))
    sinf, cosf, tanf = math.sin(fp), math.cos(fp), math.tan(fp)
    c1 = ep2 * cosf ** 2
    t1 = tanf ** 2
    n1 = a / math.sqrt(1 - e2 * sinf ** 2)
    r1 = a * (1 - e2) / (1 - e2 * sinf ** 2) ** 1.5
    d = x / (n1 * _UTM_K0)
    lat = fp - (n1 * tanf / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2
           - 3 * c1 ** 2) * d ** 6 / 720)
    lon0 = math.radians((int(zone) - 1) * 6 - 180 + 3)
    lon = lon0 + (
        d - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 8 * ep2
           + 24 * t1 ** 2 + 4 * c1) * d ** 5 / 120) / cosf
    return (math.degrees(lat), math.degrees(lon))


# ===========================================================================
# Math — Washington State Plane (NAD83, Lambert conformal conic)
# ===========================================================================

_GRS80_A = 6378137.0
_GRS80_F = 1 / 298.257222101
_M_TO_USFT = 3937.0 / 1200.0     # US survey foot, exact definition
_WA_BBOX = (45.54, 49.05, -124.8, -116.9)  # EPSG WA area of use

_WA_ZONES = {
    "WA North": {  # EPSG:32148
        "epsg": 32148, "lat1": 47.5, "lat2": 48.73333333333333,
        "lat0": 47.0, "lon0": -120.83333333333333,
        "fe_m": 500000.0, "fn_m": 0.0},
    "WA South": {  # EPSG:32149
        "epsg": 32149, "lat1": 45.83333333333333, "lat2": 47.33333333333333,
        "lat0": 45.33333333333333, "lon0": -120.5,
        "fe_m": 500000.0, "fn_m": 0.0},
}
SP_NOTE_OUTSIDE_WA = ("state plane shown for Washington zones only — "
                      "this point is outside Washington")


def _lcc_params(spec):
    a, f = _GRS80_A, _GRS80_F
    e2 = f * (2 - f)
    e = math.sqrt(e2)

    def m_of(phi):
        s = math.sin(phi)
        return math.cos(phi) / math.sqrt(1 - e2 * s * s)

    def t_of(phi):
        s = math.sin(phi)
        return (math.tan(math.pi / 4 - phi / 2)
                / (((1 - e * s) / (1 + e * s)) ** (e / 2)))
    p1, p2 = math.radians(spec["lat1"]), math.radians(spec["lat2"])
    p0 = math.radians(spec["lat0"])
    n = ((math.log(m_of(p1)) - math.log(m_of(p2)))
         / (math.log(t_of(p1)) - math.log(t_of(p2))))
    big_f = m_of(p1) / (n * t_of(p1) ** n)
    rho0 = a * big_f * t_of(p0) ** n
    return n, big_f, rho0, t_of


def state_plane_in_zone(lat, lon, name):
    """WA state plane in an explicitly named zone (no area
    check) — the false-origin definitions are pinned against
    this in the test suite."""
    spec = _WA_ZONES[name]
    n, big_f, rho0, t_of = _lcc_params(spec)
    phi = math.radians(lat)
    rho = _GRS80_A * big_f * t_of(phi) ** n
    theta = n * (math.radians(lon) - math.radians(spec["lon0"]))
    e_m = spec["fe_m"] + rho * math.sin(theta)
    n_m = spec["fn_m"] + rho0 - rho * math.cos(theta)
    return {"zone": name, "epsg": spec["epsg"],
            "easting_ft": round(e_m * _M_TO_USFT, 2),
            "northing_ft": round(n_m * _M_TO_USFT, 2),
            "easting_m": round(e_m, 3), "northing_m": round(n_m, 3)}


def latlon_to_state_plane_wa(lat, lon):
    """WA state plane for a lat/lon, or None outside Washington.
    Zone rule (JUDGMENT CALL): the statutory split is by COUNTY
    (RCW 58.20.130 — e.g. Pierce/Thurston are South, King/Kitsap
    North, Grant splits at 47°30'N); with no county polygons on
    board, the latitude parallel 47.5 is used as the split,
    which agrees with the county rule for the Puget Sound job
    sites this serves (Seattle North; Tacoma/Puyallup/Olympia
    South) and can differ near county edges away from that
    parallel. Easting/northing are the metric EPSG grid
    converted to US survey feet (the endpoint contract)."""
    lat_min, lat_max, lon_min, lon_max = _WA_BBOX
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        return None
    name = "WA North" if lat >= 47.5 else "WA South"
    return state_plane_in_zone(lat, lon, name)


def convert(lat, lon) -> dict:
    """The /gps/convert payload for one coordinate."""
    out = {"lat": lat, "lon": lon}
    try:
        out["utm"] = latlon_to_utm(lat, lon)
    except ValueError:
        out["utm"] = None
    sp = latlon_to_state_plane_wa(lat, lon)
    out["state_plane"] = sp
    out["note"] = "" if sp else SP_NOTE_OUTSIDE_WA
    return out


# ===========================================================================
# Driving route (OSRM with steps — the piece og_maps lacks)
# ===========================================================================

def _osrm_json(url, params):
    """One OSRM GET through og_maps' HTTP helper (same UA,
    timeouts, OG_OSRM_URL seam). Tests patch THIS function."""
    import og_maps
    return og_maps._get_json(url, params=params,
                             timeout=og_maps.OSRM_TIMEOUT)


def geocode_place(place):
    """Place name -> og_maps.geocode result or None. Tests patch
    og_maps.geocode; this wrapper always calls through it."""
    import og_maps
    return og_maps.geocode(place)


def _resolve_endpoint_labeled(raw, label_hint=""):
    """A route endpoint -> (lat, lon, label) or None: coords
    direct (the label is the caller's hint — e.g. "your
    location" for a shared-position start), or {place}
    geocoded (the label is the geocoder's own label, the
    place words as fallback)."""
    if not isinstance(raw, dict):
        return None
    coords = _coords_of(raw)
    if coords is not None:
        return (coords[0], coords[1], str(label_hint or ""))
    place = raw.get("place")
    if isinstance(place, str) and place.strip():
        hit = geocode_place(place.strip())
        if hit:
            coords = valid_latlon(hit.get("lat"), hit.get("lon"))
            if coords is not None:
                label = str(hit.get("label") or "").strip() \
                    or place.strip()
                return (coords[0], coords[1], label)
    return None


def _resolve_endpoint(raw):
    """A route endpoint: {lat, lon} direct, or {place} geocoded.
    Returns (lat, lon) or None."""
    labeled = _resolve_endpoint_labeled(raw)
    if labeled is None:
        return None
    return (labeled[0], labeled[1])


_MANEUVER_TEXT = {
    "depart": "Head out",
    "turn": "Turn",
    "new name": "Continue",
    "continue": "Continue",
    "merge": "Merge",
    "on ramp": "Take the ramp",
    "off ramp": "Take the exit",
    "fork": "At the fork, keep",
    "end of road": "At the end of the road, turn",
    "roundabout": "At the roundabout, take exit",
    "rotary": "At the rotary, take exit",
    "exit roundabout": "Exit the roundabout",
    "arrive": "Arrive",
    "notification": "Continue",
}


def _step_instruction(step) -> str:
    man = step.get("maneuver") or {}
    mtype = (man.get("type") or "").strip()
    modifier = (man.get("modifier") or "").strip()
    name = (step.get("name") or "").strip()
    base = _MANEUVER_TEXT.get(mtype, "Continue")
    parts = [base]
    if mtype == "roundabout" or mtype == "rotary":
        idx = man.get("exit")
        if idx:
            parts = [f"At the roundabout, take exit {idx}"]
    elif modifier and mtype not in ("depart", "arrive", "continue",
                                    "new name", "notification"):
        parts.append(modifier)
    text = " ".join(p for p in parts if p)
    if name and name != "-":
        if mtype == "depart":
            text = f"Head out on {name}"
        elif mtype == "arrive":
            text = f"Arrive at {name}"
        else:
            text = f"{text} onto {name}" if mtype in (
                "turn", "end of road", "fork", "off ramp",
                "on ramp", "merge") else f"{text} on {name}"
    if mtype == "arrive" and (not name or name == "-"):
        text = "Arrive at your destination"
    return text


def route_driving(from_raw, to_raw):
    """{distance_m, duration_s, steps:[...]} or None when either
    endpoint cannot be resolved, OSRM is unreachable, or OSRM
    reports no route. NOTHING is invented on failure — the
    route handler turns None into an honest 502."""
    a = _resolve_endpoint(from_raw)
    b = _resolve_endpoint(to_raw)
    if a is None or b is None:
        return None
    import og_maps
    base = og_maps.OSRM_URL
    url = (f"{base}{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}")
    data = _osrm_json(url, {"overview": "false", "steps": "true"})
    if not data or data.get("code") != "Ok" or not data.get("routes"):
        return None
    try:
        r0 = data["routes"][0]
        steps = []
        for leg in r0.get("legs") or []:
            for step in leg.get("steps") or []:
                steps.append({
                    "instruction": _step_instruction(step),
                    "distance_m": round(float(step.get("distance") or 0), 1),
                    "name": (step.get("name") or "").strip(),
                })
        return {"distance_m": round(float(r0["distance"]), 1),
                "duration_s": round(float(r0["duration"]), 1),
                "steps": steps}
    except Exception as e:
        logger.warning(f"GPS OSRM parse failed: {e}")
        return None


# ===========================================================================
# Active route (Round 68) — one stored route per account
# ===========================================================================


def _clean_route(raw):
    """A stored active route -> the clean dict or None.
    Mirrors _clean_point: totals must be non-negative
    numbers, every step keeps the /gps/route step shape,
    both ends must carry in-range coords. Anything else
    reads as NO route — a corrupt blob can never surface
    as a route on a map."""
    if not isinstance(raw, dict):
        return None
    try:
        distance_m = float(raw.get("distance_m"))
        duration_s = float(raw.get("duration_s"))
        created = float(raw.get("created") or 0)
    except (TypeError, ValueError):
        return None
    if distance_m < 0 or duration_s < 0:
        return None
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list):
        return None
    steps = []
    for s in steps_raw[:MAX_ROUTE_STEPS]:
        if not isinstance(s, dict):
            return None
        try:
            sd = float(s.get("distance_m") or 0)
        except (TypeError, ValueError):
            return None
        steps.append({
            "instruction": str(s.get("instruction") or "")[:NOTE_CAP],
            "distance_m": round(sd, 1),
            "name": str(s.get("name") or "")[:NAME_CAP],
        })
    ends = {}
    for key in ("from", "to"):
        end = raw.get(key)
        coords = _coords_of(end) if isinstance(end, dict) else None
        if coords is None:
            return None
        ends[key] = {"lat": coords[0], "lon": coords[1],
                     "label": str(end.get("label") or "")[:NAME_CAP]}
    return {"distance_m": round(distance_m, 1),
            "duration_s": round(duration_s, 1),
            "steps": steps,
            "from": ends["from"], "to": ends["to"],
            "created": created}


def active_route_for(uid: str):
    """The account's stored active route — the last route
    /gps/route computed for it or a chat directions ask
    loaded — or None."""
    if not uid:
        return None
    return _clean_route(_get("gpsroute:" + uid))


def store_route(uid, route, from_raw, to_raw,
                from_label="", to_label=""):
    """Store a computed route (route_driving's answer) as
    the account's active route, its ends resolved to
    coords + labels so a map can plot them. Returns the
    stored route, or None when there is no uid, the route
    is not a route, or either end cannot be resolved —
    nothing partial is ever stored."""
    if not uid or not isinstance(route, dict):
        return None
    a = _resolve_endpoint_labeled(from_raw, from_label)
    b = _resolve_endpoint_labeled(to_raw, to_label)
    if a is None or b is None:
        return None
    stored = _clean_route({
        "distance_m": route.get("distance_m"),
        "duration_s": route.get("duration_s"),
        "steps": route.get("steps"),
        "from": {"lat": a[0], "lon": a[1], "label": a[2]},
        "to": {"lat": b[0], "lon": b[1], "label": b[2]},
        "created": time.time(),
    })
    if stored is None:
        return None
    _put("gpsroute:" + uid, stored)
    return stored


# ===========================================================================
# Location switch (Round 73) — one per-account toggle
# ===========================================================================


def location_on_for(uid: str) -> bool:
    """The account's location switch: True only when the
    stored record says on. Anything else — no record yet,
    a corrupt blob, no uid — reads OFF (the default)."""
    if not uid:
        return False
    data = _get("gpsloc:" + uid)
    return isinstance(data, dict) and data.get("on") is True


def set_location_on(uid: str, on) -> bool:
    """Flip the account's location switch; returns the
    state now stored. No uid stores nothing and reads
    back OFF. Nothing anywhere calls this on its own —
    the only callers are the owner's own tap (POST
    /gps/location), the owner's own chat ask (og_maps'
    location-toggle seam) and the owner's YES to the
    one-time permission ask (Round 78).
    Turning ON also marks the one-time ask as asked
    (Round 78): the owner has made the location decision
    themselves, so OG never needs to ask them later —
    even after they turn it back off."""
    if uid:
        _put("gpsloc:" + uid, {"on": bool(on)})
        if on:
            _mark_location_asked(uid)
    return location_on_for(uid)


# ===========================================================================
# One-time location permission ask (Round 78)
# ===========================================================================
# Brent's rule: "Only ask one time then it stay on until
# they turn it off." One record per account beside the
# switch: "gpslocask:"+uid -> {"asked": bool, "pending":
# {"destination", "ask", "ts"} | None}. The asked flag is
# set the moment the ask is made (and by any manual turn-on,
# via set_location_on above); the pending ask is what a
# later yes/no in chat answers, and it expires — a yes ten
# minutes later is just a yes to nothing.

LOC_ASK_TTL_S = 600.0


def _loc_ask_record(uid: str) -> dict:
    if not uid:
        return {}
    data = _get("gpslocask:" + uid)
    return data if isinstance(data, dict) else {}


def location_asked_for(uid: str) -> bool:
    """True once the account has been asked the one-time
    location permission question — or has made the
    decision itself by turning the switch on. No record,
    a corrupt blob or no uid all read NOT asked."""
    return _loc_ask_record(uid).get("asked") is True


def _mark_location_asked(uid: str):
    """Record that the location decision has been faced:
    the asked flag set, any pending ask cleared. Called
    when the ask is made (via set_location_pending_ask)
    and by set_location_on when the switch turns on."""
    _put("gpslocask:" + uid, {"asked": True, "pending": None})


def set_location_pending_ask(uid: str, destination, ask_text) -> bool:
    """Store the pending one-time ask for the account and
    set the asked flag in the same write — the flag is set
    the moment the ask is made, whatever the answer turns
    out to be. Returns False (stores nothing) with no uid
    or no destination."""
    if not uid or not destination:
        return False
    _put("gpslocask:" + uid, {
        "asked": True,
        "pending": {
            "destination": str(destination),
            "ask": str(ask_text or ""),
            "ts": time.time(),
        },
    })
    return True


def location_pending_ask(uid: str):
    """The account's pending one-time ask, or None — no
    record, already answered, or past its expiry. An
    expired pending is cleared as it is read, so it can
    never be answered late."""
    rec = _loc_ask_record(uid)
    pending = rec.get("pending")
    if not isinstance(pending, dict):
        return None
    try:
        ts = float(pending.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts <= 0 or (time.time() - ts) > LOC_ASK_TTL_S:
        _put("gpslocask:" + uid,
             {"asked": rec.get("asked") is True, "pending": None})
        return None
    return pending


def clear_location_pending_ask(uid: str):
    """Drop the pending ask (it has been answered); the
    asked flag is untouched — it stays set for good."""
    if uid:
        rec = _loc_ask_record(uid)
        _put("gpslocask:" + uid,
             {"asked": rec.get("asked") is True, "pending": None})


# ===========================================================================
# Routes
# ===========================================================================


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _uid_of(request: Request) -> str:
    return request.cookies.get(UID_COOKIE) or ""


def _bad(detail):
    return JSONResponse({"ok": False, "detail": detail}, status_code=400)


def register_gps_routes(app):

    @app.get("/gps/points")
    async def gps_points_list(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"points": []}
        return {"points": points_for(uid)}

    @app.post("/gps/points")
    async def gps_points_add(request: Request):
        uid = _uid_of(request)
        if not uid:
            return _bad("no visitor id")
        body = await _json_body(request)
        pt = add_point(uid, body.get("name"), body.get("lat"),
                       body.get("lon"), body.get("note") or "")
        if pt is None:
            return _bad("name required and lat/lon in range")
        return {"ok": True, "point": pt}

    @app.post("/gps/points/delete")
    async def gps_points_delete(request: Request):
        uid = _uid_of(request)
        if not uid:
            return _bad("no visitor id")
        body = await _json_body(request)
        if not delete_point(uid, body.get("id")):
            return JSONResponse({"ok": False, "detail": "point not found"},
                                status_code=404)
        return {"ok": True}

    @app.post("/gps/points/update")
    async def gps_points_update(request: Request):
        uid = _uid_of(request)
        if not uid:
            return _bad("no visitor id")
        body = await _json_body(request)
        pt = update_point(uid, body.get("id"),
                          name=body.get("name") if "name" in body else None,
                          note=body.get("note") if "note" in body else None)
        if pt is False:
            return _bad("bad name/note")
        if pt is None:
            return JSONResponse({"ok": False, "detail": "point not found"},
                                status_code=404)
        return {"ok": True, "point": pt}

    @app.get("/gps/points/export")
    async def gps_points_export(request: Request):
        uid = _uid_of(request)
        return PlainTextResponse(
            points_csv(uid) if uid else "name,lat,lon,note\n",
            media_type="text/csv",
            headers={"Content-Disposition":
                     "attachment; filename=og-gps-points.csv"})

    @app.post("/gps/points/import")
    async def gps_points_import(request: Request):
        uid = _uid_of(request)
        if not uid:
            return _bad("no visitor id")
        body = await _json_body(request)
        result = import_points_csv(uid, body.get("csv"))
        if result is None:
            detail = ("csv must be CSV text in the export format "
                      "(name,lat,lon,note)")
            return JSONResponse({"ok": False, "detail": detail,
                                 "error": detail},
                                status_code=400)
        return {"ok": True, "added": result["added"],
                "skipped": result["skipped"]}

    @app.get("/gps/convert")
    async def gps_convert(request: Request):
        coords = valid_latlon(request.query_params.get("lat"),
                              request.query_params.get("lon"))
        if coords is None:
            return _bad("lat/lon required and in range")
        return convert(coords[0], coords[1])

    @app.post("/gps/between")
    async def gps_between(request: Request):
        body = await _json_body(request)
        a = _coords_of(body.get("a"))
        b = _coords_of(body.get("b"))
        if a is None or b is None:
            return _bad("a and b need lat/lon in range")
        return between(a[0], a[1], b[0], b[1])

    @app.post("/gps/stakeout")
    async def gps_stakeout(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        at = _coords_of(body.get("at"))
        if at is None:
            return _bad("at needs lat/lon in range")
        target = _coords_of(body.get("target")) \
            if isinstance(body.get("target"), dict) else None
        target_name = ""
        if target is None and body.get("target_id"):
            tid = body.get("target_id")
            for pt in points_for(uid) if uid else []:
                if pt["id"] == tid:
                    target = (pt["lat"], pt["lon"])
                    target_name = pt["name"]
                    break
            if target is None:
                return JSONResponse(
                    {"ok": False, "detail": "target point not found"},
                    status_code=404)
        elif target is None and isinstance(body.get("target"), dict):
            return _bad("target needs lat/lon in range")
        if target is None:
            return _bad("target_id or target {lat, lon} required")
        out = between(at[0], at[1], target[0], target[1])
        out.pop("bearing_back_deg", None)
        out["target"] = {"lat": target[0], "lon": target[1],
                         "name": target_name}
        return out

    @app.post("/gps/route")
    async def gps_route(request: Request):
        body = await _json_body(request)
        # Validate shape first so a bad endpoint is a 400, not a
        # 502: an endpoint is resolvable-shaped when it carries
        # coords or a place string.
        for key in ("from", "to"):
            raw = body.get(key)
            if not isinstance(raw, dict) or (
                    _coords_of(raw) is None
                    and not (isinstance(raw.get("place"), str)
                             and raw.get("place").strip())):
                return _bad(f"{key} needs {{lat, lon}} or {{place}}")
        route = route_driving(body.get("from"), body.get("to"))
        if route is None:
            return JSONResponse(
                {"ok": False,
                 "detail": "route unavailable — the routing service "
                           "could not be reached or found no route; "
                           "nothing was made up"},
                status_code=502)
        out = dict(route)
        uid = _uid_of(request)
        if uid:
            # Round 68: the computed route also becomes the
            # account's ACTIVE route — the one the GPS map
            # loads — and rides the answer additively as
            # "route". An unresolvable end (impossible here:
            # the route just computed from these ends) simply
            # stores nothing; the answer stands either way.
            stored = store_route(uid, route, body.get("from"),
                                 body.get("to"))
            if stored is not None:
                out["route"] = stored
        return out

    @app.get("/gps/route/active")
    async def gps_route_active(request: Request):
        uid = _uid_of(request)
        return {"ok": True,
                "route": active_route_for(uid) if uid else None}

    @app.get("/gps/location")
    async def gps_location_get(request: Request):
        uid = _uid_of(request)
        if not uid:
            # Signed-in only (the pinned contract). In
            # production the Round 44 gate answers first
            # with this same body; this covers any mount
            # without the gate.
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        return {"ok": True, "on": location_on_for(uid)}

    @app.post("/gps/location")
    async def gps_location_set(request: Request):
        uid = _uid_of(request)
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        if not isinstance(body.get("on"), bool):
            return _bad("on must be true or false")
        return {"ok": True, "on": set_location_on(uid, body["on"])}

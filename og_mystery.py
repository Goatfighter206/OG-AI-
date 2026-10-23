"""Ticket MM — MURDER MYSTERY game mode: OG runs a
whodunit in chat, built on the question templates Brent
approved 2026-10-23 (og-murder-mystery-ticket-2026-10-23.md).

AUDIT (cand-murder-mystery/REPORT.md has the full
verdict). OG had story-shaped pieces, none of them a
game, none of them this ticket:

- og_books (Generator 7c) drafts a book outline the
  visitor approves, then writes chapters into the
  Library. A content generator, not a playable mode:
  no state machine, no hidden truth, no scoring.
- og_songs / og_podcast: the same generator shape.
- The Crypto Hub "games" are COMING SOON labels
  (Ticket M scope cut) — no engine behind them.
- og_userwatch (Ticket W, unlanded candidate) is the
  newest chat seam: install wrappers on detect_intent
  + web_search, deterministic claims, grounded
  directives the persona voices, one written record
  per user in the house blob store, an owner page,
  a kill switch. THIS MODULE follows that pattern
  exactly and builds only the missing game seam.

THE GAME, honestly built:

- ONE written record per game (No Junk Drawer): the
  setup answers, the committed truth, the suspects,
  the clue list with served flags, the phase, the
  score. Owner door: "my games" in chat + GET /games
  (resume / abandon). Records die with the account
  (purge_owner, the og_store_ready cascade), when
  abandoned, or when finished. A live game untouched
  for 7 days parks itself (status "parked", ONE Feed
  notice) and "resume my game" revives it — the
  standing 7-day park rule.
- TRUTH FIRST, STORED, NEVER LEAKED: when setup
  completes, the module DEALS the whole truth —
  killer, method, motive, finder + the finder's lie,
  the real clues vs the planted ones — into the
  record BEFORE the first scene is served. Every
  scene, statement, and clue the user ever sees is
  rendered from that record by this module, so the
  mystery cannot drift and is honestly solvable.
  Directives to the persona are built ONLY from
  public fields (roster, served clues, the current
  question); truth fields are rendered exactly once,
  in the reveal, after the accusation is locked.
- THE TEMPLATES ARE THE SKELETON: Template 1 (quick
  start, 5 questions) opens every game, ONE question
  at a time; "you pick" / "deal 'em" on any question
  and OG deals it. A long game (asked for at entry
  or during setup) folds in Template 2's 8. Picking
  "suspect" as the role runs Template 4's character
  creator (6 questions) and puts the user's character
  in the roster — secretly the killer if they ask.
  The play loop is Template 3: interrogate / believe
  or press / search the room, check the body, follow
  a lead / clue real or planted / #1 suspect / accuse
  or gather more — ending in "Lock it in. Who did
  it, how, and what was the motive?", collected as
  who → how → why and scored against the stored
  truth, element by element.
- DETERMINISTIC, NO LLM IN THE STATE MACHINE: the
  module parses each answer, advances the record,
  and hands the persona a grounded directive (the
  og_artifacts / og_userwatch seam shape). The
  persona voices it; it never invents a clue.
- MAIN THREAD RULE: a turn is one record read + one
  record write — a quick glance, nothing heavy.
- Kill switch: OG_MYSTERY_ENABLED — enabled unless
  the env value is one of 0/false/no/off (the house
  pattern). Disabled, the routes answer 404 and the
  chat seam claims nothing, so chat is untouched.

app.py carries only the import, the tool install,
and the register lines.

MM-PICS (games wave, 2026-10-23): pictures ride
og_mystery_pics — one crime scene + one portrait per
suspect at game start, one photo per clue at the
moment it is served, all on the existing image
machinery and tier caps. Prompts are built there
from public state only; this module passes it the
record and never a truth field as a prompt input.
"""

import html
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

import og_mystery_pics as _pics

logger = logging.getLogger(__name__)


# --- Kill switch ---------------------------------------------------------------


def mystery_enabled() -> bool:
    """OG_MYSTERY_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_MYSTERY_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Config (the approved No Junk Drawer numbers) -------------------------------

MAX_GAMES = 10               # per user — a games list is not a junk drawer
PARK_AFTER_S = 7 * 86400     # a stuck game parks itself after 7 idle days


# --- Store (house blob pattern, as in og_userwatch) -----------------------------
# One authoritative record set per user: table
# og_mystery_data (uid, kind) via og_db when
# OG_MEMORY_DB_URL is set, else the JSON file below.
# kind "games" -> {id: record}.

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, \
    apply_bounds as _apply_bounds, \
    normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

MYSTERY_STORE_FILE = os.getenv(
    "OG_MYSTERY_STORE", "mystery_store.json")
_store_lock = threading.RLock()


def _db_connect():
    conn = _pooled_connect(
        _normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_mystery_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(MYSTERY_STORE_FILE):
        try:
            with open(MYSTERY_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Mystery store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(MYSTERY_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Mystery store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_mystery_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Mystery DB load failed: {e}")
            return default
    with _store_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_mystery_data "
                        "(uid, kind, data) VALUES (%s, %s, %s) "
                        "ON CONFLICT (uid, kind) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Mystery DB save failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _delete_blobs(uid: str):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_mystery_data "
                        "WHERE uid=%s", (uid,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Mystery DB purge failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        store.pop(uid, None)
        _save_file_store(store)


# --- Records ---------------------------------------------------------------------


def _records(uid: str) -> Dict:
    got = _blob(uid, "games", {})
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict)}


def _save_records(uid: str, mine: Dict):
    _put_blob(uid, "games", mine)


def _get_record(uid: str, game_id: str) -> Optional[Dict]:
    if not uid or not game_id:
        return None
    rec = _records(uid).get(game_id)
    if rec is None or rec.get("owner") != uid:
        return None
    return rec


def _notify_parked(uid: str, rec: Dict):
    """ONE Feed notice when a game parks (the standing
    7-day rule). Kind "notice" — og_notify's plain
    mailbox kind; nothing new is registered there."""
    try:
        import og_notify as _notify
        _notify.record(
            uid, "notice", "Murder mystery parked",
            f"Your murder mystery ({rec.get('title', 'untitled')}) "
            "sat untouched for 7 days, so it's parked. Say "
            "\"resume my game\" in chat or open your games "
            "page to pick it back up.")
    except Exception as e:
        logger.warning(f"Mystery park notice failed: {e}")


def _revalidate(uid: str, mine: Dict, now: float) -> bool:
    """State re-validates on read (No Junk Drawer): a
    live game idle past the park line parks itself,
    with one notice. Returns True if anything changed."""
    changed = False
    for rec in mine.values():
        if rec.get("owner") != uid:
            continue
        if rec.get("status") == "live" and \
                now - float(rec.get("last_touched") or now) > PARK_AFTER_S:
            rec["status"] = "parked"
            changed = True
            if not rec.get("notice_sent"):
                rec["notice_sent"] = True
                _notify_parked(uid, rec)
    return changed


def list_games_for_owner(uid: str) -> List[Dict]:
    """The owner's own games, newest first, re-validated
    on read. The store is keyed by owner and every
    record re-checks its owner."""
    if not uid:
        return []
    mine = _records(uid)
    if _revalidate(uid, mine, time.time()):
        _save_records(uid, mine)
    out = [r for r in mine.values() if r.get("owner") == uid]
    out.sort(key=lambda r: float(r.get("created") or 0),
             reverse=True)
    return out


def live_game_for_owner(uid: str) -> Optional[Dict]:
    for rec in list_games_for_owner(uid):
        if rec.get("status") == "live":
            return rec
    return None


def parked_game_for_owner(uid: str) -> Optional[Dict]:
    for rec in list_games_for_owner(uid):
        if rec.get("status") == "parked":
            return rec
    return None


def purge_owner(uid: str) -> bool:
    """Records die with the account (the og_store_ready
    cascade step)."""
    _delete_blobs(uid)
    return True


# --- The dealing banks ------------------------------------------------------------
# Everything OG "deals" comes from these banks. Methods
# and motives carry the keyword sets accusation scoring
# matches against — the truth and its scoring keys are
# dealt together, from the same record, so a dealt game
# is always scoreable.

_NAMES = [
    "Marcus Vale", "Elena Brooks", "Tommy Reyes", "Priya Nair",
    "Walter Grange", "Sadie Cole", "Hugo Ferreira", "Nina Petrov",
    "Cal Donovan", "Rosa Jimenez", "Eddie Lark", "Mabel Quinn",
    "Frank Delgado", "Iris Chen", "Oscar Boyd", "Lena Fischer",
]
_ROLES = [
    "business partner", "chef", "ex-wife", "younger brother",
    "family lawyer", "housekeeper", "doctor", "rival club owner",
    "chauffeur", "business rival", "old army buddy", "accountant",
]
_SECRETS = [
    "owes the victim serious money",
    "was secretly selling company files",
    "had a key to the building they never returned",
    "was about to be fired and knew it",
    "is not who their paperwork says they are",
    "was seen arguing with the victim that morning",
    "stands to lose everything if the will changes",
    "was somewhere else that night — and won't say where",
]
_SETTINGS = [
    "a mansion up on the hill", "the night train to Chicago",
    "a smoky downtown nightclub", "a glass office tower after hours",
    "a small town where everybody knows everything",
    "a lake house with one road out",
]
_VICTIM_PROFILES = [
    "rich, powerful, and feared more than loved",
    "charming in public, ruthless on paper",
    "old money with a brand-new will",
    "a self-made name hiding one bad year",
]
_FINDERS = [
    "the housekeeper", "the night guard", "the business partner",
    "the victim's sister", "the first guest to arrive",
]
_FINDER_LIES = [
    "swears they never touched anything — but their prints are on the door handle",
    "says the door was locked — it wasn't, and they knew it",
    "left out the phone call they made ten minutes before finding the body",
    "moved the victim's glass before anyone else got there, and never said so",
]
_METHODS = [
    {"id": "poison", "label": "poisoned",
     "keywords": ["poison", "toxin", "drink", "glass", "cup"],
     "clue": "A faint bitter-almond smell hangs over the victim's glass.",
     "body": "No wounds anywhere. Discolored lips — something in the drink did this."},
    {"id": "stabbed", "label": "stabbed",
     "keywords": ["stab", "knife", "blade", "letter opener"],
     "clue": "A letter opener is missing from the desk set — the empty slot is clean.",
     "body": "One clean wound, up close. No sign of a struggle from across the room."},
    {"id": "shot", "label": "shot",
     "keywords": ["shot", "shoot", "gun", "bullet", "casing"],
     "clue": "A single shell casing sits under the rug's edge, half-hidden.",
     "body": "One bullet, fired close. Whoever did it stood right there."},
    {"id": "pushed", "label": "pushed",
     "keywords": ["push", "fell", "fall", "stairs", "balcony", "shove"],
     "clue": "Scuff marks rake across the tiles, all dragging one way — toward the edge.",
     "body": "A long fall, and the railing is far too high for an accident."},
    {"id": "strangled", "label": "strangled",
     "keywords": ["strangl", "chok", "throat", "rope", "hands"],
     "clue": "Fibers under the victim's nails — they fought back at the end.",
     "body": "Bruising at the throat. This was personal, and it was angry."},
]
_MOTIVES = [
    {"id": "inheritance", "label": "the inheritance",
     "keywords": ["inherit", "money", "will", "fortune", "cash", "rich"],
     "clue": "A fresh copy of the will sits in the grate, only half-burned."},
    {"id": "jealousy", "label": "jealousy",
     "keywords": ["jealous", "envy", "affair", "lover", "cheat"],
     "clue": "Two glasses on the side table. One's lipstick-marked — and it isn't the spouse's shade."},
    {"id": "blackmail", "label": "blackmail",
     "keywords": ["blackmail", "secret", "expose", "photo", "letters"],
     "clue": "An envelope of photographs, every face scratched out by hand."},
    {"id": "revenge", "label": "revenge",
     "keywords": ["revenge", "payback", "ruined", "grudge", "years ago"],
     "clue": "An old newspaper clipping about the victim, folded and refolded a hundred times."},
    {"id": "silencing", "label": "silencing a witness",
     "keywords": ["silenc", "witness", "testif", "court", "knew too much", "trial"],
     "clue": "A court summons with the victim's name on it — dated next week."},
    {"id": "business", "label": "the business deal",
     "keywords": ["business", "deal", "partner", "company", "contract", "rival"],
     "clue": "A contract on the desk with one signature line torn clean off."},
]
_ODD_CLUES = [
    "A wet footprint by the door, two sizes too small for anyone in the house.",
    "A train ticket for tomorrow, bought in the victim's name — by somebody else.",
    "Every clock in the room stopped at a different time.",
]
_ALIBI_PLACES = [
    "the library, alone", "the bar down the road", "my room, on the phone",
    "the garage, fixing the car", "the terrace, smoking", "the kitchen, with the staff",
]


def _method_by_id(mid: str) -> Optional[Dict]:
    for m in _METHODS:
        if m["id"] == mid:
            return m
    return None


def _motive_by_id(mid: str) -> Optional[Dict]:
    for m in _MOTIVES:
        if m["id"] == mid:
            return m
    return None


_DEAL_RE = re.compile(
    r"\byou pick\b|\bdeal '?em\b|\byou choose\b|\bsurprise me\b"
    r"|\bwhatever\b|\bidk\b|\bi don'?t care\b|\byour call\b", re.I)


def _dealt(text: str) -> bool:
    return bool(_DEAL_RE.search(str(text or "")))


# --- Setup: the templates, one question at a time ---------------------------------
# Template 1 (quick start) always runs. Template 2's
# fields fold in for a long game. Template 4 runs when
# the role answer is "suspect" (play a character).
# Each entry: (setup key, the question OG asks). The
# question text rides the directive as a fact — the
# persona asks it in his own voice.

_T1 = [
    ("victim", "Who got killed? Give me a name — or say \"you pick\" and I'll deal one."),
    ("setting", "Where did it happen — a mansion, a train, a nightclub, an office, a small town? Or \"you pick\"."),
    ("role", "You want to be the detective, play a suspect, or just watch it unfold?"),
    ("tone", "How dark we going — cozy, gritty noir, or full horror?"),
    ("twists", "Clean culprit, or twists where nobody's innocent?"),
]
_T2 = [
    ("victim_profile", "What's the victim like — rich, powerful, hated, loved, hiding something? Or \"you pick\"."),
    ("method", "How were they killed — poisoned, stabbed, shot, pushed, strangled? Or \"you pick\"."),
    ("finder", "Who found the body? Name them — or \"you pick\"."),
    ("suspects", "Give me 4 suspects — just names is fine. Or say \"deal 'em\" and I'll build the whole roster."),
    ("odd_clue", "What's the one clue that doesn't fit? Or \"you pick\"."),
    ("alone", "Is the killer working alone — yes or no? Or \"you pick\"."),
    ("second_death", "Does anyone else die before the end — yes or no? Or \"you pick\"."),
    ("reveal_style", "You want to guess the killer as we go, or save it all for the big reveal at the end?"),
]
_T4 = [
    ("char_name_job", "What's your character's name and job in this story?"),
    ("char_relation", "How did you know the victim?"),
    ("char_alibi", "What's your alibi — where were you when they died?"),
    ("char_secret", "What's the secret you're hoping the detective never finds?"),
    ("char_suspect", "Who do YOU think did it?"),
    ("char_innocent", "Are you actually innocent... or do you want me to secretly make you the killer?"),
]


def _parse_role(text: str) -> str:
    low = str(text or "").lower()
    if re.search(r"\bsuspect\b|\bcharacter\b|\bplay a\b|\bbe one of\b", low):
        return "character"
    if re.search(r"\bwatch\b|\bwatcher\b|\bnarrat", low):
        return "watcher"
    return "detective"


def _parse_tone(text: str) -> str:
    low = str(text or "").lower()
    if "horror" in low:
        return "full horror"
    if "cozy" in low or "cosy" in low:
        return "cozy"
    return "gritty noir"


def _parse_method(text: str) -> Optional[str]:
    low = str(text or "").lower()
    for m in _METHODS:
        if m["id"] in low or any(k in low for k in m["keywords"]):
            return m["id"]
    return None


def _parse_suspect_names(text: str) -> List[str]:
    raw = re.split(r",|\band\b", str(text or ""))
    names = []
    for part in raw:
        name = part.strip().strip(".!?")
        if 1 < len(name) <= 40 and not _dealt(name):
            names.append(name)
    return names[:4]


def create_game(uid: str, long_game: bool, now: float) -> Optional[Dict]:
    """One written record per game. At the cap, the
    oldest finished/abandoned record dies first — a
    live game is never pruned under the user."""
    mine = _records(uid)
    if _revalidate(uid, mine, now):
        pass
    if len(mine) >= MAX_GAMES:
        dead = sorted(
            (r for r in mine.values()
             if r.get("status") in ("finished", "abandoned")),
            key=lambda r: float(r.get("created") or 0))
        if not dead:
            return None
        mine.pop(dead[0]["id"], None)
    rec = {
        "id": uuid.uuid4().hex[:12],
        "owner": uid,
        "created": now,
        "last_touched": now,
        "status": "live",
        "phase": "t1",
        "q_index": 0,
        "long": bool(long_game),
        "setup": {},
        "character": {},
        "truth": None,
        "suspects": [],
        "clues": [],
        "watch_script": [],
        "last_suspect_id": "",
        "heat_done": 0,
        "guess": {},
        "score": None,
        "notice_sent": False,
        "pics": [],
        "pics_off": False,
        "pics_notice_sent": False,
        "pics_pending_notice": "",
        "title": "untitled mystery",
        "last_prompt": _T1[0][1],
    }
    mine[rec["id"]] = rec
    _save_records(uid, mine)
    return rec


def _deal_truth(rec: Dict):
    """THE COMMIT. Runs once, when setup completes —
    before the first scene is served. Everything the
    story will ever say is fixed here, in the record,
    server-side only."""
    rng = random.Random(rec["id"] + str(int(rec["created"])))
    setup = rec["setup"]
    char = rec.get("character") or {}

    victim = setup.get("victim") or rng.choice(_NAMES)
    setup["victim"] = victim
    setting = setup.get("setting") or rng.choice(_SETTINGS)
    setup["setting"] = setting
    used = {victim.lower()}
    pool = [n for n in _NAMES if n.lower() not in used]
    rng.shuffle(pool)

    suspects: List[Dict] = []

    def add_suspect(name, role=None, secret=None, alibi=None,
                    is_user=False):
        s = {
            "id": "s%d" % (len(suspects) + 1),
            "name": name,
            "role": role or rng.choice(_ROLES),
            "secret": secret or rng.choice(_SECRETS),
            "alibi": alibi or rng.choice(_ALIBI_PLACES),
            "is_user": bool(is_user),
            "statement": "",
            "dead": False,
        }
        suspects.append(s)
        return s

    user_suspect = None
    if setup.get("role") == "character" and char:
        cname = char.get("name") or "The Stranger"
        user_suspect = add_suspect(
            cname, role=char.get("job") or "guest",
            secret=char.get("secret") or rng.choice(_SECRETS),
            alibi=char.get("alibi") or rng.choice(_ALIBI_PLACES),
            is_user=True)

    named = setup.get("suspect_names") or []
    for name in named:
        if name.lower() != victim.lower():
            add_suspect(name)
    while len(suspects) < 4 and pool:
        add_suspect(pool.pop())

    # Killer: the user's character if they asked for it,
    # else dealt from the NPC roster.
    if user_suspect is not None and char.get("make_killer"):
        killer = user_suspect
    else:
        npcs = [s for s in suspects if not s["is_user"]] or suspects
        killer = rng.choice(npcs)

    method = _method_by_id(setup.get("method_id") or "") \
        or rng.choice(_METHODS)
    motive = rng.choice(_MOTIVES)
    finder = setup.get("finder") or rng.choice(_FINDERS)
    odd = setup.get("odd_clue") or rng.choice(_ODD_CLUES)
    accomplice = None
    if setup.get("alone") is False:
        others = [s for s in suspects if s["id"] != killer["id"]]
        accomplice = rng.choice(others) if others else None

    clues = [
        {"text": method["clue"], "real": True, "kind": "method",
         "served": False},
        {"text": motive["clue"], "real": True, "kind": "motive",
         "served": False},
        {"text": f"A button off a {killer['role']}'s coat, torn "
                 "loose by the door — fresh.",
         "real": True, "kind": "killer", "served": False},
        {"text": odd, "real": False, "kind": "odd", "served": False},
    ]
    innocents = [s for s in suspects if s["id"] != killer["id"]]
    if innocents:
        framed = rng.choice(innocents)
        clues.append({
            "text": f"{framed['name']}'s glove stuffed behind a "
                    "chair — way too easy to find.",
            "real": False, "kind": "planted", "served": False})
    if rec.get("long"):
        clues.append({
            "text": f"The victim's last note, half-written: "
                    f"\"...can't trust—\" and then nothing.",
            "real": True, "kind": "extra", "served": False})
    rest = clues[1:]
    rng.shuffle(rest)
    clues = [clues[0]] + rest

    for s in suspects:
        s["statement"] = (
            f"\"I was in {s['alibi']} when it happened. "
            f"I saw nothing, I heard nothing.\"")

    rec["truth"] = {
        "killer_id": killer["id"],
        "killer_name": killer["name"],
        "method_id": method["id"],
        "method_label": method["label"],
        "method_body": method["body"],
        "motive_id": motive["id"],
        "motive_label": motive["label"],
        "finder": finder,
        "finder_lie": rng.choice(_FINDER_LIES),
        "accomplice_name": accomplice["name"] if accomplice else "",
        "second_death": bool(setup.get("second_death")),
        "second_death_done": False,
    }
    rec["suspects"] = suspects
    rec["clues"] = clues
    # Watcher path: a fixed interleaved script of
    # statements and clues, dealt at commit — same
    # record, same truth, no drift.
    script = [{"kind": "body"}]
    for s in suspects:
        script.append({"kind": "statement", "suspect_id": s["id"]})
    for i in range(len(clues)):
        script.append({"kind": "clue", "clue_index": i})
    rng.shuffle(script)
    rec["watch_script"] = script
    rec["title"] = f"the death of {victim}"


# --- Directives -------------------------------------------------------------------
# The og_artifacts / og_userwatch seam shape: a grounded
# directive block the persona voices. Facts only, built
# from PUBLIC fields — this is the one door between the
# record and the chat, and truth fields never pass
# through it before the reveal.


def _result(tag: str, title: str, body: str) -> List[Dict]:
    return [{"title": title, "body": body, "href": ""}]


def _directive(body: str) -> List[Dict]:
    return _result(
        "MURDER MYSTERY", "Murder mystery — say it like this",
        "[MYSTERY] Deliver this in persona, plainly and "
        "briefly, with exactly these facts. Ask the ONE "
        f"question at the end, exactly one:\n{body}")


def _public_roster(rec: Dict) -> str:
    parts = []
    for s in rec.get("suspects") or []:
        tag = " (that's you)" if s.get("is_user") else ""
        gone = " — dead" if s.get("dead") else ""
        parts.append(f"{s['name']}, the {s['role']}{tag}{gone}")
    return "; ".join(parts)


def _find_suspect(rec: Dict, text: str) -> Optional[Dict]:
    low = str(text or "").lower()
    for s in rec.get("suspects") or []:
        name = s["name"].lower()
        first = name.split()[0]
        if name in low or re.search(rf"\b{re.escape(first)}\b", low):
            return s
    if re.search(r"\bme\b|\bi did it\b|\bmyself\b", low):
        for s in rec.get("suspects") or []:
            if s.get("is_user"):
                return s
    return None


# --- Setup machine ------------------------------------------------------------------


def _apply_setup_answer(rec: Dict, key: str, text: str):
    raw = str(text or "").strip()
    dealt = _dealt(raw)
    setup = rec["setup"]
    if "long game" in raw.lower():
        rec["long"] = True
    if key == "victim":
        setup["victim"] = None if dealt else raw[:60]
        if setup["victim"]:
            rec["title"] = f"the death of {setup['victim']}"
    elif key == "setting":
        setup["setting"] = None if dealt else raw[:80]
    elif key == "role":
        setup["role"] = _parse_role(raw)
    elif key == "tone":
        setup["tone"] = _parse_tone(raw)
    elif key == "twists":
        setup["twists"] = "clean" if "clean" in raw.lower() else "twists"
    elif key == "victim_profile":
        setup["victim_profile"] = None if dealt else raw[:120]
    elif key == "method":
        setup["method_id"] = None if dealt else _parse_method(raw)
    elif key == "finder":
        setup["finder"] = None if dealt else raw[:60]
    elif key == "suspects":
        setup["suspect_names"] = [] if dealt else _parse_suspect_names(raw)
    elif key == "odd_clue":
        setup["odd_clue"] = None if dealt else raw[:140]
    elif key == "alone":
        low = raw.lower()
        setup["alone"] = False if re.search(r"\bno\b|\baccomplice\b", low) else True
    elif key == "second_death":
        setup["second_death"] = bool(
            re.search(r"\byes\b|\banother\b|\bkill", raw.lower())) \
            and not dealt
    elif key == "reveal_style":
        setup["reveal_style"] = "guess" if "guess" in raw.lower() else "reveal"
    elif key == "char_name_job":
        char = rec["character"]
        parts = re.split(r",|\s-\s|\s+as\s+", raw, maxsplit=1)
        char["name"] = parts[0].strip()[:40] or "The Stranger"
        char["job"] = parts[1].strip()[:40] if len(parts) > 1 else "guest"
    elif key == "char_relation":
        rec["character"]["relation"] = raw[:120]
    elif key == "char_alibi":
        rec["character"]["alibi"] = raw[:140]
    elif key == "char_secret":
        rec["character"]["secret"] = raw[:140]
    elif key == "char_suspect":
        rec["character"]["suspect_guess"] = raw[:60]
    elif key == "char_innocent":
        low = raw.lower()
        rec["character"]["make_killer"] = bool(
            "killer" in low and "innocent" not in low.replace(
                "not innocent", ""))


def _next_setup_phase(rec: Dict) -> Optional[str]:
    if rec["phase"] == "t1":
        if rec.get("long"):
            return "t2"
        if rec["setup"].get("role") == "character":
            return "t4"
        return None
    if rec["phase"] == "t2":
        if rec["setup"].get("role") == "character":
            return "t4"
        return None
    return None


def _opening(rec: Dict) -> List[Dict]:
    setup = rec["setup"]
    truth = rec["truth"]
    role = setup.get("role", "detective")
    facts = (
        f"The game is set. {setup.get('victim')} is dead — "
        f"found in {setup.get('setting')}. "
        f"{truth['finder']} found the body. "
        f"The tone is {setup.get('tone', 'gritty noir')}. "
        f"The suspects: {_public_roster(rec)}.")
    if setup.get("victim_profile"):
        facts += f" The victim: {setup['victim_profile']}."
    if role == "watcher":
        q = ("Say \"next\" whenever you're ready for the "
             "next piece — or accuse whenever you think "
             "you've got it.")
    elif role == "character":
        q = ("The others are watching you too. Who do you "
             "talk to first — or do you search the room?")
    else:
        q = ("Who do you want to interrogate first — or "
             "do you search the room?")
    rec["last_prompt"] = q
    return _directive(facts + f"\n{q}")


def _setup_turn(rec: Dict, text: str) -> List[Dict]:
    table = {"t1": _T1, "t2": _T2, "t4": _T4}[rec["phase"]]
    key, _q = table[rec["q_index"]]
    _apply_setup_answer(rec, key, text)
    rec["q_index"] += 1
    if rec["q_index"] < len(table):
        nq = table[rec["q_index"]][1]
        rec["last_prompt"] = nq
        return _directive(f"Locked in. Next question: {nq}")
    nxt = _next_setup_phase(rec)
    if nxt:
        rec["phase"] = nxt
        rec["q_index"] = 0
        table2 = {"t2": _T2, "t4": _T4}[nxt]
        nq = table2[0][1]
        rec["last_prompt"] = nq
        lead = ("Now the full setup — eight more, same "
                "deal: answer or say \"you pick\"."
                if nxt == "t2" else
                "You're playing a character in this one. "
                "Six quick questions to build them.")
        return _directive(f"{lead}\nFirst: {nq}")
    _deal_truth(rec)
    rec["phase"] = "play"
    made = _pics.opening_pictures(rec)
    out = _opening(rec)
    if made:
        out[0]["body"] += (
            "\n[Pictures made for this game: the crime "
            "scene and a portrait for each suspect — "
            "saved to the Library under Images.]")
    return out


# --- Play machine (Template 3) -------------------------------------------------------


def _unserved(rec: Dict) -> List[Dict]:
    return [c for c in rec.get("clues") or [] if not c.get("served")]


def _second_death_event(rec: Dict) -> str:
    truth = rec["truth"]
    if not truth.get("second_death") or truth.get("second_death_done"):
        return ""
    total = len(rec.get("clues") or [])
    served = sum(1 for c in rec["clues"] if c.get("served"))
    if served < max(1, total // 2):
        return ""
    candidates = [s for s in rec["suspects"]
                  if s["id"] != truth["killer_id"]
                  and not s.get("is_user") and not s.get("dead")]
    if not candidates:
        return ""
    victim2 = candidates[0]
    victim2["dead"] = True
    truth["second_death_done"] = True
    return (f" And it gets worse: {victim2['name']} was just "
            "found dead too. The killer is still moving.")


def _serve_next_clue(rec: Dict) -> Optional[str]:
    left = _unserved(rec)
    if not left:
        return None
    clue = left[0]
    clue["served"] = True
    _pics.clue_picture(rec, clue)
    return clue["text"] + _second_death_event(rec)


def _heat_check(rec: Dict) -> str:
    """Character path: at set beats, the room turns on
    the user's character. Their answer is flavor on the
    record; the loop does not branch on it."""
    if rec["setup"].get("role") != "character":
        return ""
    served = sum(1 for c in rec["clues"] if c.get("served"))
    if served in (2, 4) and rec.get("heat_done", 0) < (1 if served == 2 else 2):
        rec["heat_done"] = rec.get("heat_done", 0) + 1
        rec["heat_pending"] = True
        char = rec.get("character") or {}
        return (f"\nAll eyes swing to you, "
                f"{char.get('name', 'stranger')}. Where'd you "
                f"say you were when {rec['setup'].get('victim')} died?")
    return ""


def _play_turn(rec: Dict, text: str) -> List[Dict]:
    low = str(text or "").lower()
    setup = rec["setup"]
    truth = rec["truth"]

    if rec.get("heat_pending"):
        rec["heat_pending"] = False
        rec.setdefault("character", {}).setdefault(
            "heat_answers", []).append(str(text)[:140])
        q = "Noted. Who's next — interrogate someone, or search the room?"
        rec["last_prompt"] = q
        return _directive(f"Your answer goes on the record.\n{q}")

    if re.search(r"\baccuse\b|\block it in\b|\bi know who did it\b", low):
        rec["phase"] = "accuse_who"
        q = "First: who did it? Name them."
        rec["last_prompt"] = q
        return _directive(
            "Lock it in. Who did it, how did they do it, "
            f"and what was the motive?\n{q}")

    if setup.get("role") == "watcher":
        return _watcher_turn(rec, low)

    suspect = _find_suspect(rec, text)
    if suspect is not None and not re.search(
            r"\bsearch\b|\broom\b|\bbody\b|\bclue\b|\blead\b", low):
        if suspect.get("dead"):
            body = (f"{suspect['name']} is dead — no more "
                    "answers coming from them.")
            q = "Who else — or search the room?"
        else:
            rec["last_suspect_id"] = suspect["id"]
            body = (f"{suspect['name']} (the {suspect['role']}) "
                    f"says: {suspect['statement']}")
            q = (f"Believe {suspect['name'].split()[0]}, or press "
                 "them harder? Or search the room, check the "
                 "body, follow a lead.")
        rec["last_prompt"] = q
        return _directive(body + f"\n{q}")

    if "press" in low and rec.get("last_suspect_id"):
        last = next((s for s in rec["suspects"]
                     if s["id"] == rec["last_suspect_id"]), None)
        if last is not None and last["id"] == truth["killer_id"]:
            clue = _serve_next_clue(rec)
            body = (f"{last['name']} goes quiet — then quietly "
                    "changes one small detail of their story.")
            if clue:
                body += f" And this surfaces: {clue}"
            q = "Who's your #1 suspect right now?"
        elif last is not None:
            body = f"{last['name']} holds up. Nothing new shakes loose."
            q = "Search the room, check the body, or follow a lead?"
        else:
            body = "Nobody's on the hook yet."
            q = "Interrogate somebody first — who?"
        rec["last_prompt"] = q
        return _directive(body + f"\n{q}")

    if re.search(r"\bbelieve\b", low):
        q = "Alright. Search the room, check the body, or interrogate someone else?"
        rec["last_prompt"] = q
        return _directive(f"Noted — you believe them.\n{q}")

    if re.search(r"\bbody\b|\bcorpse\b|\bcheck the\b", low):
        if not rec.get("body_served"):
            rec["body_served"] = True
            q = "That clue — you think it's real, or planted?"
            rec["last_prompt"] = q
            return _directive(
                f"On the body: {truth['method_body']}\n{q}")
        body = "You've seen the body. Nothing more there."
        q = "Search the room, or interrogate someone?"
        rec["last_prompt"] = q
        return _directive(body + f"\n{q}")

    if re.search(r"\bsearch\b|\broom\b|\bfollow\b|\blead\b|\bclue\b"
                 r"|\bnext\b|\bkeep going\b|\bcontinue\b", low) \
            or True:
        clue = _serve_next_clue(rec)
        if clue is not None:
            heat = _heat_check(rec)
            if heat:
                rec["last_prompt"] = "Where were you when it happened?"
                return _directive(f"New evidence: {clue}{heat}")
            q = "That clue — you think it's real, or planted?"
            rec["last_prompt"] = q
            return _directive(f"New evidence: {clue}\n{q}")
        q = ("That's every clue on the table. Ready to accuse — "
             "or interrogate one more?")
        rec["last_prompt"] = q
        return _directive(q)


def _watcher_turn(rec: Dict, low: str) -> List[Dict]:
    script = rec.get("watch_script") or []
    if re.search(r"\breveal\b|\bjust tell me\b|\bwho did it\b", low) \
            and not script:
        return _reveal(rec, scored=False)
    if not script:
        q = ("That's everything on the table. Accuse — say "
             "\"accuse\" — or say \"reveal\" and I'll lay it out.")
        rec["last_prompt"] = q
        return _directive(q)
    item = script.pop(0)
    rec["watch_script"] = script
    if item["kind"] == "statement":
        s = next((x for x in rec["suspects"]
                  if x["id"] == item["suspect_id"]), None)
        body = (f"{s['name']} (the {s['role']}) says: "
                f"{s['statement']}" if s else "A voice from the hall.")
    elif item["kind"] == "body":
        rec["body_served"] = True
        body = f"On the body: {rec['truth']['method_body']}"
    else:
        clue = rec["clues"][item["clue_index"]]
        clue["served"] = True
        _pics.clue_picture(rec, clue)
        body = "New evidence: " + clue["text"] + _second_death_event(rec)
    q = ("Say \"next\" for the next piece — or accuse "
         "whenever you're ready.")
    rec["last_prompt"] = q
    return _directive(body + f"\n{q}")


# --- Accusation + reveal ---------------------------------------------------------------


def _score_text(rec: Dict, score: Dict) -> str:
    truth = rec["truth"]
    def mark(ok):
        return "RIGHT" if ok else "WRONG"
    lines = [
        f"The killer: {mark(score['killer_ok'])} — it was "
        f"{truth['killer_name']}.",
        f"The method: {mark(score['method_ok'])} — "
        f"{truth['method_label']}.",
        f"The motive: {mark(score['motive_ok'])} — "
        f"{truth['motive_label']}.",
    ]
    got = sum(1 for k in ("killer_ok", "method_ok", "motive_ok")
              if score[k])
    lines.append(f"You got {got} of 3.")
    return "\n".join(lines)


def _truth_text(rec: Dict) -> str:
    truth = rec["truth"]
    setup = rec["setup"]
    body = (
        f"THE REVEAL. {truth['killer_name']} killed "
        f"{setup.get('victim')} — {truth['method_label']}, "
        f"for {truth['motive_label']}. "
        f"{truth['finder']} found the body, and here's what "
        f"they hid: the finder {truth['finder_lie']}.")
    if truth.get("accomplice_name"):
        body += (f" And they weren't alone — "
                 f"{truth['accomplice_name']} was in on it.")
    killer = next((s for s in rec["suspects"]
                   if s["id"] == truth["killer_id"]), None)
    if killer is not None and killer.get("is_user"):
        body += (" And the twist you ordered: the killer "
                 "was you all along.")
    return body


def _reveal(rec: Dict, scored: bool) -> List[Dict]:
    rec["phase"] = "done"
    rec["status"] = "finished"
    body = _truth_text(rec)
    if scored and rec.get("score"):
        body += "\n" + _score_text(rec, rec["score"])
    q = "Play again? Say \"play again\" and we deal a fresh one."
    rec["last_prompt"] = q
    return _directive(body + f"\n{q}")


def _accuse_turn(rec: Dict, text: str) -> List[Dict]:
    truth = rec["truth"]
    raw = str(text or "")
    if rec["phase"] == "accuse_who":
        suspect = _find_suspect(rec, raw)
        if suspect is None:
            q = "Didn't catch a name. Who did it? Name one of the suspects."
            rec["last_prompt"] = q
            return _directive(q)
        rec["guess"] = {"killer_id": suspect["id"],
                        "killer_name": suspect["name"]}
        rec["phase"] = "accuse_how"
        q = f"{suspect['name']}. Locked. Now: how did they do it?"
        rec["last_prompt"] = q
        return _directive(q)
    if rec["phase"] == "accuse_how":
        method = _method_by_id(truth["method_id"])
        low = raw.lower()
        rec["guess"]["method_ok"] = bool(
            method and any(k in low for k in method["keywords"]))
        rec["guess"]["method_raw"] = raw[:80]
        rec["phase"] = "accuse_why"
        q = "And the motive — why did they do it?"
        rec["last_prompt"] = q
        return _directive(f"On the record.\n{q}")
    # accuse_why
    motive = _motive_by_id(truth["motive_id"])
    low = raw.lower()
    rec["guess"]["motive_ok"] = bool(
        motive and any(k in low for k in motive["keywords"]))
    rec["guess"]["motive_raw"] = raw[:80]
    rec["score"] = {
        "killer_ok": rec["guess"].get("killer_id") == truth["killer_id"],
        "method_ok": bool(rec["guess"].get("method_ok")),
        "motive_ok": bool(rec["guess"].get("motive_ok")),
    }
    return _reveal(rec, scored=True)


# --- Chat claims -------------------------------------------------------------------------

_START_RE = re.compile(
    r"\bmurder mystery\b|\bwhodunit\b|\bmystery game\b"
    r"|\bplay (?:a |the )?(?:murder )?mystery\b"
    r"|\bstart (?:a |the )?(?:murder )?mystery\b", re.I)
_PLAY_AGAIN_RE = re.compile(
    r"\bplay again\b|\bnew game\b|\banother (?:game|mystery)\b", re.I)
_LIST_RE = re.compile(
    r"\bmy games\b|\blist (?:my )?games\b|\bgames list\b", re.I)
_RESUME_RE = re.compile(
    r"\bresume (?:my |the )?game\b|\bresume (?:my |the )?mystery\b", re.I)
_ABANDON_RE = re.compile(
    r"\babandon (?:my |the )?(?:game|mystery)\b"
    r"|\bquit (?:the |my )?(?:game|mystery)\b"
    r"|\bend (?:the |my )?(?:game|mystery)\b"
    r"|\bstop (?:the |my )?(?:game|mystery)\b", re.I)
_BOOK_RE = re.compile(r"\bwrite\b[^.!?]*\bbook\b|\bbook about\b", re.I)


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "")
    low = raw.lower()
    if not uid or not mystery_enabled():
        return None
    games = list_games_for_owner(uid)
    live = next((g for g in games if g.get("status") == "live"), None)
    parked = next((g for g in games if g.get("status") == "parked"), None)
    if _LIST_RE.search(low) and games:
        return {"op": "list"}
    if _RESUME_RE.search(low) and parked is not None:
        return {"op": "resume"}
    if live is not None and _ABANDON_RE.search(low):
        return {"op": "abandon"}
    if live is not None and _START_RE.search(low):
        return {"op": "already"}
    if live is not None:
        return {"op": "turn", "text": raw}
    if _START_RE.search(low) and not _BOOK_RE.search(low):
        return {"op": "start",
                "long": bool(re.search(r"\blong\b|\bfull\b", low))}
    newest = games[0] if games else None
    if newest is not None and newest.get("status") == "finished" \
            and _PLAY_AGAIN_RE.search(low):
        return {"op": "start",
                "long": bool(re.search(r"\blong\b|\bfull\b", low))}
    return None


def mystery_results(job: Dict, uid: str) -> List[Dict]:
    op = job.get("op")
    now = time.time()
    if op == "start":
        rec = create_game(uid, job.get("long", False), now)
        if rec is None:
            return _directive(
                "Your games list is full of live games — "
                "finish or abandon one first, then ask again.")
        q = _T1[0][1]
        lead = ("A murder mystery. Five quick setup "
                "questions, one at a time — answer each, or "
                "say \"you pick\" and I'll deal it. Say "
                "\"long game\" any time in setup for the "
                "full build.")
        if job.get("long"):
            lead = ("A LONG murder mystery. Thirteen setup "
                    "questions, one at a time — answer each, "
                    "or say \"you pick\" and I'll deal it.")
        return _directive(f"{lead}\nFirst: {q}")
    if op == "already":
        live = live_game_for_owner(uid)
        title = live.get("title", "your mystery") if live else "your mystery"
        q = live.get("last_prompt", "") if live else ""
        return _directive(
            f"You've already got a game running — {title}. "
            "Say \"abandon my game\" if you want to scrap it "
            f"and start over.\n{q}")
    if op == "abandon":
        live = live_game_for_owner(uid)
        if live is None:
            return _directive("No live game to abandon.")
        mine = _records(uid)
        rec = mine.get(live["id"])
        if rec is not None:
            rec["status"] = "abandoned"
            rec["last_touched"] = now
            _save_records(uid, mine)
        return _directive(
            f"Done — {live.get('title', 'the game')} is "
            "abandoned and off your live list. Ask for a "
            "murder mystery any time to deal a fresh one.")
    if op == "resume":
        parked = parked_game_for_owner(uid)
        if parked is None:
            return _directive("No parked game waiting to resume.")
        mine = _records(uid)
        rec = mine.get(parked["id"])
        if rec is not None:
            rec["status"] = "live"
            rec["last_touched"] = now
            _save_records(uid, mine)
        q = parked.get("last_prompt", "")
        return _directive(
            f"Back on — {parked.get('title', 'your mystery')} "
            f"is live again.\n{q}")
    if op == "list":
        games = list_games_for_owner(uid)
        if not games:
            return _directive(
                "No murder mysteries on your list. Ask me "
                "for one and we'll deal it.")
        lines = []
        for g in games:
            line = (f"- {g.get('title', 'untitled')} — "
                    f"{g.get('status')}")
            if g.get("pics"):
                labels = "; ".join(
                    str(p.get("label", "picture"))
                    for p in g["pics"])
                line += (f"\n  Pictures ({len(g['pics'])}): "
                         f"{labels} — in your Library "
                         "under Images.")
            lines.append(line)
        return _directive(
            "Your murder mysteries:\n" + "\n".join(lines))
    if op == "turn":
        live = live_game_for_owner(uid)
        if live is None:
            return _directive(
                "That game isn't live anymore. Ask for a "
                "murder mystery to start a fresh one.")
        mine = _records(uid)
        rec = mine.get(live["id"])
        if rec is None:
            return _directive("That game record is gone.")
        phase = rec.get("phase")
        if phase in ("t1", "t2", "t4"):
            out = _setup_turn(rec, job.get("text", ""))
        elif phase == "play":
            out = _play_turn(rec, job.get("text", ""))
        elif phase in ("accuse_who", "accuse_how", "accuse_why"):
            out = _accuse_turn(rec, job.get("text", ""))
        else:
            out = _directive("That game is finished.")
        # MM-PICS: the ONE plain text-only notice, on
        # the turn where pictures first failed / capped.
        if rec.get("pics_pending_notice") and out:
            out[0]["body"] += "\n" + rec["pics_pending_notice"]
            rec["pics_pending_notice"] = ""
            rec["pics_notice_sent"] = True
        rec["last_touched"] = now
        mine[rec["id"]] = rec
        _save_records(uid, mine)
        return out
    return _directive("That mystery ask didn't parse — say "
                      "\"murder mystery\" and we'll deal one.")


# --- The chat seam (the og_userwatch pattern) -------------------------------------------

_slot: Dict = {"job": None}


def install_mystery_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks
    so murder-mystery asks and in-game turns run here.
    Installed in app.py's build chain; persona files
    never touched."""
    if getattr(agent_instance, "_og_mystery_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _slot["job"] = None
        try:
            uid = get_uid()
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Mystery trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        _slot["job"] = None
        if job:
            try:
                results = mystery_results(job, get_uid())
            except Exception as e:
                logger.warning(f"Mystery job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_mystery_installed = True


# --- Owner door: GET /games + resume/abandon ----------------------------------------------


def _request_uid(request: Request) -> str:
    cookie_uid = request.cookies.get("ogai_uid") or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, cookie_uid)
    except Exception:
        return cookie_uid


def _render_games_page(uid: str) -> str:
    games = list_games_for_owner(uid)
    rows = ""
    for g in games:
        rows += (
            "<div style='border:1px solid #8a6d1f;padding:12px;"
            "margin:10px 0;border-radius:8px'>"
            f"<b>{html.escape(str(g.get('title', 'untitled')))}</b><br>"
            f"Status: {html.escape(str(g.get('status', '')))} · "
            f"phase: {html.escape(str(g.get('phase', '')))}<br>")
        if g.get("pics"):
            pics_txt = "; ".join(
                html.escape(str(p.get("label", "picture")))
                + (f" (Library #{html.escape(str(p['library_id']))})"
                   if p.get("library_id") else "")
                for p in g["pics"])
            rows += (f"Pictures ({len(g['pics'])}): {pics_txt} "
                     "&mdash; saved to your Library under "
                     "Images.<br>")
        if g.get("status") == "live":
            rows += (
                f"<form method='post' action='/games/{g.get('id')}/abandon'>"
                "<button type='submit'>Abandon this game</button>"
                "</form>")
        elif g.get("status") == "parked":
            rows += (
                f"<form method='post' action='/games/{g.get('id')}/resume'>"
                "<button type='submit'>Resume this game</button>"
                "</form>")
        rows += "</div>"
    if not rows:
        rows = ("<p>No murder mysteries yet. Ask OG in chat "
                "for a murder mystery and he'll deal one.</p>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Your murder mysteries — OG</title></head>"
            "<body style='background:#111;color:#eee;font-family:"
            "system-ui;padding:20px'><h1>🔪 Your murder mysteries</h1>"
            + rows + "</body></html>")


def register_mystery_routes(app):
    @app.get("/games")
    def games_page(request: Request):
        if not mystery_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = _request_uid(request)
        if not uid:
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(_render_games_page(uid),
                            headers={"Cache-Control": "no-cache"})

    @app.post("/games/{game_id}/abandon")
    def games_abandon(request: Request, game_id: str):
        if not mystery_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = _request_uid(request)
        rec = _get_record(uid, game_id) if uid else None
        if rec is None or rec.get("status") not in ("live", "parked"):
            raise HTTPException(status_code=404, detail="Not found")
        mine = _records(uid)
        mine[game_id]["status"] = "abandoned"
        mine[game_id]["last_touched"] = time.time()
        _save_records(uid, mine)
        return HTMLResponse(_render_games_page(uid),
                            headers={"Cache-Control": "no-cache"})

    @app.post("/games/{game_id}/resume")
    def games_resume(request: Request, game_id: str):
        if not mystery_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = _request_uid(request)
        rec = _get_record(uid, game_id) if uid else None
        if rec is None or rec.get("status") != "parked":
            raise HTTPException(status_code=404, detail="Not found")
        mine = _records(uid)
        mine[game_id]["status"] = "live"
        mine[game_id]["last_touched"] = time.time()
        _save_records(uid, mine)
        return HTMLResponse(_render_games_page(uid),
                            headers={"Cache-Control": "no-cache"})

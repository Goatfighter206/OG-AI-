"""Voice folder (Round 98, Brent's orders 2026-10-12):
"we should have a couple different voice styles to
use" + "it should go in a folder, called voice that
you can click on to see all the voices to choose
from."

ONE per-account voice-style store — the single source
both faces read for how OG SOUNDS:

    GET  /voice?avatar=og|kane|sable
        -> {"ok": true, "voice": <effective voice id>,
            "picked": <stored pick or null>,
            "avatar": <avatar>, "default_voice": <id>,
            "styles": [{"voice", "name", "note",
                        "current", "is_default"}, ...]}
    POST /voice {"voice": <id>, "avatar": <optional>}
        -> the same payload, after the pick is stored
    no uid -> 401 sign_in_required; an unknown voice
    id -> 400, writes nothing (the R73/R84 contract
    shape; routes ride og_library's registration — NOT
    in PUBLIC_PATHS, so the Round 44 gate covers them).

WHAT A "STYLE" HONESTLY IS. The audit (round-98/
NOTES.md): POST /tts calls OpenAI's speech endpoint
with model "tts-1" — a voice-selection model. It
takes a voice id and the text; it has NO delivery-
instructions channel (that is the newer
gpt-4o-mini-tts model's feature, and /tts does not
call it). So a style here is exactly a VOICE: the six
Round 37 voices, each of which measurably changes
the /tts request (the suite asserts the request body
carries the pick). There are no fake styles and no
delivery styles this round — saying otherwise would
need an app.py model change, and app.py is frozen.

DEFAULTS. No record = the AVATAR's own voice — the
Round 37 map, kept as this module's one table and
pinned by the suite against app.py's TTS_VOICES /
TTS_DEFAULT_VOICE and the Unity client's VoiceId
table so the faces cannot drift: OG = onyx,
KANE = echo, SABLE = nova. The pick is per-ACCOUNT
(it follows the user across devices); the default
is per-avatar (each face asks with the avatar on
its own stage: the web's stage is OG, the app sends
its selected figure). Picking a style changes the
SOUND only — never the words (persona law).

WHO READS THE STORE: the two speaking clients. The
web page speaks through speakReply -> fetchTTSPromise
-> POST /tts; static/og_r98.js carries this store's
effective voice into that request (the page's own
default path is untouched when no pick exists).
The app speaks through OGApiClient.Speak; fix82
caches this store's effective voice the fix75 way
and Speak sends it. Generator narration does NOT
read this store: the podcast is two-voice by design
(its own resolve_voices) and story videos pin onyx
in og_video — both named out of scope in the spec.

STORAGE. The codebase's established durable pattern
— one key -> JSON blob — in this module's own store,
exactly the og_permissions form (Postgres table
og_voice_data when OG_MEMORY_DB_URL is set, else a
JSON file: OG_VOICE_STORE or voice_store.json).
Records are keyed "voice:"+uid -> {"voice": <id>}
and hold ONLY an explicit pick; no record already
reads exactly right for every existing account.
Round 99's law: with a database configured, a store
error raises og_db.StoreError and the caller fails
honestly (the app-wide 503) — never a shadow answer.

Ticket R addendum (2026-10-23): the MEANER PACK —
forty accented styles in four families (Mexican,
Russian, Southern, gangster; five women's + five
men's voices each) — joins the table below the six
classics. The audit for it is written at the pack
table itself; the short version: tts-1 still has no
instructions channel, so the pack does NOT ride it.
A pack style resolves (speech_for) to a base voice
plus delivery instructions on gpt-4o-mini-tts —
the model whose instructions channel OpenAI's own
text-to-speech guide lists accent control for. The
six classics resolve exactly as Round 98 left them.

Real-voice rebuild addendum (Ticket R, 2026-10-23,
later the same day): Brent's ear FAILED the
impression pack ("the voices don't have real accents
or real voices"), so the pack was rebuilt on REAL
voices. Each pack entry now names one real
ElevenLabs library voice (its el_voice_id, from the
census in cand-voicepack-el/REPORT.md §6 — the picks
Brent heard as samples and approved), and speech_for
resolves a pack id to that voice on the ElevenLabs
lane ({"provider": "elevenlabs", ...}); /tts speaks
it through og_voice_lab's library seam behind the
pack-only flag OG_VOICE_PACK_EL_ENABLED. The forty
ids are UNCHANGED, so an existing pick remaps to the
real voice and just starts sounding right. The
gangster family's women's shelf is five noir /
femme-fatale character voices, labeled Noir in the
folder — there is no honest full mob shelf for
women in the library, and the copy says so instead
of faking one. The impression apparatus (base voices
+ instructions) is retired; the classics are
byte-untouched and still resolve to tts-1.

R4 addendum (Ticket R, 2026-10-23, later still):
Brent heard the landed pack and re-picked the
gangster shelf by ear. The five gangster men are
new (Antonio off the census bench, The Duke,
Branok, Damien, Final Boss) under the SAME five
ids. The noir women's shelf keeps Jean at
gangster-marin and gains six: Serafina, AImee,
Aida and Alicia take the four retired ids, and
Elisabeth Carson + Aaliyah join on two new ids
(gangster-alloy, gangster-fable) — seven noir
women, so the gangster family is 5M + 7F, the
pack 42 styles and the folder 48. Stored picks
remap by id, as before. Audition record:
cand-voicepack-el/auditions2/REPORT.md.

R5 addendum (Ticket R, 2026-10-23, still later):
Brent cut the gangster family ENTIRELY — "Takeoff
gangster, voices, both females and male" — and
ordered two new families in its place: SEXY, ten
women's voices in a moaning style (breathy /
sensual library filings, MoanElisa first), and
GHETTO, twenty voices (ten women's + ten men's)
from urban / street / sassy-attitude library
filings. Jean survives the cut — she re-enters
under Sexy, the one gangster-shelf voice Brent
liked — joined by three more of the noir shelf
(Serafina, Aida, Alicia) under their own names;
Aaliyah re-enters under Ghetto. Every other
gangster el id is retired. The pack is now five
families, 60 styles, the folder 66. Census +
picks: the R5 sections of the Ticket R file.
Stored picks remap by id where the id survives;
a pick whose id retired reads as no pick (the
store's standing guard) and the face falls back
to its avatar default — never a crash, never a
substituted engine.
"""

import asyncio
import json
import logging
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_VOICE_STORE", "voice_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# ===========================================================================
# The style table (display order: OG's own voice first, then the
# other two stage voices, then the rest of the Round 37 set)
# ===========================================================================

STYLES = [
    {"voice": "onyx", "name": "Onyx",
     "note": "OG's own voice — deep and steady, the one "
             "he ships with."},
    {"voice": "echo", "name": "Echo",
     "note": "Kane's voice — a broader, heavier man's "
             "voice."},
    {"voice": "nova", "name": "Nova",
     "note": "Sable's voice — a clear woman's voice."},
    {"voice": "fable", "name": "Fable",
     "note": "A lively storyteller's voice with a "
             "British edge."},
    {"voice": "alloy", "name": "Alloy",
     "note": "Even and balanced — sits right in the "
             "middle."},
    {"voice": "shimmer", "name": "Shimmer",
     "note": "A softer, clearer woman's voice."},
]

# ===========================================================================
# Ticket R: the meaner pack — forty REAL voices.
#
# THE AUDIT (2026-10-23, Ticket R): the pack first
# shipped as base voice + delivery instructions on
# OpenAI's gpt-4o-mini-tts. Brent's ear failed it the
# same morning: "the voices don't have real accents
# or real voices." He was right — an instruction can
# ask for an accent; it cannot BE one.
#
# THE REBUILD (2026-10-23, cand-voicepack-el): every
# pack entry below names ONE real ElevenLabs library
# voice — the picks from the census in
# cand-voicepack-el/REPORT.md §6, rendered as samples
# Brent heard and approved ("Sounds good"). A pack
# style IS that voice: speech_for resolves the id to
# {"provider": "elevenlabs", "voice": <el_voice_id>}
# and /tts speaks it through og_voice_lab's library
# seam, gated by the pack-only flag
# OG_VOICE_PACK_EL_ENABLED (+ the provider key). With
# the flag off or the key absent the pack fails
# honestly — it NEVER falls back to the retired
# impression. The forty ids are the same ids the
# impression build shipped, so stored picks remap to
# the real voices untouched.
#
# THE SPLIT (Brent's refinement): each family's ten =
# five women's voices + five men's. The gangster
# family's women's shelf is honestly NOIR: the
# library holds no full shelf of women mob personas,
# so those five are noir / femme-fatale character
# voices and the folder labels them Noir — the
# gangster men are the real mob shelf. On the bench
# (not shipped): Antonio, the gangster alternate, and
# Dante, who bills at a custom character rate — see
# the census.
#
# R4 (2026-10-23, Brent's ear rulings on the landed
# pack — "Just use them all" on the round-2
# auditions): the gangster men are RE-PICKED —
# Antonio comes off the bench, joined by The Duke,
# Branok, Damien and Final Boss, under the same five
# ids; the five first-shipped men are retired. The
# noir shelf keeps ONLY Jean of the original five;
# Serafina, AImee, Aida and Alicia take the retired
# four ids, and Elisabeth Carson + Aaliyah join on
# new ids (gangster-alloy, gangster-fable) — seven
# noir women in all, so this family breaks the 5 + 5
# split on purpose: 5M + 7F, pack 42, folder 48.
#
# R5 (2026-10-23, Brent's ruling the same day):
# the gangster family is REMOVED whole — all
# twelve rows, both shelves. In its place: SEXY
# (ten women, breathy / sensual filings — the
# moaning style Brent named, carried by the
# voices' own filed descriptors) and GHETTO
# (twenty: ten women + ten men, urban / street /
# sassy-attitude filings). The library's honest
# limit stands on the record: its deep bench here
# is sassy / street-attitude CHARACTERS, not
# dialect-filed voices — the folder rows carry
# the real filed names, nothing relabeled. Five
# el ids carry over from the retired shelf (Jean,
# Serafina, Aida, Alicia -> Sexy; Aaliyah ->
# Ghetto); the other seven retire with it. Pack
# 60, folder 66, five families.
#
# THE SIX CLASSICS DO NOT MOVE: no el_voice_id, no
# provider tag — their upstream request stays
# byte-identical to the Round 37/98 one (tts-1, no
# instructions key), so every existing classic pick
# sounds exactly as it did. "Meaner" is the voices
# themselves now — real people recorded with real
# accents; the words are never touched (persona law).
# ===========================================================================

CLASSIC_MODEL = "tts-1"

_PACK_SHELVES = {
    "mexican": ("Mexican", "Meaner pack — Mexican accent"),
    "russian": ("Russian", "Meaner pack — Russian accent"),
    "southern": ("Southern", "Meaner pack — Southern accent"),
    "sexy": ("Sexy", "Meaner pack — Sexy"),
    "ghetto": ("Ghetto", "Meaner pack — Ghetto"),
}

# (id family, shelf, base, gender, library name,
#  el_voice_id, folder note) — the census picks,
#  cand-voicepack-el/REPORT.md §6, in folder order:
#  each family women first, then men — Mexican /
#  Russian / Southern F1..F5 + M1..M5; Sexy (R5)
#  F1..F10, no men; Ghetto (R5) F1..F10 + M1..M10.
_PACK_EL = (
    ("mexican", "mexican", "marin", "female", "Erika",
     "r3SDVYUIvcC4EweQtSj0",
     "Erika — a woman from Sinaloa: raspy and genuine, "
     "a little hoarse. A real ElevenLabs library voice."),
    ("mexican", "mexican", "coral", "female", "Adri",
     "z1ngDYs2H24Xsd8ts3az",
     "Adri — husky, natural and credible. A real "
     "ElevenLabs library voice."),
    ("mexican", "mexican", "nova", "female", "Paloma",
     "j3pzCmrdzkT5DUtyT61a",
     "Paloma — resonant and earthy, mature, with velvet "
     "depth and authority. A real ElevenLabs library voice."),
    ("mexican", "mexican", "sage", "female", "Magda",
     "qpw1Pr9hKQ9lsPLpeNcM",
     "Magda — calm and grounded, crisp and clear, a soft "
     "Mexican accent. A real ElevenLabs library voice."),
    ("mexican", "mexican", "shimmer", "female", "Emily",
     "SLiC4fN7sbVbYUgtVxNM",
     "Emily — soft, welcoming and upbeat; warm and "
     "youthful. A real ElevenLabs library voice."),
    ("mexican", "mexican", "onyx", "male", "Carlos Corella",
     "LnGOA2SxH2fX1e1iNzEp",
     "Carlos Corella — authoritative and deep. A real "
     "ElevenLabs library voice."),
    ("mexican", "mexican", "echo", "male", "Luis Guillermo",
     "gwk2CKlAQ1B4a2RPxJoF",
     "Luis Guillermo — warm and engaging, deep, with a "
     "natural cadence. A real ElevenLabs library voice."),
    ("mexican", "mexican", "ash", "male", "Tony",
     "lRf3yb6jZby4fn3q3Q7M",
     "Tony — witty and upbeat, a native Mexican speaker. "
     "A real ElevenLabs library voice."),
    ("mexican", "mexican", "ballad", "male",
     "Enrique Mondragón", "iDEmt5MnqUotdwCIVplo",
     "Enrique Mondragón — elegant and dynamic, a calm "
     "Mexican accent. A real ElevenLabs library voice."),
    ("mexican", "mexican", "verse", "male", "Adriel",
     "TNuNcwk4LzbPpi1XEANc",
     "Adriel — a deep storyteller: warm and "
     "authoritative. A real ElevenLabs library voice."),
    ("russian", "russian", "marin", "female", "Dasha",
     "8HSRAwEWAAa6wv9cdi5S",
     "Dasha — warm, serious and smooth. A real "
     "ElevenLabs library voice."),
    ("russian", "russian", "coral", "female", "Oksana",
     "Re06WujjJplgPJjn7M6s",
     "Oksana — Russian-accented English, plain and "
     "direct. A real ElevenLabs library voice."),
    ("russian", "russian", "nova", "female", "Betty",
     "8OkSxYl0qQH1Kg3Qzxu2",
     "Betty — warm and smoky, a strong Russian accent, "
     "slightly husky. A real ElevenLabs library voice."),
    ("russian", "russian", "sage", "female", "Natasha",
     "rl5Q2u6U81iTG1BKztyX",
     "Natasha — confident and polished. A real "
     "ElevenLabs library voice."),
    ("russian", "russian", "shimmer", "female", "Tatyana",
     "J60xcCIM7ET7HMi7hMZu",
     "Tatyana — firm, direct, a little hard-edged. A "
     "real ElevenLabs library voice."),
    ("russian", "russian", "onyx", "male", "Alexey",
     "XaEUesE01wKIKaa0xI0h",
     "Alexey — English with a Russian accent. A real "
     "ElevenLabs library voice."),
    ("russian", "russian", "cedar", "male", "Maxim",
     "IGRoDkfYV2Frq7Ly0PRu",
     "Maxim — a strong Russian accent, baritone. A real "
     "ElevenLabs library voice."),
    ("russian", "russian", "fable", "male", "Viktor",
     "Xh5OictnmgRO4dff7pLm",
     "Viktor — bold, Slavic and impatient. A real "
     "ElevenLabs library voice."),
    ("russian", "russian", "ash", "male", "Vlad",
     "XjdmlV0OFXfXE6Mg2Sb7",
     "Vlad — mysterious and profound, a character "
     "voice. A real ElevenLabs library voice."),
    ("russian", "russian", "verse", "male", "Maksim",
     "6sXsAlJKKBf265ucBSRt",
     "Maksim — raw, unpolished and deep. A real "
     "ElevenLabs library voice."),
    ("southern", "southern", "marin", "female", "Ms. Walker",
     "DLsHlh26Ugcm6ELvS0qi",
     "Ms. Walker — warm, reassuring and round. A real "
     "ElevenLabs library voice."),
    ("southern", "southern", "coral", "female", "River",
     "wkqe33083YLBV35we7yB",
     "River — charismatic and upbeat, an authentic "
     "Georgia Southern drawl. A real ElevenLabs library "
     "voice."),
    ("southern", "southern", "nova", "female",
     "Kelli LaShae", "Z5JpFCNFIz8Nhe4KEikq",
     "Kelli LaShae — a refined Mississippi drawl. A real "
     "ElevenLabs library voice."),
    ("southern", "southern", "sage", "female", "Josie",
     "TfVjIROhkRShQb9pCFfK",
     "Josie — smooth and sultry. A real ElevenLabs "
     "library voice."),
    ("southern", "southern", "shimmer", "female",
     "Dr. Kendall", "BVA1oNX6xZt6o7QaUwxr",
     "Dr. Kendall — a soft Southern voice, confident. A "
     "real ElevenLabs library voice."),
    ("southern", "southern", "echo", "male", "Havoc",
     "dtVZnErhiiosqofxDzSH",
     "Havoc — a gritty Southern narrator. A real "
     "ElevenLabs library voice."),
    ("southern", "southern", "ballad", "male", "Austin",
     "Bj9UqZbhQsanLzgalpEG",
     "Austin — deep and raspy, a gravelly good ol' boy. "
     "A real ElevenLabs library voice."),
    ("southern", "southern", "cedar", "male", "Jedediah",
     "Cb8NLd0sUB8jI4MW2f9M",
     "Jedediah — inviting and rich, a deep Texas twang. "
     "A real ElevenLabs library voice."),
    ("southern", "southern", "onyx", "male", "John Texas",
     "7FroLDTDG92jPfUW6BlQ",
     "John Texas — deep and smooth, gravelly — a tough "
     "guy. A real ElevenLabs library voice."),
    ("southern", "southern", "fable", "male", "Rhett Sutton",
     "D4xQvkd2SmpDZJ8sEwvA",
     "Rhett Sutton — slow and deep. A real ElevenLabs "
     "library voice."),
    ("sexy", "sexy", "marin", "female", "MoanElisa",
     "RhnjhPth7vQCdC4HFKLp",
     "MoanElisa — sultry, dark and velvety, spicy; a "
     "breathy, moaning style. A real ElevenLabs "
     "library voice."),
    ("sexy", "sexy", "coral", "female", "Serafina",
     "4tRn1lSkEn13EVTuqb0g",
     "Serafina — a sensual temptress, mature. A real "
     "ElevenLabs library voice."),
    ("sexy", "sexy", "nova", "female", "Valerie",
     "7kTHQRfmpeNlB1V4xfvV",
     "Valerie — sultry, seductive and romantic, "
     "husky. A real ElevenLabs library voice."),
    ("sexy", "sexy", "sage", "female", "Raina",
     "WxqqAhUiswIRQNTBz2a5",
     "Raina — seductive, soft and silky, whispery. "
     "A real ElevenLabs library voice."),
    ("sexy", "sexy", "shimmer", "female", "Aida",
     "CiwzbDpaN3pQXjTgx3ML",
     "Aida — sultry, deep and silky. A real "
     "ElevenLabs library voice."),
    ("sexy", "sexy", "alloy", "female", "Alicia",
     "DZ7MjaeWSDB7ixyC0ETX",
     "Alicia — a sensual whisper, ASMR-soft. A real "
     "ElevenLabs library voice."),
    ("sexy", "sexy", "fable", "female", "Jean",
     "eVItLK1UvXctxuaRV2Oq",
     "Jean — an alluring, playful femme fatale. A "
     "real ElevenLabs library voice."),
    ("sexy", "sexy", "echo", "female", "Jessica",
     "LEnmbrrxYsUYS7vsRRwD",
     "Jessica — intimate and sensual, soft. A real "
     "ElevenLabs library voice."),
    ("sexy", "sexy", "ash", "female", "Anna",
     "bv62BmVlrpG0pQegOpuN",
     "Anna — sensual and seductive, soft. A real "
     "ElevenLabs library voice."),
    ("sexy", "sexy", "verse", "female", "Sloane",
     "BAMYoBHLZM7lJgJAmFz0",
     "Sloane — sultry, seductive and calm. A real "
     "ElevenLabs library voice."),
    ("ghetto", "ghetto", "marin", "female", "Aaliyah",
     "T3b0vsQ5dQwMZ5ckOwBk",
     "Aaliyah — urban and confident. A real "
     "ElevenLabs library voice."),
    ("ghetto", "ghetto", "coral", "female", "Bianca",
     "2bk7ULW9HfwvcIbMWod0",
     "Bianca — a sassy city girl. A real ElevenLabs "
     "library voice."),
    ("ghetto", "ghetto", "nova", "female", "Brooklyn",
     "zWoalRDt5TZrmW4ROIA7",
     "Brooklyn — an African American New Yorker: "
     "urban, confident and slang-aware. A real "
     "ElevenLabs library voice."),
    ("ghetto", "ghetto", "sage", "female", "Kesa",
     "A2YXU4WQdM14YT3hVcji",
     "Kesa — Southern, sassy and outspoken. A real "
     "ElevenLabs library voice."),
    ("ghetto", "ghetto", "shimmer", "female", "Nadia",
     "sSP3AkPGxKdmrKBm6ot7",
     "Nadia — Black, confident and articulate. A "
     "real ElevenLabs library voice."),
    ("ghetto", "ghetto", "alloy", "female", "CiCi",
     "fLQhkOW7F9KVKAjYCbhr",
     "CiCi — sweet and loving, a Southern homegirl "
     "voice. A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "fable", "female",
     "Bianca K", "GBwcTzPhNA3UxDyL66FN",
     "Bianca K — a Black voice with a little sass. "
     "A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "ash", "female", "Kathie",
     "o9B86nZP8mMLaT5FBEzP",
     "Kathie — sassy and playful, with a spoiled "
     "attitude. A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "verse", "female", "Ivanna",
     "xFPwxsyzPJFEaL61n2ms",
     "Ivanna — a gossip queen: sassy, bold and "
     "playful. A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "ember", "female", "Aerisita",
     "03vEurziQfq3V8WZhQvn",
     "Aerisita — sassy and comedic, over-the-top "
     "emotion. A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "onyx", "male",
     "Ghetto Vocals", "wviQSyxmwqYdSDww6bhE",
     "Ghetto Vocals — filed under that very name: a "
     "calm urban narrative voice. A real ElevenLabs "
     "library voice."),
    ("ghetto", "ghetto", "echo", "male", "Alexander",
     "dYcHQ0F5ki90WgfHiRL8",
     "Alexander — deep, high-energy urban authority. "
     "A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "cedar", "male",
     "Carlos Smith", "TsMnIyjnlVjR3YuH6kw5",
     "Carlos Smith — urban, confident and deep. A "
     "real ElevenLabs library voice."),
    ("ghetto", "ghetto", "bronze", "male", "Jamal",
     "Ybqj6CIlqb6M85s9Bl4n",
     "Jamal — an urban American voice with a street "
     "accent, expressive and confident. A real "
     "ElevenLabs library voice."),
    ("ghetto", "ghetto", "ballad", "male", "Jersey",
     "pZAOODRR3C2t0ITvMXg4",
     "Jersey — a smooth baritone, streetwise with a "
     "touch of grit. A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "gold", "male", "Austin",
     "5qCZ1fypgQ3vDZthoT9B",
     "Austin — chill, with a gritty Detroit edge. A "
     "real ElevenLabs library voice."),
    ("ghetto", "ghetto", "smoke", "male", "Murph",
     "248nvfaZe8BXhKntjmpp",
     "Murph — confident and bold, with attitude. A "
     "real ElevenLabs library voice."),
    ("ghetto", "ghetto", "jet", "male", "Andray",
     "FsK9b8Cv2pGkFUtfpOyM",
     "Andray — friendly, energetic and upbeat. A "
     "real ElevenLabs library voice."),
    ("ghetto", "ghetto", "slate", "male", "Chris O.",
     "2rRUaEOGIGalXrcuLN55",
     "Chris O. — a deep, confident Black broadcaster. "
     "A real ElevenLabs library voice."),
    ("ghetto", "ghetto", "flint", "male",
     "Keylo Nsane", "U0zkfLcDk6nbg75zQcNs",
     "Keylo Nsane — confident and expressive, "
     "laid-back with natural grit. A real ElevenLabs "
     "library voice."),
)


def _pack_styles():
    out = []
    for fam_key, shelf, base, gender, el_name, el_id, \
            note in _PACK_EL:
        label, group = _PACK_SHELVES[shelf]
        out.append({
            "voice": f"{fam_key}-{base}",
            "name": f"{label} — {el_name}",
            "note": note,
            "el_voice_id": el_id,
            "accent": label,
            "gender": gender,
            "group": group,
        })
    return out


PACK = _pack_styles()
ALL_STYLES = STYLES + PACK

VOICE_IDS = [s["voice"] for s in ALL_STYLES]
_BY_ID = {s["voice"]: s for s in ALL_STYLES}

# The Round 37 avatar -> voice map: the DEFAULT each
# face falls back to with no pick stored. One table,
# suite-pinned against app.py + the Unity client.
AVATAR_VOICES = {"og": "onyx", "kane": "echo", "sable": "nova"}
DEFAULT_AVATAR = "og"


def _avatar(avatar) -> str:
    """Normalize a client-supplied avatar id; anything
    unknown is OG (a typo'd avatar must never block a
    pick — the pick is what gets stored)."""
    a = str(avatar or "").strip().lower()
    return a if a in AVATAR_VOICES else DEFAULT_AVATAR


# ===========================================================================
# Durable store (og_permissions pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_voice_data ("
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
            logger.warning(f"Voice store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Voice store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_voice_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Voice DB load failed: {e}")
            raise StoreError("voice store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_voice_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Voice DB save failed: {e}")
            raise StoreError("voice store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


# ===========================================================================
# Reads and writes (the single source every speaking face reads)
# ===========================================================================


def stored_voice(uid: str):
    """The account's explicit pick, or None. A stored
    value that is not a known voice id reads as no
    pick (the store only ever writes valid ids; this
    guards a hand-edited or legacy record). No uid ->
    None (there is no account to read)."""
    if not uid:
        return None
    rec = _get("voice:" + uid)
    if isinstance(rec, dict):
        v = rec.get("voice")
        if v in _BY_ID:
            return v
    return None


def voice_for(uid: str, avatar=None) -> str:
    """The effective voice for one speaking face: the
    account's pick when one is stored, else the
    avatar's own Round 37 voice."""
    pick = stored_voice(uid)
    if pick:
        return pick
    return AVATAR_VOICES[_avatar(avatar)]


def set_voice(uid: str, voice) -> str:
    """Store the account's pick; returns the pick now
    in force (what actually reads back — the R73
    discipline). Unknown id -> writes nothing, returns
    the current effective voice for OG. No uid ->
    nothing written."""
    if voice not in _BY_ID:
        return voice_for(uid)
    if not uid:
        return voice
    _put("voice:" + uid, {"voice": voice})
    return stored_voice(uid) or voice


# ===========================================================================
# Round 107 (B1): the reads moved OFF the event loop.
#
# /voice used to run its sync store read (psycopg through
# og_db's bounds — up to ~15 s on a slow database) INLINE
# on the loop, and the page polls it about every 6 s: any
# database slowdown froze the whole worker for every
# visitor (the diagnosis's #1 stall cause). The handlers
# below now reach the store only through asyncio.to_thread.
# No payload cache: Round 99's law (pinned by the r98
# suite) says a store error answers the honest 503, never
# a shadow answer — a cache would serve exactly that
# during an outage. The thread hop is the cure; the polls
# cost one bounded store read each, like every sibling
# settings route.
# ===========================================================================


def _set_voice_payload(uid: str, voice, avatar) -> dict:
    """POST's one thread hop: store the pick, then build
    the payload from what actually reads back."""
    set_voice(uid, voice)
    return voice_payload(uid, avatar)


def _pack_live() -> bool:
    """Whether the meaner pack can actually speak
    right now — og_voice_lab's pack-only gate (the
    OG_VOICE_PACK_EL_ENABLED flag + the provider key),
    read at payload time. A lazy import: the lab
    module is heavy and this module loads first. Any
    import trouble reads as not-live — the folder then
    paints the pack as not switched on, which is the
    honest failure direction."""
    try:
        import og_voice_lab
        return bool(og_voice_lab.pack_enabled())
    except Exception:
        return False


def _style_row(s, effective, default, pack_live=True):
    """One folder row. Classic rows carry exactly the
    Round 98 fields; pack rows add their group, accent
    and gender so a client can label them honestly
    (the folder paints a header per group), plus an
    "available" mark: False while the pack's
    ElevenLabs lane is not switched on, so the folder
    paints that honestly instead of offering a voice
    that cannot speak."""
    row = {"voice": s["voice"], "name": s["name"],
           "note": s["note"],
           "current": s["voice"] == effective,
           "is_default": s["voice"] == default}
    if "accent" in s:
        row["group"] = s["group"]
        row["accent"] = s["accent"]
        row["gender"] = s["gender"]
        row["available"] = bool(pack_live)
    return row


def voice_payload(uid: str, avatar=None) -> dict:
    """The folder's payload: the effective voice, the
    asking face's avatar + its default, and every
    style row with its current/default marks — all
    read from the one store."""
    av = _avatar(avatar)
    default = AVATAR_VOICES[av]
    pick = stored_voice(uid)
    effective = pick or default
    live = _pack_live()
    return {
        "ok": True,
        "voice": effective,
        "picked": pick,
        "avatar": av,
        "default_voice": default,
        "styles": [_style_row(s, effective, default, live)
                   for s in ALL_STYLES],
    }


def speech_for(voice_id) -> dict:
    """What /tts must do for one voice id. Classics
    (and an id that names no style at all) resolve to
    the shipped OpenAI shape — the model, the voice,
    no instructions — so their upstream body stays
    exactly the Round 37/98 one; the unknown-id case
    is the OG default on the classic model, the
    fallback /tts has always applied to a missing or
    invalid voice field. A PACK id resolves to the
    ElevenLabs lane instead: {"provider":
    "elevenlabs", "voice": <the entry's real
    el_voice_id>}. The /tts route keys on the provider
    tag — a pack id only ever reaches the ElevenLabs
    seam, never the OpenAI body; the two engines are
    never substituted for each other."""
    s = _BY_ID.get(voice_id) if isinstance(voice_id, str) else None
    if s is None:
        return {"model": CLASSIC_MODEL,
                "voice": AVATAR_VOICES[DEFAULT_AVATAR],
                "instructions": None}
    if "el_voice_id" in s:
        return {"provider": "elevenlabs",
                "voice": s["el_voice_id"],
                "model": None, "instructions": None}
    return {"model": CLASSIC_MODEL, "voice": s["voice"],
            "instructions": None}


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's pick
    (tombstone, the og_gps purge form) — a re-created
    account starts at its avatar's default voice."""
    if uid:
        _put("voice:" + uid, None)


# ===========================================================================
# Routes — GET/POST /voice (the R73/R84 contract shape)
# ===========================================================================


def _bad(detail):
    return JSONResponse({"ok": False, "detail": detail},
                        status_code=400)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register_voice_routes(app):

    @app.get("/voice")
    async def voice_get(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            # Signed-in only (the pinned contract). In
            # production the Round 44 gate answers first
            # with this same body; this covers any mount
            # without the gate.
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        # Round 107 (B1): the sync store read runs OFF
        # the event loop (see the note above voice_payload).
        return await asyncio.to_thread(
            voice_payload, uid, request.query_params.get("avatar"))

    @app.post("/voice")
    async def voice_set(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        voice = body.get("voice")
        if voice not in _BY_ID:
            return _bad("unknown voice")
        # Round 107 (B1): the write + rebuild run OFF the
        # loop in one hop; the answer is built from what
        # actually reads back (the R73 discipline).
        return await asyncio.to_thread(
            _set_voice_payload, uid, voice, body.get("avatar"))

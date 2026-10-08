"""
OG utilities pack (Round 17) — the pocket tools: a calculator,
unit conversions (plus live currency), translation, QR codes and
short links. Ships LIVE — no external keys, no accounts.

Wiring is the same app-layer seam as Rounds 3/4/6/9/16:
install_utils_tools wraps the agent's (already lookup/file/maps/
connect/unity-wrapped) detect_intent + web_search hooks. A
tool-phrased ask is parsed from the visitor's raw message at
detect time; when the search hook fires, the utility runs FIRST
and returns grounded results in the {title, body, href} shape both
chat paths format into model context. A miss — the ask isn't a
utility ask, an upstream is down, the visitor is at a cap the
fall-through also respects — returns None and the previous search
runs untouched. General questions ABOUT these topics ("what is a
qr code", "how do exchange rates work") match nothing here and
flow to normal chat exactly as before. Persona files untouched.

The four utilities:

1. CALCULATOR — "calculate 47 * 8.5", "what's 15% of 2,300",
   "sqrt of 144", "(12+8)/4". A SAFE evaluator: the expression is
   parsed with Python's ast and walked against a strict whitelist
   of nodes, operators and math functions — no eval/exec, no
   attribute access, no names outside math constants/functions.
   Magnitude/step guards refuse runaway powers instead of
   computing them. Pure local compute: 0 budget units.

2. CONVERSIONS — length/weight/volume/temperature/speed/area/
   data, both directions ("5 miles in km", "180 lbs to kg",
   "convert 70 degrees F to C"). Local factor tables with exact
   factors; results rounded honestly (up to 4 decimal places, no
   fake precision). Offline: 0 units. CURRENCY rides the keyless
   Frankfurter feed (European Central Bank reference rates) with
   a same-day in-process cache; the answer always names the rate
   date, and a currency answer spends 1 lookup unit (external
   data). A dead feed falls through to the normal lookup chain.

3. TRANSLATION — "translate 'where is the bathroom' to Spanish",
   "how do you say X in French", "what does 'buenos días' mean".
   Rides the keyless MyMemory API; the answer is grounded in the
   translation the API actually returned — never invented. On
   quota/upstream failure the ask falls through to the normal
   lookup path. 1 lookup unit per real translation.

4. QR CODES + SHORT LINKS —
   QR: "make a QR code for <url or text>" renders the PNG LOCALLY
   with segno (pure Python — the payload never leaves the
   server), stores it per-visitor for 24 h and serves it only to
   the owning ogai_uid cookie at GET /utils/qr/<id>.png. Payload
   cap 512 chars (http/https URL or plain text); 20 QRs a day per
   visitor, counted in-module; 0 lookup units.
   Short links: "shorten <url>" validates http/https, refuses
   OG's own /auth, /pro and /s paths, mints an unguessable code
   and stores it (JSON file, or Postgres table og_shortlinks
   when OG_MEMORY_DB_URL is set). GET /s/<code> 302s to the
   target; unknown codes 404. "my short links" lists the
   visitor's own (<=10). Creation is metered by the per-tier
   og_tiers cap kind "shortlink" (free 5 / standard 25 / pro 50
   / blue 100 / blackout 500 per day) via app.py's
   _consume_shortlink; listing/redirects are free.
"""

import ast
import io
import json
import logging
import math
import operator
import os
import re
import secrets
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger(__name__)

UTILS_STORE_DIR = os.getenv("OG_UTILS_STORE_DIR", "utils_store")
_QR_TTL = 24 * 60 * 60          # QR PNGs are kept 24 h
_QR_DAILY_LIMIT = 20            # per visitor, counted in-module
_QR_MAX_PAYLOAD = 512
_SHORTLINK_FILE = "shortlinks_store.json"

_PUBLIC_BASE = os.getenv(
    "OG_PUBLIC_URL", "https://og-ai-service.onrender.com").rstrip("/")


def _pro_url() -> str:
    return _PUBLIC_BASE + "/pro"


def _result(tag: str, title: str, body: str, href: str = "") -> list:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


# ===========================================================================
# 1. CALCULATOR — a whitelist AST evaluator (never eval/exec)
# ===========================================================================

_MAX_EXPR_CHARS = 200
_MAX_STEPS = 200
_MAX_MAGNITUDE = 1e100
_MAX_INT_BITS = 8192


def _cbrt(x: float) -> float:
    if hasattr(math, "cbrt"):
        return math.cbrt(x)
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


_FUNCS = {
    "sqrt": math.sqrt, "cbrt": _cbrt,
    "log": math.log, "ln": math.log, "log2": math.log2,
    "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


class _CalcError(Exception):
    """A refused or uncomputable expression (message is honest)."""


class _Evaluator:
    """Recursive whitelist walker over a parsed expression tree."""

    def __init__(self):
        self.steps = 0

    def run(self, tree) -> float:
        value = self._node(tree.body)
        return self._guard(value)

    def _guard(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _CalcError("that didn't come out to a number")
        if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
            raise _CalcError("that number is way too big to compute")
        if isinstance(value, float) and (math.isnan(value)
                                         or math.isinf(value)
                                         or abs(value) > _MAX_MAGNITUDE):
            raise _CalcError("that number is way too big to compute")
        return value

    def _node(self, node):
        self.steps += 1
        if self.steps > _MAX_STEPS:
            raise _CalcError("that expression is too long to compute")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) \
                    and not isinstance(node.value, bool):
                return node.value
            raise _CalcError("only numbers are allowed in there")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left = self._node(node.left)
            right = self._node(node.right)
            if isinstance(node.op, ast.Pow):
                self._guard_pow(left, right)
            try:
                return self._guard(_BINOPS[type(node.op)](left, right))
            except ZeroDivisionError:
                raise _CalcError("you can't divide by zero")
            except (OverflowError, ValueError):
                raise _CalcError("that number is way too big to compute")
        if isinstance(node, ast.UnaryOp) \
                and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self._node(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FUNCS and not node.keywords:
            args = [self._node(a) for a in node.args]
            fn = _FUNCS[node.func.id]
            if node.func.id == "log" and len(args) == 2:
                if args[0] <= 0 or args[1] <= 0 or args[1] == 1:
                    raise _CalcError("that log doesn't exist")
                return self._guard(math.log(args[0], args[1]))
            if len(args) != 1:
                raise _CalcError("those functions take one number")
            try:
                return self._guard(fn(args[0]))
            except (ValueError, OverflowError):
                raise _CalcError("that doesn't compute — check the input")
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return _CONSTS[node.id]
        raise _CalcError("that isn't plain math I can run")

    @staticmethod
    def _guard_pow(base, exp):
        """Refuse runaway powers BEFORE computing them (9**9**9)."""
        if abs(exp) > 1000:
            raise _CalcError("that power is way too big to compute")
        if isinstance(base, int) and isinstance(exp, int) and exp > 0 \
                and base not in (0, 1, -1):
            digits = exp * math.log10(abs(base))
            if digits > 4000:
                raise _CalcError("that power is way too big to compute")
        if isinstance(base, float) and base > 0 and exp > 0:
            if exp * math.log10(base) > 100:
                raise _CalcError("that power is way too big to compute")


def _normalize_expr(raw: str) -> Optional[str]:
    """Turn a visitor's math phrasing into a parseable expression
    string, or None when it clearly isn't one. Word operators and
    percent forms are rewritten; anything else is left for the AST
    whitelist to judge."""
    expr = str(raw or "").strip().strip("?").strip()
    if not expr or len(expr) > _MAX_EXPR_CHARS:
        return None
    expr = expr.replace("×", "*").replace("✕", "*") \
        .replace("÷", "/").replace("−", "-").replace("^", "**")
    expr = re.sub(r"(?<=\d),(?=\d)", "", expr)  # 2,300 -> 2300
    low = expr.lower()
    # Root prefixes bind the whole remainder: "sqrt of 144" -> sqrt(144).
    for prefix, fn in (("square root of ", "sqrt"),
                       ("cube root of ", "cbrt"),
                       ("sqrt of ", "sqrt"), ("cbrt of ", "cbrt")):
        if low.startswith(prefix):
            expr = f"{fn}({expr[len(prefix):]})"
            low = expr.lower()
            break
    # Percent-of: "15% of 2300" / "15 percent of 2300" -> ((15)/100)*(2300).
    m = re.match(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(.+)$",
                 low, re.S)
    if m:
        expr = f"(({m.group(1)})/100)*({m.group(2)})"
        low = expr
    # Attached percent elsewhere: "2300 * 15%" -> "2300 * ((15)/100)".
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"((\1)/100)", expr)
    low = expr.lower()
    # Word operators.
    low = re.sub(r"\bmultiplied by\b", "*", low)
    low = re.sub(r"\bdivided by\b", "/", low)
    low = re.sub(r"\btimes\b", "*", low)
    low = re.sub(r"\bplus\b", "+", low)
    low = re.sub(r"\bminus\b", "-", low)
    low = re.sub(r"\bmodulo\b", "%", low)
    # "144 squared" / "5 cubed".
    low = re.sub(r"(\d+(?:\.\d+)?|\([^()]*\))\s*squared\b", r"(\1)**2", low)
    low = re.sub(r"(\d+(?:\.\d+)?|\([^()]*\))\s*cubed\b", r"(\1)**3", low)
    return low.strip()


def _evaluate(expr: str):
    """Evaluate a normalized expression. Returns (value, None) or
    (None, honest_error)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None, "that doesn't parse as math"
    try:
        return _Evaluator().run(tree), None
    except _CalcError as e:
        return None, str(e)
    except Exception:
        return None, "that doesn't compute"


def _fmt_num(value) -> str:
    if isinstance(value, int):
        return f"{value:,}" if abs(value) >= 10000 else str(value)
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    text = f"{value:.10g}"
    return text


_CALC_TRIGGER_RE = re.compile(
    r"^\W*(?:please\s+)?(?:calculate|calc|compute|solve|evaluate|"
    r"work out|figure out)\b[:\s]+(.+)$", re.I | re.S)
_WHATIS_RE = re.compile(
    r"^\W*(?:what(?:'s| is)|how much is)\s+(.+?)\s*\??\s*$", re.I | re.S)


def _parse_calc(message: str) -> Optional[Dict]:
    """A calculator job {expr_raw, expr, value|error, explicit}, or
    None. Explicit triggers claim even when the math is broken (so
    OG answers honestly); bare expressions claim only when they
    actually evaluate."""
    text = str(message).strip()
    m = _CALC_TRIGGER_RE.match(text)
    if m:
        expr = _normalize_expr(m.group(1))
        if expr is None:
            return {"kind": "calc", "explicit": True,
                    "expr": m.group(1).strip(), "value": None,
                    "error": "that doesn't parse as math"}
        value, error = _evaluate(expr)
        return {"kind": "calc", "explicit": True, "expr": expr,
                "value": value, "error": error}
    m = _WHATIS_RE.match(text)
    candidates = [m.group(1)] if m else [text]
    for cand in candidates:
        if not any(ch.isdigit() for ch in cand):
            continue
        expr = _normalize_expr(cand)
        if expr is None:
            continue
        # A bare candidate must carry an operator or a function —
        # a lone number or a plain-English question is not a job.
        if not re.search(r"[+\-*/%()]|\*\*|sqrt|cbrt|log|of\b", expr):
            continue
        value, error = _evaluate(expr)
        if error is not None:
            continue  # not really math — leave it to normal chat
        return {"kind": "calc", "explicit": bool(m), "expr": expr,
                "value": value, "error": None}
    return None


def _calc_result(job: Dict) -> list:
    if job.get("error"):
        body = (f"The visitor asked you to calculate "
                f"\"{job.get('expr', '')}\" but it can't be "
                f"computed: {job['error']}. Do NOT invent a number. "
                f"Tell them that plainly, in persona, and invite a "
                f"cleaner version of the expression.")
        return _result("OG-CALC: ERROR", "🧮 Calculator", body)
    pretty = job["expr"].replace("**", "^")
    body = (f"The visitor asked to calculate: {pretty}. The "
            f"computed result is exactly: {pretty} = "
            f"{_fmt_num(job['value'])}. Answer with exactly this "
            f"result — do not recompute it, round it differently, "
            f"or add other numbers.")
    return _result("OG-CALC", "🧮 Calculator", body)


# ===========================================================================
# 2. CONVERSIONS — local factor tables (+ Frankfurter for currency)
# ===========================================================================

# alias -> (dimension, canonical label, factor to the dimension's
# base unit). Temperature is special-cased (affine, not a factor).
_UNITS: Dict[str, tuple] = {}


def _reg_unit(dimension, label, factor, *aliases):
    for alias in aliases:
        _UNITS[alias] = (dimension, label, factor)


# length — base: metre
_reg_unit("length", "millimetres", 0.001, "mm", "millimeter", "millimeters",
          "millimetre", "millimetres")
_reg_unit("length", "centimetres", 0.01, "cm", "centimeter", "centimeters",
          "centimetre", "centimetres")
_reg_unit("length", "metres", 1.0, "m", "meter", "meters", "metre", "metres")
_reg_unit("length", "kilometres", 1000.0, "km", "kilometer", "kilometers",
          "kilometre", "kilometres")
_reg_unit("length", "inches", 0.0254, "in", "inch", "inches")
_reg_unit("length", "feet", 0.3048, "ft", "foot", "feet")
_reg_unit("length", "yards", 0.9144, "yd", "yard", "yards")
_reg_unit("length", "miles", 1609.344, "mi", "mile", "miles")
_reg_unit("length", "nautical miles", 1852.0, "nmi", "nautical mile",
          "nautical miles")
# weight — base: kilogram
_reg_unit("weight", "grams", 0.001, "g", "gram", "grams", "gramme", "grammes")
_reg_unit("weight", "kilograms", 1.0, "kg", "kilo", "kilos", "kilogram",
          "kilograms")
_reg_unit("weight", "ounces", 0.028349523125, "oz", "ounce", "ounces")
_reg_unit("weight", "pounds", 0.45359237, "lb", "lbs", "pound", "pounds")
_reg_unit("weight", "stones", 6.35029318, "stone", "stones")
_reg_unit("weight", "US tons", 907.18474, "ton", "tons", "us ton", "us tons")
_reg_unit("weight", "tonnes", 1000.0, "tonne", "tonnes", "metric ton",
          "metric tons", "metric tonne", "metric tonnes")
# volume — base: litre
_reg_unit("volume", "millilitres", 0.001, "ml", "milliliter", "milliliters",
          "millilitre", "millilitres")
_reg_unit("volume", "litres", 1.0, "l", "liter", "liters", "litre", "litres")
_reg_unit("volume", "cubic metres", 1000.0, "m3", "cubic meter",
          "cubic meters", "cubic metre", "cubic metres")
_reg_unit("volume", "teaspoons", 0.00492892159, "tsp", "teaspoon",
          "teaspoons")
_reg_unit("volume", "tablespoons", 0.0147867648, "tbsp", "tablespoon",
          "tablespoons")
_reg_unit("volume", "fluid ounces", 0.0295735296, "fl oz", "floz",
          "fluid ounce", "fluid ounces")
_reg_unit("volume", "cups", 0.2365882365, "cup", "cups")
_reg_unit("volume", "pints", 0.473176473, "pt", "pint", "pints")
_reg_unit("volume", "quarts", 0.946352946, "qt", "quart", "quarts")
_reg_unit("volume", "gallons", 3.785411784, "gal", "gallon", "gallons")
# temperature — special-cased below
_reg_unit("temperature", "degrees Celsius", "C", "c", "celsius", "°c",
          "degrees c", "degrees celsius", "centigrade")
_reg_unit("temperature", "degrees Fahrenheit", "F", "f", "fahrenheit",
          "°f", "degrees f", "degrees fahrenheit")
_reg_unit("temperature", "kelvin", "K", "k", "kelvin", "kelvins")
# speed — base: metres/second
_reg_unit("speed", "metres per second", 1.0, "m/s", "mps", "meters per second",
          "metres per second")
_reg_unit("speed", "kilometres per hour", 1.0 / 3.6, "km/h", "kmh", "kph",
          "kilometers per hour", "kilometres per hour")
_reg_unit("speed", "miles per hour", 0.44704, "mph", "miles per hour")
_reg_unit("speed", "knots", 0.514444444, "knot", "knots", "kn")
# area — base: square metre
_reg_unit("area", "square metres", 1.0, "m2", "sq m", "square meter",
          "square meters", "square metre", "square metres")
_reg_unit("area", "square kilometres", 1e6, "km2", "sq km", "square kilometer",
          "square kilometers", "square kilometre", "square kilometres")
_reg_unit("area", "hectares", 10000.0, "ha", "hectare", "hectares")
_reg_unit("area", "square feet", 0.09290304, "ft2", "sq ft", "square foot",
          "square feet")
_reg_unit("area", "square miles", 2589988.11, "mi2", "sq mi", "square mile",
          "square miles")
_reg_unit("area", "acres", 4046.8564224, "acre", "acres")
# data — base: byte (decimal SI by default, binary aliases explicit)
_reg_unit("data", "bits", 0.125, "bit", "bits")
_reg_unit("data", "bytes", 1.0, "b", "byte", "bytes")
_reg_unit("data", "kilobytes", 1000.0, "kb", "kilobyte", "kilobytes")
_reg_unit("data", "megabytes", 1e6, "mb", "megabyte", "megabytes")
_reg_unit("data", "gigabytes", 1e9, "gb", "gigabyte", "gigabytes")
_reg_unit("data", "terabytes", 1e12, "tb", "terabyte", "terabytes")
_reg_unit("data", "kibibytes", 1024.0, "kib", "kibibyte", "kibibytes")
_reg_unit("data", "mebibytes", 1048576.0, "mib", "mebibyte", "mebibytes")
_reg_unit("data", "gibibytes", 1073741824.0, "gib", "gibibyte", "gibibytes")
_reg_unit("data", "tebibytes", 1099511627776.0, "tib", "tebibyte",
          "tebibytes")

_UNIT_ALT = "|".join(re.escape(a) for a in
                     sorted(_UNITS, key=len, reverse=True))
_NUM = r"(-?\d[\d,]*(?:\.\d+)?)"
_CONV_A_RE = re.compile(
    rf"(?:convert|change|turn)?\s*{_NUM}\s*({_UNIT_ALT})\s+"
    rf"(?:to|into|in|=)\s+({_UNIT_ALT})\b", re.I)
_CONV_HOWMANY_RE = re.compile(
    rf"how many\s+({_UNIT_ALT})\s+(?:are\s+)?(?:in|is in)\s+{_NUM}\s*"
    rf"({_UNIT_ALT})\b", re.I)
_CONV_HOWMANY2_RE = re.compile(
    rf"how many\s+({_UNIT_ALT})\s+(?:is|are)\s+{_NUM}\s*({_UNIT_ALT})\b",
    re.I)


def _to_celsius(value: float, scale: str) -> float:
    if scale == "C":
        return value
    if scale == "F":
        return (value - 32.0) * 5.0 / 9.0
    return value - 273.15  # K


def _from_celsius(value: float, scale: str) -> float:
    if scale == "C":
        return value
    if scale == "F":
        return value * 9.0 / 5.0 + 32.0
    return value + 273.15  # K


def _fmt_conv(value: float) -> str:
    """Honest rounding: integers stay integers; otherwise at most
    4 decimal places (4 significant figures for tiny values) — no
    fake precision beyond the exact factors."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    rounded = round(value, 4)
    if rounded != 0 and abs(rounded) >= 0.0001:
        return f"{rounded:.4f}".rstrip("0").rstrip(".")
    return f"{value:.4g}"


def _fmt_amount(raw: str) -> str:
    value = float(raw.replace(",", ""))
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.4g}"


def _convert(job: Dict):
    """Run a unit conversion. Returns (display, factor_note) or
    None when the units don't share a dimension."""
    amount = float(job["amount"].replace(",", ""))
    dim1, label1, f1 = _UNITS[job["u1"]]
    dim2, label2, f2 = _UNITS[job["u2"]]
    if dim1 != dim2:
        return None
    if dim1 == "temperature":
        result = _from_celsius(_to_celsius(amount, f1), f2)
        note = "temperature converts by formula, not a fixed factor"
    else:
        result = amount * f1 / f2
        note = (f"1 {label1[:-1] if label1.endswith('s') else label1} = "
                f"{_fmt_conv(f1 / f2)} {label2}")
    display = (f"{_fmt_amount(job['amount'])} {label1} = "
               f"{_fmt_conv(result)} {label2}")
    return display, note


# --- Currency (Frankfurter / ECB reference rates) ---------------------------

_CURRENCIES = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "buck": "USD",
    "bucks": "USD", "us dollar": "USD", "us dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "british pound": "GBP", "british pounds": "GBP",
    "jpy": "JPY", "yen": "JPY", "japanese yen": "JPY",
    "cny": "CNY", "yuan": "CNY", "rmb": "CNY", "chinese yuan": "CNY",
    "chf": "CHF", "franc": "CHF", "francs": "CHF", "swiss franc": "CHF",
    "cad": "CAD", "canadian dollar": "CAD", "canadian dollars": "CAD",
    "aud": "AUD", "australian dollar": "AUD",
    "australian dollars": "AUD",
    "nzd": "NZD", "new zealand dollar": "NZD",
    "sek": "SEK", "swedish krona": "SEK",
    "nok": "NOK", "norwegian krone": "NOK",
    "dkk": "DKK", "danish krone": "DKK",
    "pln": "PLN", "zloty": "PLN", "polish zloty": "PLN",
    "mxn": "MXN", "mexican peso": "MXN", "mexican pesos": "MXN",
    "brl": "BRL", "real": "BRL", "brazilian real": "BRL",
    "inr": "INR", "rupee": "INR", "rupees": "INR", "indian rupee": "INR",
    "krw": "KRW", "won": "KRW", "south korean won": "KRW",
    "zar": "ZAR", "rand": "ZAR", "south african rand": "ZAR",
    "aed": "AED", "dirham": "AED", "dirhams": "AED",
    "sgd": "SGD", "singapore dollar": "SGD",
    "hkd": "HKD", "hong kong dollar": "HKD",
    "try": "TRY", "lira": "TRY", "turkish lira": "TRY",
    "thb": "THB", "baht": "THB", "thai baht": "THB",
    "idr": "IDR", "rupiah": "IDR",
    "php": "PHP", "philippine peso": "PHP",
    "czk": "CZK", "czech koruna": "CZK",
    "huf": "HUF", "forint": "HUF",
    "ils": "ILS", "shekel": "ILS", "shekels": "ILS",
    "isk": "ISK", "icelandic krona": "ISK",
    "ron": "RON", "leu": "RON", "romanian leu": "RON",
    "bgn": "BGN", "lev": "BGN", "bulgarian lev": "BGN",
    "hrk": "HRK",
}
_CUR_ALT = "|".join(re.escape(a) for a in
                    sorted(_CURRENCIES, key=len, reverse=True))
_CUR_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CONV_CUR_RE = re.compile(
    rf"(?:convert|change|turn)?\s*{_NUM}\s*({_CUR_ALT})\s+"
    rf"(?:to|into|in)\s+({_CUR_ALT})\b", re.I)
_CONV_CUR_SYM_RE = re.compile(
    rf"([$€£¥])\s*{_NUM}\s+(?:to|into|in)\s+({_CUR_ALT})\b", re.I)

_rate_cache: Dict[tuple, tuple] = {}   # (frm, to) -> (day, rate, date)
_rate_lock = threading.Lock()


def _fetch_rate(frm: str, to: str):
    """Today's ECB reference rate frm->to via Frankfurter, cached
    per UTC day. Returns (rate, rate_date) or None on any failure."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _rate_lock:
        hit = _rate_cache.get((frm, to))
    if hit and hit[0] == today:
        return hit[1], hit[2]
    import httpx
    try:
        with httpx.Client(timeout=8, follow_redirects=True) as client:
            resp = client.get(
                "https://api.frankfurter.dev/v1/latest",
                params={"from": frm, "to": to})
        if resp.status_code != 200:
            logger.warning(
                f"Frankfurter status: {resp.status_code}")
            return None
        data = resp.json()
        rate = float((data.get("rates") or {})[to])
        rate_date = str(data.get("date") or today)
    except Exception as e:
        logger.warning(f"Frankfurter fetch failed: {e}")
        return None
    with _rate_lock:
        _rate_cache[(frm, to)] = (today, rate, rate_date)
    return rate, rate_date


def _parse_convert(message: str) -> Optional[Dict]:
    """A conversion job {kind: convert|currency, ...} or None."""
    text = " " + re.sub(r"\s+", " ", str(message).strip()) + " "
    m = _CONV_CUR_SYM_RE.search(text)
    if m:
        return {"kind": "currency", "amount": m.group(2),
                "c1": _CUR_SYMBOLS[m.group(1)],
                "c2": _CURRENCIES[m.group(3).lower()]}
    m = _CONV_CUR_RE.search(text)
    if m:
        return {"kind": "currency", "amount": m.group(1),
                "c1": _CURRENCIES[m.group(2).lower()],
                "c2": _CURRENCIES[m.group(3).lower()]}
    m = _CONV_A_RE.search(text)
    if m:
        return {"kind": "convert", "amount": m.group(1),
                "u1": m.group(2).lower(), "u2": m.group(3).lower()}
    m = _CONV_HOWMANY_RE.search(text) or _CONV_HOWMANY2_RE.search(text)
    if m:
        # how-many flips the sides: "how many km in 5 miles" asks
        # for the count of u2 inside amount of u1.
        return {"kind": "convert", "amount": m.group(2),
                "u1": m.group(3).lower(), "u2": m.group(1).lower()}
    return None


def _convert_result(job: Dict) -> Optional[list]:
    got = _convert(job)
    if got is None:
        dim1 = _UNITS[job["u1"]][0]
        dim2 = _UNITS[job["u2"]][0]
        body = (f"The visitor asked to convert {_UNITS[job['u1']][1]} "
                f"to {_UNITS[job['u2']][1]}, but those measure "
                f"different things ({dim1} vs {dim2}) — it can't be "
                f"done. Tell them that plainly, in persona, no "
                f"invented number.")
        return _result("OG-CONVERT: MISMATCH", "🔁 Conversions", body)
    display, note = got
    body = (f"The visitor asked for a conversion. The exact "
            f"converted result is: {display} ({note}). Answer with "
            f"exactly this result — do not recompute it or add "
            f"other numbers.")
    return _result("OG-CONVERT", "🔁 Conversions", body)


def _currency_result(job: Dict, uid: str, consume_lookup) -> Optional[list]:
    amount = float(job["amount"].replace(",", ""))
    frm, to = job["c1"], job["c2"]
    if frm == to:
        display = (f"{_fmt_amount(job['amount'])} {frm} = "
                   f"{_fmt_amount(job['amount'])} {to} (same currency)")
        body = (f"The visitor asked to convert a currency into "
                f"itself. Result: {display}. Answer with exactly "
                f"this, in persona — no rate needed.")
        return _result("OG-CONVERT: CURRENCY", "🔁 Currency", body)
    got = _fetch_rate(frm, to)
    if got is None:
        return None  # feed down — fall through to the lookup chain
    rate, rate_date = got
    result = amount * rate
    if uid and consume_lookup is not None:
        try:
            if not consume_lookup(uid):
                logger.info("Currency answer skipped: visitor at "
                            "daily lookup cap")
                return None
        except Exception as e:
            logger.warning(f"Currency budget consume failed: {e}")
            return None
    body = (f"The visitor asked to convert currency. Live rate: "
            f"1 {frm} = {rate:.6g} {to} — European Central Bank "
            f"reference rate dated {rate_date}, via Frankfurter. "
            f"The converted result is exactly: "
            f"{_fmt_amount(job['amount'])} {frm} = "
            f"{_fmt_conv(result)} {to}. Answer with exactly this "
            f"result and name the rate date ({rate_date}) — do not "
            f"recompute it or use any other rate.")
    return _result("OG-CONVERT: CURRENCY", "🔁 Currency", body)


# ===========================================================================
# 3. TRANSLATION — MyMemory (keyless), grounded or fall-through
# ===========================================================================

_LANGS = {
    "english": "en", "spanish": "es", "french": "fr", "german": "de",
    "italian": "it", "portuguese": "pt", "dutch": "nl",
    "russian": "ru", "ukrainian": "uk", "polish": "pl",
    "swedish": "sv", "norwegian": "no", "danish": "da",
    "finnish": "fi", "greek": "el", "czech": "cs", "slovak": "sk",
    "romanian": "ro", "hungarian": "hu", "bulgarian": "bg",
    "croatian": "hr", "serbian": "sr", "bosnian": "bs",
    "slovenian": "sl", "estonian": "et", "latvian": "lv",
    "lithuanian": "lt", "turkish": "tr", "arabic": "ar",
    "hebrew": "he", "persian": "fa", "farsi": "fa", "urdu": "ur",
    "hindi": "hi", "bengali": "bn", "japanese": "ja",
    "korean": "ko", "chinese": "zh-CN", "mandarin": "zh-CN",
    "vietnamese": "vi", "thai": "th", "indonesian": "id",
    "malay": "ms", "tagalog": "tl", "filipino": "tl",
    "swahili": "sw", "latin": "la", "esperanto": "eo",
    "irish": "ga", "welsh": "cy", "catalan": "ca", "basque": "eu",
    "galician": "gl", "icelandic": "is", "maltese": "mt",
    "albanian": "sq", "macedonian": "mk", "belarusian": "be",
    "georgian": "ka", "armenian": "hy", "azerbaijani": "az",
    "kazakh": "kk", "uzbek": "uz", "tamil": "ta", "telugu": "te",
    "marathi": "mr", "punjabi": "pa", "gujarati": "gu",
    "kannada": "kn", "malayalam": "ml", "nepali": "ne",
    "sinhala": "si", "khmer": "km", "lao": "lo", "burmese": "my",
    "mongolian": "mn", "amharic": "am", "somali": "so",
    "yoruba": "yo", "igbo": "ig", "hausa": "ha", "zulu": "zu",
}
_LANG_CODES = set(_LANGS.values())
_LANG_NAME_BY_CODE = {}
for _name, _code in _LANGS.items():
    _LANG_NAME_BY_CODE.setdefault(_code, _name)

_TRANS_MAX_CHARS = 500
_TRANS_TO_RE = re.compile(
    r"^\W*(?:please\s+)?translate\s+(.+?)\s+(?:in)?to\s+"
    r"([a-z][a-z \-]{1,24}?)\s*[.!?]?\s*$", re.I | re.S)
_TRANS_SAY_RE = re.compile(
    r"^\W*(?:how (?:do you|to) say|say)\s+(.+?)\s+in\s+"
    r"([a-z][a-z \-]{1,24}?)\s*[.!?]?\s*$", re.I | re.S)
_TRANS_MEAN_RE = re.compile(
    r"^\W*what does\s+(.+?)\s+mean(?:\s+in\s+([a-z][a-z \-]{1,24}?))?"
    r"\s*\??\s*$", re.I | re.S)


def _lang_code(name: str) -> Optional[str]:
    key = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if key in _LANGS:
        return _LANGS[key]
    if key in _LANG_CODES:
        return key
    return None


def _strip_quotes(text: str) -> str:
    text = str(text or "").strip()
    for a, b in (("'", "'"), ('"', '"'), ("“", "”"), ("‘", "’")):
        if len(text) >= 2 and text.startswith(a) and text.endswith(b):
            return text[1:-1].strip()
    return text


def _parse_translate(message: str) -> Optional[Dict]:
    """A translation job {text, src ('en'|'autodetect'), tgt,
    tgt_name}, or None. Claims only tool-phrased asks with a
    resolvable target language."""
    text = str(message).strip()
    m = _TRANS_TO_RE.match(text)
    if m:
        tgt = _lang_code(m.group(2))
        if tgt:
            return {"kind": "translate",
                    "text": _strip_quotes(m.group(1)),
                    "src": "autodetect", "tgt": tgt,
                    "tgt_name": _LANG_NAME_BY_CODE.get(tgt, tgt)}
        return None
    m = _TRANS_SAY_RE.match(text)
    if m:
        tgt = _lang_code(m.group(2))
        if tgt:
            return {"kind": "translate",
                    "text": _strip_quotes(m.group(1)),
                    "src": "en", "tgt": tgt,
                    "tgt_name": _LANG_NAME_BY_CODE.get(tgt, tgt)}
        return None
    m = _TRANS_MEAN_RE.match(text)
    if m:
        raw = m.group(1)
        tgt = _lang_code(m.group(2)) if m.group(2) else "en"
        if not tgt:
            return None
        word = re.match(r"(?:the\s+)?([a-z]+)\s+(?:word|phrase)\s+"
                        r"(.+)$", raw.strip(), re.I)
        if word and _lang_code(word.group(1)):
            return {"kind": "translate",
                    "text": _strip_quotes(word.group(2)),
                    "src": _lang_code(word.group(1)), "tgt": tgt,
                    "tgt_name": _LANG_NAME_BY_CODE.get(tgt, tgt)}
        quoted = raw.strip()[:1] in ("'", '"', "“", "‘")
        # An unquoted, language-less "what does X mean" is claimed
        # only when X carries non-ASCII letters (a foreign phrase)
        # — plain-English questions stay normal chat.
        if not quoted and not m.group(2) \
                and not re.search(r"[^\x00-\x7F]", raw):
            return None
        return {"kind": "translate", "text": _strip_quotes(raw),
                "src": "autodetect", "tgt": tgt,
                "tgt_name": _LANG_NAME_BY_CODE.get(tgt, tgt)}
    return None


def _mymemory_translate(text: str, src: str, tgt: str) -> Optional[str]:
    """One MyMemory translation, or None on quota/upstream/parse
    failure (the caller falls through to the lookup chain)."""
    import httpx
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": f"{src}|{tgt}"})
        if resp.status_code != 200:
            logger.warning(
                f"MyMemory status: {resp.status_code}")
            return None
        data = resp.json()
    except Exception as e:
        logger.warning(f"MyMemory fetch failed: {e}")
        return None
    try:
        if str(data.get("responseStatus")) != "200" \
                or data.get("quotaFinished"):
            return None
        translated = str(
            (data.get("responseData") or {}).get("translatedText")
            or "").strip()
    except Exception:
        return None
    if not translated or "MYMEMORY WARNING" in translated.upper() \
            or "QUERY LENGTH LIMIT" in translated.upper() \
            or "NO QUERY SPECIFIED" in translated.upper() \
            or "INVALID EMAIL" in translated.upper():
        return None
    return translated


def _translate_result(job: Dict, uid: str, consume_lookup) \
        -> Optional[list]:
    text = job["text"]
    if not text:
        return None
    if len(text) > _TRANS_MAX_CHARS:
        body = (f"The visitor asked you to translate a text of "
                f"{len(text)} characters, but the translation "
                f"tool takes at most {_TRANS_MAX_CHARS} characters "
                f"at a time. Tell them that plainly, in persona, "
                f"and invite them to send it in shorter pieces. "
                f"Do NOT translate it yourself.")
        return _result("OG-TRANSLATE: TOO-LONG", "🌐 Translation",
                       body)
    translated = _mymemory_translate(text, job["src"], job["tgt"])
    if translated is None:
        return None  # quota/upstream — honest fall-through
    if uid and consume_lookup is not None:
        try:
            if not consume_lookup(uid):
                logger.info("Translation skipped: visitor at daily "
                            "lookup cap")
                return None
        except Exception as e:
            logger.warning(f"Translation budget consume failed: {e}")
            return None
    body = (f"The visitor asked for a translation into "
            f"{job['tgt_name']}. The translation service returned "
            f"exactly this: \"{translated}\" — for their text: "
            f"\"{text}\". Give them exactly that translation — do "
            f"not re-translate it yourself or offer alternatives "
            f"as if they were the translation.")
    return _result("OG-TRANSLATE", "🌐 Translation", body)


# ===========================================================================
# 4a. QR CODES — segno, generated locally; the payload never leaves
# ===========================================================================

_utils_lock = threading.Lock()


def _utils_dir() -> str:
    os.makedirs(UTILS_STORE_DIR, exist_ok=True)
    return UTILS_STORE_DIR


def _qr_usage_path() -> str:
    return os.path.join(_utils_dir(), "qr_usage.json")


def _qr_used_today(uid: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(_qr_usage_path()) as f:
            store = json.load(f)
    except Exception:
        return 0
    entry = (store or {}).get(uid)
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))


def _qr_record_use(uid: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _qr_usage_path()
    with _utils_lock:
        try:
            with open(path) as f:
                store = json.load(f)
            if not isinstance(store, dict):
                store = {}
        except Exception:
            store = {}
        entry = store.get(uid)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        entry["count"] = int(entry.get("count", 0)) + 1
        store[uid] = entry
        try:
            with open(path, "w") as f:
                json.dump(store, f)
        except Exception as e:
            logger.warning(f"QR usage save failed: {e}")


def _sweep_old_qrs():
    try:
        now = time.time()
        for fname in os.listdir(UTILS_STORE_DIR):
            if not (fname.startswith("qr_")
                    and fname.endswith((".png", ".json"))):
                continue
            fpath = os.path.join(UTILS_STORE_DIR, fname)
            if now - os.path.getmtime(fpath) > _QR_TTL:
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    except OSError:
        pass


_QR_RES = (
    re.compile(r"(?:make|create|generate|give me|get me)\s+"
               r"(?:a\s+|an\s+|me\s+a\s+)?qr\s*code\s*"
               r"(?:for|of|with|to)?\s*:?\s*(.+)$", re.I | re.S),
    re.compile(r"qr\s*code\s*[:\-]\s*(.+)$", re.I | re.S),
    re.compile(r"qr\s*code\s+for\s+(.+)$", re.I | re.S),
)


def _parse_qr(message: str) -> Optional[Dict]:
    text = str(message).strip()
    for pattern in _QR_RES:
        m = pattern.search(text)
        if m:
            payload = _strip_quotes(m.group(1).strip().rstrip("."))
            if payload:
                return {"kind": "qr", "payload": payload}
    return None


def _qr_payload_final(payload: str) -> str:
    """A bare domain the visitor clearly means as a link gets its
    scheme; anything else stays exactly the text they gave."""
    if re.match(r"^https?://", payload, re.I):
        return payload
    if " " not in payload and re.fullmatch(
            r"[\w\-]+(\.[\w\-]+)+(/\S*)?", payload):
        return "https://" + payload
    return payload


def _qr_result(job: Dict, uid: str) -> list:
    payload = _qr_payload_final(job["payload"])
    if len(payload) > _QR_MAX_PAYLOAD:
        body = (f"The visitor asked for a QR code for a payload of "
                f"{len(payload)} characters, but QR payloads here "
                f"cap at {_QR_MAX_PAYLOAD} characters. Tell them "
                f"that plainly, in persona, and invite a shorter "
                f"link or text. No QR was made.")
        return _result("OG-QR: TOO-LONG", "🔳 QR code", body)
    if not uid:
        return None
    if _qr_used_today(uid) >= _QR_DAILY_LIMIT:
        body = (f"The visitor asked for a QR code but they've made "
                f"{_QR_DAILY_LIMIT} today — that's the daily limit "
                f"for QR codes. Tell them plainly, in persona, that "
                f"it resets tomorrow. No QR was made.")
        return _result("OG-QR: CAP", "🔳 QR code", body)
    try:
        import segno
        qr = segno.make(payload, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=8, border=2)
        png = buf.getvalue()
    except Exception as e:
        logger.warning(f"QR generation failed: {e}")
        return None
    try:
        _utils_dir()
        _sweep_old_qrs()
        qr_id = uuid.uuid4().hex
        with open(os.path.join(UTILS_STORE_DIR, f"qr_{qr_id}.png"),
                  "wb") as f:
            f.write(png)
        meta = {"uid": uid, "payload": payload,
                "created": datetime.now(timezone.utc).isoformat()}
        with open(os.path.join(UTILS_STORE_DIR, f"qr_{qr_id}.json"),
                  "w") as f:
            json.dump(meta, f)
    except Exception as e:
        logger.warning(f"QR store failed: {e}")
        return None
    _qr_record_use(uid)
    link = f"{_PUBLIC_BASE}/utils/qr/{qr_id}.png"
    body = (f"DONE — the QR code is generated. Its link is {link} "
            f"— give them that EXACT full URL, unchanged (do not "
            f"shorten it, do not swap the domain), in persona. The "
            f"QR encodes exactly this: \"{payload}\". The link "
            f"works for 24 hours and only from their browser.")
    return _result("OG-QR", "🔳 QR code", body, link)


# ===========================================================================
# 4b. SHORT LINKS — per-visitor codes; /s/<code> redirects
# ===========================================================================

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend unavailable
    psycopg = None
    _Jsonb = None

_short_lock = threading.Lock()
_CODE_ALPHABET = string.ascii_letters + string.digits


def _short_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_shortlinks ("
            "code TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_shortlinks() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _short_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT code, data FROM og_shortlinks")
                    return {code: data for code, data in cur.fetchall()}
        except Exception as e:
            logger.warning(
                f"Shortlink store DB load failed, using file: {e}")
    if os.path.exists(_SHORTLINK_FILE):
        try:
            with open(_SHORTLINK_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load shortlink store: {e}")
    return {}


def _save_shortlinks(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _short_db_connect() as conn:
                with conn.cursor() as cur:
                    for code, data in store.items():
                        cur.execute(
                            "INSERT INTO og_shortlinks (code, data) "
                            "VALUES (%s, %s) ON CONFLICT (code) "
                            "DO UPDATE SET data = EXCLUDED.data",
                            (code, _Jsonb(data)))
                    cur.execute("SELECT code FROM og_shortlinks")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_shortlinks WHERE code = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(
                f"Shortlink store DB save failed, using file: {e}")
    try:
        with open(_SHORTLINK_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save shortlink store: {e}")


_SHORTEN_RES = (
    re.compile(r"shorten\s+(?:this\s+)?(\S+)", re.I),
    re.compile(r"(?:make|create|give me)\s+(?:a\s+)?short\s*"
               r"(?:link|url)\s*(?:for|to)?\s*:?\s*(https?://\S+)",
               re.I),
    re.compile(r"short\s*(?:link|url)\s*(?:for|to)?\s*:?\s*"
               r"(https?://\S+)", re.I),
)
_MY_LINKS_RE = re.compile(r"\bmy short ?links\b", re.I)
_URL_TRAILING = ".,;:!?)]}\"'’”"


def _clean_url(raw: str) -> Optional[str]:
    """A complete http/https link, or None. A bare domain with a
    path (example.com/page) gets its https:// — that's plainly
    what the visitor means; anything else is not a link."""
    url = str(raw or "").strip().strip("<>").rstrip(_URL_TRAILING)
    if not re.match(r"^https?://", url, re.I) and re.fullmatch(
            r"[\w\-]+(\.[\w\-]+)+(/\S*)?", url):
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _is_blocked_own_path(url: str) -> bool:
    """Never shorten OG's own /auth, /pro or /s paths (sign-in and
    payment surfaces, and no redirect loops)."""
    try:
        parsed = urlparse(url)
        own = urlparse(_PUBLIC_BASE)
    except Exception:
        return False
    if (parsed.hostname or "").lower() != (own.hostname or "").lower():
        return False
    path = (parsed.path or "").lower()
    return path.startswith(("/auth", "/pro", "/s/")) or path == "/s"


def _parse_shorten(message: str) -> Optional[Dict]:
    text = str(message)
    if _MY_LINKS_RE.search(text):
        return {"kind": "my_links"}
    for pattern in _SHORTEN_RES:
        m = pattern.search(text)
        if m:
            url = _clean_url(m.group(1))
            if url:
                return {"kind": "shorten", "url": url}
            return {"kind": "shorten_bad"}
    return None


def _shorten_result(job: Dict, uid: str, consume_shortlink) -> list:
    url = job["url"]
    if _is_blocked_own_path(url):
        body = (f"The visitor asked to shorten {url} — that's one "
                f"of OG's own sign-in/payment/link paths, and those "
                f"are never shortened. Tell them that plainly, in "
                f"persona. No link was made.")
        return _result("OG-SHORTLINK: REFUSED", "🔗 Short links",
                       body)
    if not uid:
        return None
    with _short_lock:
        store = _load_shortlinks()
        code = None
        for _ in range(5):
            candidate = "".join(
                secrets.choice(_CODE_ALPHABET) for _ in range(7))
            if candidate not in store:
                code = candidate
                break
        if code is None:
            return None
        store[code] = {
            "url": url, "uid": uid,
            "created": datetime.now(timezone.utc).isoformat()}
        _save_shortlinks(store)
    spent = True
    if consume_shortlink is not None:
        try:
            spent = bool(consume_shortlink(uid))
        except Exception as e:
            logger.warning(f"Shortlink consume failed: {e}")
            spent = False
    if not spent:
        # Cap raced us — remove the link, charge nothing (the
        # Round 16 rule: a unit is spent only on success).
        with _short_lock:
            store = _load_shortlinks()
            store.pop(code, None)
            _save_shortlinks(store)
        body = (f"The visitor asked to shorten a link but they've "
                f"hit today's short-link limit for their plan. Do "
                f"NOT give them a link — none was kept. Tell them "
                f"plainly, in persona, that the limit resets "
                f"tomorrow, or that higher plans make more: "
                f"{_pro_url()}")
        return _result("OG-SHORTLINK: CAP", "🔗 Short links", body,
                       _pro_url())
    link = f"{_PUBLIC_BASE}/s/{code}"
    body = (f"DONE — the short link is made: {link} — give them "
            f"that EXACT full URL, unchanged, in persona. It "
            f"redirects to {url}. Their links stay listed under "
            f"\"my short links\" anytime they ask.")
    return _result("OG-SHORTLINK", "🔗 Short links", body, link)


def _my_links_result(uid: str) -> list:
    if not uid:
        return None
    with _short_lock:
        store = _load_shortlinks()
    mine = [(rec.get("created", ""), code, rec.get("url", ""))
            for code, rec in store.items()
            if isinstance(rec, dict) and rec.get("uid") == uid]
    mine.sort(reverse=True)
    mine = mine[:10]
    if not mine:
        body = ("The visitor asked for their short links but they "
                "haven't made any yet. Tell them that plainly, in "
                "persona, and that they can just say \"shorten\" "
                "plus a link to make one.")
        return _result("OG-SHORTLINK: LIST", "🔗 Short links", body)
    lines = [f"- {_PUBLIC_BASE}/s/{code} → {url}"
             for _, code, url in mine]
    body = ("The visitor asked for their short links. These are "
            "theirs, newest first — list them exactly like this, "
            "nothing invented:\n\n" + "\n".join(lines))
    return _result("OG-SHORTLINK: LIST", "🔗 Short links", body)


# ===========================================================================
# Intent routing + execution
# ===========================================================================

def parse_utils_intent(message: str) -> Optional[Dict]:
    """Parse a utilities job from the raw message, or None.
    Priority: translation > QR > short links > conversion/
    currency > calculator (the most specific phrasings win)."""
    for parser in (_parse_translate, _parse_qr, _parse_shorten,
                   _parse_convert, _parse_calc):
        try:
            job = parser(message)
        except Exception as e:
            logger.warning(f"Utils parser {parser.__name__} "
                           f"failed: {e}")
            job = None
        if job:
            return job
    return None


def utils_results(job, message, uid, consume_lookup,
                  consume_shortlink):
    """Run one parsed utilities job. Returns web_search-shaped
    results on a handled ask, or None on a true miss (upstream
    down, cap the fall-through also respects) so the caller falls
    through to the previous search untouched. Budget: calculator,
    offline conversions, QR and short-link creation spend 0
    lookup units (short links are metered by their own tier cap);
    currency and translation spend 1 lookup unit per real answer."""
    if not job:
        return None
    kind = job.get("kind", "")
    if kind == "calc":
        return _calc_result(job)
    if kind == "convert":
        return _convert_result(job)
    if kind == "currency":
        return _currency_result(job, uid, consume_lookup)
    if kind == "translate":
        return _translate_result(job, uid, consume_lookup)
    if kind == "qr":
        return _qr_result(job, uid)
    if kind == "shorten":
        return _shorten_result(job, uid, consume_shortlink)
    if kind == "shorten_bad":
        body = ("The visitor asked to shorten something that "
                "isn't a complete http/https link. Tell them "
                "plainly, in persona, and ask for the full link "
                "starting with http:// or https://. No link was "
                "made.")
        return _result("OG-SHORTLINK: BAD", "🔗 Short links", body)
    if kind == "my_links":
        return _my_links_result(uid)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this LAST, after Rounds 3/4/6/9–16)
# ---------------------------------------------------------------------------

# The utilities job parsed for the exchange currently being
# processed. All chat processing is serialized under app.py's
# _memory_lock, so a single slot is safe — the same reasoning as
# app.py's own slots.
_pending = {"job": None}


def install_utils_tools(agent_instance, get_uid, consume_lookup,
                        consume_shortlink):
    """Wrap the agent's (already lookup/file/maps/connect/unity-
    wrapped) detect_intent + web_search hooks so tool-phrased asks
    try og_utils FIRST and fall through to the previous search on
    a miss. get_uid() returns the current visitor's uid;
    consume_lookup(uid) spends one unit of the shared Round 3
    lookup budget (currency + translation answers);
    consume_shortlink(uid) spends one unit of the per-tier
    short-link cap. Claimed jobs also clear needs_code_generation —
    a pocket-tool ask is never a code-generation job. Non-utility
    messages pass through untouched. Persona files never touched."""
    if getattr(agent_instance, "_og_utils_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        try:
            job = parse_utils_intent(str(message))
            if job:
                _pending["job"] = job
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Utils trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                results = utils_results(
                    job, query, get_uid(), consume_lookup,
                    consume_shortlink)
            except Exception as e:
                logger.warning(f"Utils job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_utils_installed = True


# --- Routes (live) -------------------------------------------------------------

def register_utils_routes(app):
    """Attach GET /utils/qr/<id>.png (a visitor's QR image, owner
    only, 24 h) and GET /s/<code> (short-link redirect)."""

    @app.get("/utils/qr/{qr_id}.png")
    async def utils_qr_png(qr_id: str, raw_request: Request):
        if not re.fullmatch(r"[0-9a-f]{32}", qr_id or ""):
            raise HTTPException(status_code=404, detail="Not found")
        ppath = os.path.join(UTILS_STORE_DIR, f"qr_{qr_id}.png")
        mpath = os.path.join(UTILS_STORE_DIR, f"qr_{qr_id}.json")
        if not os.path.exists(ppath) or not os.path.exists(mpath):
            raise HTTPException(status_code=404, detail="Not found")
        if time.time() - os.path.getmtime(ppath) > _QR_TTL:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            with open(mpath) as f:
                meta = json.load(f)
        except Exception:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if not uid or meta.get("uid") != uid:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(ppath, media_type="image/png",
                            filename="qr.png")

    @app.get("/s/{code}")
    async def utils_shortlink(code: str, raw_request: Request):
        if not re.fullmatch(r"[A-Za-z0-9]{7}", code or ""):
            raise HTTPException(status_code=404, detail="Not found")
        with _short_lock:
            store = _load_shortlinks()
        record = store.get(code)
        if not isinstance(record, dict) or not record.get("url"):
            raise HTTPException(status_code=404, detail="Not found")
        return RedirectResponse(url=record["url"], status_code=302)

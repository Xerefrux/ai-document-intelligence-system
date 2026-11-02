"""Turning messy strings into numbers, dates and currency codes, without guessing silently.

When a value can be read two ways ("1,234" or "03/04/2026"), the parser picks the most likely reading
from hints found elsewhere in the same document, and says it was ambiguous so the caller can lower
its confidence and a validation rule can ask a person to check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------- currency

CURRENCY_SYMBOLS: dict[str, str] = {
    "US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "৳": "BDT",
    "₽": "RUB", "₩": "KRW", "₺": "TRY", "₫": "VND", "₱": "PHP", "R$": "BRL", "Tk": "BDT", "Tk.": "BDT",
}
# "$" is also CAD, AUD and others, so it is reported with lower confidence than a printed code.
AMBIGUOUS_SYMBOLS = {"$", "¥"}

ISO_CODES = {
    "USD", "EUR", "GBP", "JPY", "CNY", "INR", "BDT", "AUD", "CAD", "CHF", "SGD", "HKD", "NZD", "SEK", "NOK",
    "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR", "BRL", "MXN", "AED", "SAR", "MYR", "THB", "IDR", "PHP", "KRW",
    "VND", "PKR", "LKR", "NPR", "EGP", "NGN", "KES", "QAR", "KWD", "OMR", "BHD", "ILS", "RUB", "UAH", "RON",
    "BGN", "ARS", "CLP", "COP", "PEN", "TWD", "MMK", "KHR", "GHS", "TZS", "UGX", "ETB", "MAD", "DZD", "JOD",
}

_SYM_PREFIX = r"(?:US\$|R\$|[$€£¥₹৳₽₩₺₫₱]|Tk\.?|Rs\.?|[A-Z]{3})"
_MINUS = "-\u2212\u2013"  # hyphen, the Unicode minus sign and the en dash: all three are used as a minus in documents

_NUM_OK = re.compile(r"^(?:\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:[   ',.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)$")
_CELL_RE = re.compile(
    rf"""^\s*(?P<open>\()?\s*(?P<sign1>[{_MINUS}+])?\s*(?P<pre>{_SYM_PREFIX})?\s*(?P<sign2>[{_MINUS}+])?\s*
         (?P<num>[.,]?\d[\d.,'   ]*)\s*(?P<pct>%)?\s*(?P<bd>/-)?\s*(?P<post>{_SYM_PREFIX})?\s*(?P<trail>[{_MINUS}])?\s*(?P<close>\))?\s*$""",
    re.X,
)
_TOKEN_RE = re.compile(
    rf"""(?<![\w.,/#{_MINUS}])(?P<neg>[{_MINUS}(])?\s?(?P<pre>{_SYM_PREFIX})?\s?
         (?P<num>\d{{1,2}}(?:,\d{{2}})+,\d{{3}}(?:\.\d{{1,4}})?|\d{{1,3}}(?:[,'.]\d{{3}})+(?:[.,]\d{{1,4}})?|\d+(?:[.,]\d{{1,4}})?)
         (?P<pct>\s?%)?(?P<bd>/-)?(?P<post>\s?[A-Z]{{3}})?(?P<close>\))?(?![\w/])""",
    re.X,
)


@dataclass
class Money:
    value: Decimal
    currency: str | None = None
    ambiguous: bool = False
    percent: bool = False
    raw: str = ""

    def as_float(self) -> float:
        return float(round(self.value, 4))


def symbol_to_code(sym: str | None) -> str | None:
    if not sym:
        return None
    sym = sym.strip()
    if sym in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[sym]
    up = sym.upper().rstrip(".")
    if up in ISO_CODES:
        return up
    return None


def _interpret_number(num: str, hint: str | None) -> tuple[Decimal | None, bool]:
    """Read digits with separators. `hint` is the decimal mark this document uses ('.' or ',') when known."""
    s = num.replace(" ", " ").replace(" ", " ").strip()
    s = s.replace(" ", "").replace("'", "")
    if not s or not re.search(r"\d", s):
        return None, False
    dots, commas = s.count("."), s.count(",")
    lakh = re.fullmatch(r"(\d{1,2})((?:,\d{2})+),(\d{3})(\.\d+)?", s)
    if lakh:  # 1,25,000.50 as written in Bangladesh and India
        return Decimal(lakh.group(1) + lakh.group(2).replace(",", "") + lakh.group(3) + (lakh.group(4) or "")), False
    ambiguous = False
    try:
        if dots and commas:
            dec = "." if s.rfind(".") > s.rfind(",") else ","
            thou = "," if dec == "." else "."
            int_part, frac = s.rsplit(dec, 1)
            if dec in int_part:
                return None, False
            groups = int_part.split(thou)
            if not groups[0] or len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:]):
                return None, False
            return Decimal("".join(groups) + "." + frac), False
        if dots or commas:
            sep = "." if dots else ","
            parts = s.split(sep)
            if len(parts) > 2:  # 1,234,567 or 1.234.567
                if len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
                    return Decimal("".join(parts)), False
                return None, False
            head, tail = parts
            if head == "":
                return Decimal("0." + tail), False
            if len(tail) == 3 and 1 <= len(head) <= 3 and not (head.startswith("0") and len(head) > 1) and head != "0":
                # 1,234 or 1.234: thousands or decimals?
                ambiguous = True
                if hint == sep:
                    return Decimal(head + "." + tail), True
                if hint and hint != sep:
                    return Decimal(head + tail), True
                # No hint: comma as thousands, dot as decimals (US, UK and Bangladesh convention).
                return (Decimal(head + tail) if sep == "," else Decimal(head + "." + tail)), True
            return Decimal(head + "." + tail), False
        return Decimal(s), False
    except InvalidOperation:
        return None, False


def parse_amount(text: str, hint: str | None = None) -> Money | None:
    """Parse one cell that holds a single amount, such as '$1,234.50', '(12.00)', '1 234,56 EUR' or '৳5,000/-'."""
    if text is None:
        return None
    m = _CELL_RE.match(text)
    if not m:
        return None
    num = m.group("num")
    if not _NUM_OK.match(num.strip()):
        return None
    value, amb = _interpret_number(num, "." if m.group("bd") else hint)
    if value is None:
        return None
    if m.group("bd"):
        amb = False
    neg = bool(m.group("sign1") or m.group("sign2") or m.group("trail") or (m.group("open") and m.group("close")))
    if neg:
        value = -value
    for affix in (m.group("pre"), m.group("post")):
        if affix and re.fullmatch(r"[A-Z]{3}", affix) and affix not in ISO_CODES:
            return None  # "INV-2025" is a reference, not an amount in a currency called INV
    cur_txt = m.group("pre") or m.group("post")
    cur = symbol_to_code(cur_txt)
    if cur_txt in AMBIGUOUS_SYMBOLS:
        amb = amb  # a "$" is ambiguous as a currency, not as a number
    return Money(value, cur, amb, bool(m.group("pct")), text.strip())


def find_money(text: str, hint: str | None = None) -> list[tuple[Money, int, int]]:
    """Find amounts inside free text. Percentages are returned with percent=True so callers can skip them."""
    out: list[tuple[Money, int, int]] = []
    for m in _TOKEN_RE.finditer(text):
        value, amb = _interpret_number(m.group("num"), hint)
        if value is None:
            continue
        is_pct = bool(m.group("pct"))
        neg = (not is_pct and bool(m.group("neg") == "(" and m.group("close"))) or (m.group("neg") is not None and m.group("neg") in _MINUS)
        if neg:
            value = -value
        pre, post = m.group("pre"), (m.group("post") or "").strip()
        cur = symbol_to_code(pre) or symbol_to_code(post)
        out.append((Money(value, cur, amb, bool(m.group("pct")), m.group(0).strip()), m.start(), m.end()))
    return out


def parse_quantity(text: str) -> float | None:
    """'3', '2.5', '10 pcs', '4 x', '1,000' -> a number. Returns None for anything else."""
    m = re.match(r"^\s*(\d[\d,]*(?:\.\d+)?)\s*(?:x|pcs?|pc|units?|nos?\.?|hrs?|hours?|days?|kg|g|l|ml|m|ea|each)?\s*$", text or "", re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def detect_decimal_style(text: str) -> str | None:
    """Look for amounts that can only be read one way and report the decimal mark the document uses."""
    dot = len(re.findall(r"(?<![\d.,])\d{1,3}(?:,\d{3})+\.\d{1,2}(?![\d])|(?<![\d.,])\d+\.\d{2}(?![\d.,])", text))
    comma = len(re.findall(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+,\d{1,2}(?![\d])|(?<![\d.,])\d+,\d{2}(?![\d.,])", text))
    if dot and comma:
        return "." if dot >= comma * 2 else ("," if comma >= dot * 2 else None)
    if dot:
        return "."
    if comma:
        return ","
    return None


_CUR_HEADER_PAREN = re.compile(r"(?i:amount|total|price|rate|value|net|gross|unit)[^\n()]{0,25}\(\s*([A-Z]{3})\s*\)")
_CUR_SENTENCE = re.compile(r"(?i:(?:amounts?|prices?|values?|figures?|totals?|charges?|rates?|fees?)\s+(?:are\s+|is\s+)?(?:shown\s+|stated\s+|quoted\s+|expressed\s+|given\s+|payable\s+)?in)\s+([A-Z]{3})\b")


def detect_currency(text: str, prefer_lines: list[str] | None = None) -> tuple[str | None, str | None, float]:
    """Return (code, evidence, confidence). A printed ISO code beats a symbol; a "$" alone is only 0.7."""
    label = re.search(r"currency\s*(?:code)?\s*[:\-]?\s*\(?([A-Z]{3})\)?", text, re.I)
    if label and label.group(1).upper() in ISO_CODES:
        return label.group(1).upper(), label.group(0).strip(), 0.97
    for pat in (_CUR_HEADER_PAREN, _CUR_SENTENCE):
        m = pat.search(text)
        if m and m.group(1) in ISO_CODES:
            return m.group(1), m.group(0).strip(), 0.95
    counts: dict[str, int] = {}
    first_ev: dict[str, str] = {}
    sources = (prefer_lines or []) + [text]
    for chunk_idx, chunk in enumerate(sources):
        weight = 3 if chunk_idx < len(sources) - 1 else 1
        for m in re.finditer(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])\s*[-+(]?\s*\d|\d[\d.,]*\s*([A-Z]{3})(?![A-Za-z])", chunk):
            code = (m.group(1) or m.group(2) or "").upper()
            if code in ISO_CODES:
                counts[code] = counts.get(code, 0) + weight
                first_ev.setdefault(code, code)
    if counts:
        best = max(counts, key=lambda c: counts[c])
        return best, first_ev[best], 0.95
    sym_counts: dict[str, int] = {}
    sym_ev: dict[str, str] = {}
    for chunk_idx, chunk in enumerate(sources):
        weight = 3 if chunk_idx < len(sources) - 1 else 1
        for m in re.finditer(r"(US\$|R\$|[€£¥₹৳₽₩₺₫₱$]|\bTk\.?)\s*[-+(]?\s*\d", chunk):
            sym = m.group(1)
            sym_counts[sym] = sym_counts.get(sym, 0) + weight
            sym_ev.setdefault(sym, sym)
    if not sym_counts:
        if re.search(r"\d\s*/-", text) and re.search(r"\b(taka|bdt|tk)\b", text, re.I):
            return "BDT", None, 0.6
        return None, None, 0.0
    best = max(sym_counts, key=lambda s: sym_counts[s])
    code = CURRENCY_SYMBOLS.get(best)
    if code is None:
        return None, None, 0.0
    return code, sym_ev[best], 0.7 if best in AMBIGUOUS_SYMBOLS else 0.9


# --------------------------------------------------------------------------- dates

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_ISO_RE = re.compile(r"(?<![\d])(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d])")
_NUM_RE = re.compile(r"(?<![\d.])(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?![\d])")
_DMY_NAME_RE = re.compile(rf"(?<![\d])(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]*({_MONTH_RE})\.?,?[\s\-/.]*(\d{{4}}|\d{{2}})(?![\d])", re.I)
_MDY_NAME_RE = re.compile(rf"(?<![A-Za-z])({_MONTH_RE})\.?[\s\-/.]*(\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-/.]*(\d{{4}})(?![\d])", re.I)


@dataclass
class ParsedDate:
    value: date
    ambiguous: bool = False
    alt: date | None = None
    start: int = 0
    end: int = 0
    raw: str = ""


def _year(y: str) -> int:
    n = int(y)
    if len(y) == 2:
        return 2000 + n if n < 70 else 1900 + n
    return n


def _mk(y: int, m: int, d: int) -> date | None:
    if not (1900 <= y <= 2200):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, order: str | None = None) -> list[ParsedDate]:
    """Find dates in text. `order` is 'dmy' or 'mdy' for numeric dates such as 03/04/2026; None means 'unknown'."""
    found: list[ParsedDate] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    for m in _ISO_RE.finditer(text):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d and free(m.start(), m.end()):
            found.append(ParsedDate(d, False, None, m.start(), m.end(), m.group(0)))
            taken.append((m.start(), m.end()))
    for rx, named_first in ((_DMY_NAME_RE, "d"), (_MDY_NAME_RE, "m")):
        for m in rx.finditer(text):
            if not free(m.start(), m.end()):
                continue
            if named_first == "d":
                day, mon, yr = int(m.group(1)), MONTHS[m.group(2).lower()], _year(m.group(3))
            else:
                mon, day, yr = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
            d = _mk(yr, mon, day)
            if d:
                found.append(ParsedDate(d, False, None, m.start(), m.end(), m.group(0)))
                taken.append((m.start(), m.end()))
    for m in _NUM_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if a > 12 and b <= 12:
            d, amb, alt = _mk(y, b, a), False, None
        elif b > 12 and a <= 12:
            d, amb, alt = _mk(y, a, b), False, None
        elif a == b:
            d, amb, alt = _mk(y, a, b), False, None
        elif a <= 12 and b <= 12:
            dmy, mdy = _mk(y, b, a), _mk(y, a, b)
            if order == "mdy":
                d, amb, alt = mdy, False, None
            elif order == "dmy":
                d, amb, alt = dmy, False, None
            else:
                d, amb, alt = dmy, True, mdy  # default to day first, but say it was a guess
        else:
            continue
        if d:
            found.append(ParsedDate(d, amb, alt, m.start(), m.end(), m.group(0)))
            taken.append((m.start(), m.end()))
    found.sort(key=lambda p: p.start)
    return found


def infer_date_order(text: str) -> str | None:
    """'dmy' if some numeric date has a first number above 12, 'mdy' if a second number is above 12."""
    dmy = mdy = 0
    for m in _NUM_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            dmy += 1
        elif b > 12 and a <= 12:
            mdy += 1
    if dmy and not mdy:
        return "dmy"
    if mdy and not dmy:
        return "mdy"
    return None


# --------------------------------------------------------------------------- names and numbers

_LEGAL_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "llp", "gmbh", "co", "corp", "corporation", "company", "plc",
    "pvt", "pte", "sa", "bv", "ag", "srl", "spa", "oy", "ab", "as", "nv", "sdn", "bhd", "pty", "lp", "kg", "ug",
}


def normalize_name(name: str | None) -> str:
    """Lower case, no punctuation, no legal suffixes. For comparing, never for display."""
    if not name:
        return ""
    s = re.sub(r"[^\w\s]", " ", name.casefold())
    words = [w for w in s.split() if w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def normalize_invoice_number(num: str | None) -> str:
    """'inv 2026/00417', 'INV-2026-00417' and 'Inv.2026-00417' compare equal."""
    if not num:
        return ""
    return re.sub(r"[^0-9A-Z]", "", num.upper())


def money_str(v: float | None) -> str:
    return "" if v is None else f"{v:,.2f}"

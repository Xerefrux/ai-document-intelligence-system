"""Label vocabulary: the words that sit next to the values we want.

Regexes are anchored at the start of a cell. Each carries a weight that becomes the starting
confidence of a value found through it (3 = an unambiguous label, 1 = a weak one).
"""

from __future__ import annotations

import re

I = re.IGNORECASE


def rx(p: str) -> re.Pattern[str]:
    return re.compile(p, I)


# ---- OCR repair for the first word of a label ------------------------------------------------
_REPAIRS = [
    (rx(r"^[lI1|!]nv[o0]ic[e3]\b"), "Invoice"),
    (rx(r"^inv[o0]ic[e3]\b"), "Invoice"),
    (rx(r"^Gr[a4]nd\s+T[o0]ta[l1|I!]\b"), "Grand Total"),
    (rx(r"^Sub[\s\-]*T[o0]ta[l1|I!]\b"), "Subtotal"),
    (rx(r"^T[o0]ta[l1|I!]\b"), "Total"),
    (rx(r"^Dat[e3]\b"), "Date"),
    (rx(r"^Du[e3]\s+Dat[e3]\b"), "Due Date"),
    (rx(r"^Am[o0]unt\s+Du[e3]\b"), "Amount Due"),
    (rx(r"^B[i1l|!]{2}(?:ed)?\s+T[o0]\b"), "Bill To"),
]


def repair_label(text: str) -> str:
    for pat, repl in _REPAIRS:
        m = pat.match(text)
        if m:
            return repl + text[m.end():]
    return text


# ---- header fields ------------------------------------------------------------------------------
_SEP = r"\s*[:#\-.]*\s*"

INVOICE_NUMBER = [
    (rx(rf"^(?:tax\s+|vat\s+|sales\s+)?inv(?:oice)?\.?\s*(?:no|nr|num|number|id|#)\b\.?{_SEP}(?P<v>.*)$"), 3.0),
    (rx(rf"^(?:tax\s+)?inv(?:oice)?\s*#{_SEP}(?P<v>.*)$"), 3.0),
    (rx(rf"^(?:tax\s+)?invoice\s*[:\-]\s*(?P<v>.+)$"), 2.5),
    (rx(r"^(?:tax\s+)?invoice\s+(?P<v>(?=[^\s]*\d)[A-Za-z0-9][\w\-/.]*)$"), 2.5),
    (rx(rf"^(?:bill|receipt|document|doc|memo|voucher|credit\s+note|debit\s+note)\s*(?:no|nr|num|number|id|#)\b\.?{_SEP}(?P<v>.*)$"), 2.0),
    (rx(rf"^(?:ref(?:erence)?)\s*(?:no|nr|num|number|#)?\b\.?{_SEP}(?P<v>.*)$"), 0.8),
    # The first word is unreadable ("nice No: 123"). Weak, and never for labels that name some other number.
    (rx(rf"^(?!(?:order|acct|account|customer|client|po|purchase|tax|vat|gst|tin|bin|phone|tel|fax|item|serial|part|page|line|bank|card|cheque|check|batch|lot|route|shipment|tracking|delivery|waybill|sl|sr|pos)\b)[a-z]{{3,9}}\s+(?:no|nr|num|number)\b\.?{_SEP}(?P<v>\S*\d\S*)$"), 1.0),
]

INVOICE_DATE = [
    (rx(rf"^(?:invoice|inv\.?|issue|issued|billing|bill|document|doc|tax\s+point)\s*(?:date|dt)\b\.?\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(rf"^date\s+of\s+(?:issue|invoice|billing)\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(rf"^(?:dated?)\b\s*[:\-]?\s*(?P<v>.*)$"), 1.5),
]

DUE_DATE = [
    (rx(rf"^(?:payment\s+)?due(?:\s+(?:date|on|by))?\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(rf"^date\s+due\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(rf"^pay(?:ment)?\s+(?:by|before|date)\b\s*[:\-]?\s*(?P<v>.*)$"), 2.5),
    (rx(rf"^payable\s+(?:by|on|before)\b\s*[:\-]?\s*(?P<v>.*)$"), 2.5),
]

# Dates that belong to something else, so they are not mistaken for the invoice date.
OTHER_DATE_LABEL = rx(r"^(?:order|ship(?:ping|ped)?|deliver(?:y|ed)?|due|payment|po|purchase|service|period|from|to|start|end|expir\w*|valid\w*|issue\s+due|posted|paid|received|dispatch\w*|shipment)\b")

# ---- parties -----------------------------------------------------------------------------------------
CUSTOMER = [
    (rx(r"^(?:bill(?:ed)?|invoice[d]?|sold)\s*-?\s*to\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(r"^(?:customer|client|buyer|purchaser|consignee|payer|account\s+holder)(?:\s+name)?\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(r"^(?:m\s*/\s*s\.?|messrs\.?)\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(r"^(?:to)\s*[:\-]\s*(?P<v>.*)$"), 2.0),
    (rx(r"^to$"), 1.5),
    (rx(r"^(?:ship(?:ped)?|deliver(?:ed)?)\s*-?\s*to\b\s*[:\-]?\s*(?P<v>.*)$"), 1.0),
]

SUPPLIER = [
    (rx(r"^(?:from|seller|vendor|supplier|sold\s+by|issued\s+by|billed\s+by|bill\s+from|payee|remit(?:tance)?\s+to|pay\s+to|make\s+(?:cheques?|checks?)\s+payable\s+to)(?:\s+name)?\b\s*[:\-]?\s*(?P<v>.*)$"), 3.0),
    (rx(r"^(?:company|business)\s+name\b\s*[:\-]?\s*(?P<v>.*)$"), 2.0),
]

TITLE_WORDS = rx(
    r"^(?:(?:tax\s+|vat\s+|sales\s+|commercial\s+|proforma\s+|pro\s*forma\s+|final\s+|e-?)?invoice|bill|receipt|statement|credit\s+note|debit\s+note|"
    r"purchase\s+order|quotation|quote|estimate|page\s+\d+(?:\s+of\s+\d+)?|original|copy|duplicate|paid|draft|unpaid|overdue|cash\s+memo|cash\s+receipt|tax\s+receipt)$"
)

ADDRESS_WORDS = rx(
    r"\b(?:road|rd\.?|street|st\.?|avenue|ave\.?|lane|ln\.?|floor|fl\.?|suite|ste\.?|block|house|flat|apt\.?|apartment|plot|sector|"
    r"box|p\.?o\.?\s*box|building|bldg\.?|tower|plaza|market|bazar|bazaar|colony|district|zip|postcode|post\s+code|"
    r"strasse|straße|platz|boulevard|blvd\.?|drive|dr\.?|highway|hwy\.?|park|city|state|country)\b"
)
COUNTRY_WORDS = rx(r"\b(?:bangladesh|india|pakistan|usa|united\s+states|united\s+kingdom|uk|canada|australia|germany|france|singapore|malaysia|uae|china|japan)\b")
LEGAL_SUFFIX = rx(r"\b(?:ltd|limited|inc|incorporated|llc|llp|gmbh|co|corp|corporation|company|plc|pvt|pte|sa|bv|ag|srl|spa|sdn|bhd|pty|enterprises?|traders|trading|industries|solutions|services|systems|technologies|laboratories|pharma|pharmaceuticals|associates|group|holdings|international|stores?|mart|shop)\b")

EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RX = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|io|co|biz|info|bd)\b", re.I)
PHONE_RX = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")

NON_NAME_LABEL = rx(
    r"^(?:invoice|inv|bill|date|due|total|subtotal|sub\s+total|tax|vat|gst|amount|balance|qty|quantity|description|item|price|rate|"
    r"phone|tel|telephone|mobile|fax|email|e-mail|website|web|address|attn|attention|contact|ref|reference|po|order|terms|payment|"
    r"bank|account|a/c|iban|swift|bic|sort\s+code|tin|bin|vat\s+no|gst\s+no|tax\s+id|reg|registration|page|currency|notes?|"
    r"customer\s+(?:id|no|number|code)|client\s+(?:id|no)|ship|deliver|from|to|sold|seller|buyer)\b"
)

# ---- amount labels ----------------------------------------------------------------------------------
_PCT = r"\s*[@(]?\s*\d+(?:[.,]\d+)?\s*%\s*\)?"
_CUR = r"\s*\(?\s*(?:[A-Z]{3}|[$€£¥₹৳])\s*\)?"


def _norm_label(label: str) -> str:
    s = repair_label(label.strip())
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[:.\s]+$", "", s)
    return s


def classify_amount_label(label: str) -> tuple[str, int] | None:
    """Return (kind, rank) for text like 'Sub Total', 'VAT @ 15%' or 'Amount Due', else None.

    Kinds: subtotal, tax, discount, shipping, total. For totals, a higher rank is a better
    candidate for "the invoice total".
    """
    s = _norm_label(label)
    if not s or len(s) > 60:
        return None
    s_nopct = re.sub(_PCT, " ", s).strip()
    s_nocur = re.sub(r"\(?\s*(?:[a-z]{3}|[$€£¥₹৳])\s*\)?$", "", s_nopct).strip() if re.search(r"\(\s*(?:[a-z]{3}|[$€£¥₹৳])\s*\)$", s_nopct) else s_nopct
    t = re.sub(r"\s+", " ", s_nocur).strip(" :-")

    if re.search(r"\b(?:qty|quantity|items?|units?|weight|pieces|pcs|pages?|hours?)\b", t) and "total" in t:
        return None
    if re.match(r"^(?:sub\s*-?\s*total|net\s+(?:total|amount|value)(?!\s+payable)|total\s+(?:excl\w*|excluding|before|ex|net)\b.*|"
                r"(?:amount|total)\s+before\s+(?:tax|vat|gst)\b.*|taxable\s+(?:amount|value|total)|goods\s+total|items?\s+total|"
                r"merchandise\s+total|total\s+net|sub\s*-?\s*total\s*\(.*\))$", t):
        return "subtotal", 1
    if re.match(r"^(?:total\s+)?(?:sales\s+tax|tax|vat|gst|hst|pst|qst|igst|cgst|sgst|iva|mwst|btw|service\s+tax|tax\s+amount|vat\s+amount|gst\s+amount|tax\s+total|total\s+tax|total\s+vat|total\s+gst)$", t):
        return "tax", 2 if t.startswith("total") or t.endswith("total") else 1
    if re.match(r"^(?:total\s+)?(?:discount|rebate|promo(?:tion)?(?:\s+discount)?|less\s*:?\s*discount|discount\s+amount)$", t):
        return "discount", 1
    if re.match(r"^(?:shipping|freight|delivery|postage|handling|shipping\s*(?:&|and)\s*handling|delivery\s+charges?|shipping\s+charges?|freight\s+charges?)$", t):
        return "shipping", 1
    if t == "grand total" or t == "grand total amount":
        return "total", 5
    if re.match(r"^(?:invoice\s+total|total\s+invoice(?:\s+amount)?|total\s+amount|total\s+(?:incl\w*|inc|including|with)\b.*|gross\s+total|total\s+gross|"
                r"amount\s+total|total\s+payable|net\s+payable|total\s+value|total\s+price|total\s+cost|total\s+charges?|total\s+sum|invoice\s+amount)$", t):
        return "total", 4
    if t == "total":
        return "total", 3
    if re.match(r"^(?:total\s+due|total\s+amount\s+due|amount\s+due|balance\s+due|due\s+amount|amount\s+payable|please\s+pay|pay\s+this\s+amount|balance\s+payable|total\s+balance|balance)$", t):
        return "total", 2
    return None

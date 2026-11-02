"""Random but realistic invoice content, and the ground truth that goes with it.

Everything is driven by a seeded random.Random, so the same seed always gives the same dataset.
The ground truth stores what is PRINTED on the document. If a template prints no customer, the true
customer is null, because that is what a correct extractor must answer.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

A = ["Northwind", "Blue Harbour", "Apex", "Silverline", "Greenfield", "Meridian", "Crescent", "Pioneer", "Summit", "Orion", "Falcon", "Lotus",
     "Atlas", "Vertex", "Nimbus", "Cedar", "Ironbridge", "Sunrise", "Padma", "Jamuna", "Meghna", "Karnaphuli", "Bengal", "Shapla", "Delta",
     "Eastern", "Global", "Prime", "Unity", "Alpha", "Zenith", "Coral", "Aurora", "Maple", "Redwood", "Stellar", "Quantum", "Pacific", "Nordic", "Harmony"]
B = ["Traders", "Medical Supplies", "Office Solutions", "Digital Services", "Logistics", "Foods", "Constructions", "Pharma", "Engineering",
     "Consulting", "Textiles", "Electronics", "Printing Press", "Cleaning Services", "Security Services", "Catering", "Telecom", "Hardware",
     "Stationery", "Agro", "Energy", "Software", "Laboratories", "Transport"]
SUFFIX = ["Ltd.", "Limited", "Pvt Ltd", "LLC", "Inc.", "GmbH", "Co.", "Corp.", "PLC", "& Sons", "Enterprise", ""]
PEOPLE = ["Farhana Rahman", "Tanvir Ahmed", "John Smith", "Maria Garcia", "Anika Chowdhury", "David Miller", "Sadia Islam", "Karim Uddin", "Emily Clark", "Rafiq Hasan"]
STREETS = ["Harbour Road", "Gulshan Avenue", "Station Street", "Park Lane", "Mirpur Road", "Market Street", "Lake View Drive", "Industrial Area Road", "Church Street", "Airport Road", "Green Road", "King Street"]
CITIES = {
    "BD": [("Dhaka", "1212"), ("Chattogram", "4000"), ("Sylhet", "3100"), ("Khulna", "9000"), ("Rajshahi", "6000")],
    "US": [("Austin", "78701"), ("Denver", "80202"), ("Boston", "02110"), ("Seattle", "98101")],
    "GB": [("Manchester", "M1 4BT"), ("Leeds", "LS1 4AP"), ("Bristol", "BS1 5TR")],
    "DE": [("Berlin", "10115"), ("Hamburg", "20095"), ("Munich", "80331")],
    "IN": [("Mumbai", "400001"), ("Pune", "411001"), ("Chennai", "600001")],
}
COUNTRY_NAME = {"BD": "Bangladesh", "US": "USA", "GB": "United Kingdom", "DE": "Germany", "IN": "India"}

CATALOG: dict[str, list[tuple[str, float, float]]] = {
    "medical": [("Surgical gloves box", 3, 15), ("Digital thermometer", 8, 40), ("Blood pressure monitor", 25, 120), ("Hand sanitizer 500ml", 2, 9),
                ("Disposable face masks pack", 4, 18), ("Wheelchair rental per week", 30, 90), ("Nursing consultation hour", 20, 60),
                ("Oxygen concentrator service", 50, 300), ("Pulse oximeter", 15, 60), ("First aid kit", 10, 45), ("Surgical scissors", 5, 25), ("Adult diapers pack", 6, 22)],
    "it": [("Dell Latitude 5440 notebook", 700, 1500), ("27 inch monitor", 150, 400), ("Wireless keyboard and mouse set", 20, 80), ("Cloud hosting monthly plan", 20, 300),
           ("Network switch 24 port", 80, 400), ("Software license annual", 50, 900), ("Laptop docking station", 60, 220), ("External SSD 1TB", 70, 160),
           ("IT support hour", 30, 110), ("Antivirus subscription", 15, 60), ("Wi-Fi router", 40, 180), ("Server maintenance contract", 200, 2500)],
    "office": [("A4 copy paper box", 15, 40), ("Ergonomic office chair", 90, 380), ("Standing desk", 180, 650), ("Ballpoint pens box of 50", 5, 18),
               ("Whiteboard 120x90", 30, 110), ("Filing cabinet 4 drawer", 90, 320), ("Stapler heavy duty", 8, 30), ("Printer toner cartridge", 35, 140),
               ("Desk lamp LED", 12, 55), ("Sticky notes 12 pack", 4, 14)],
    "logistics": [("Freight charges Dhaka to Chattogram", 120, 900), ("Courier service parcel", 5, 40), ("Warehouse storage per month", 100, 1200),
                  ("Customs clearance fee", 80, 500), ("Pallet handling", 10, 60), ("Last mile delivery", 3, 25)],
    "food": [("Basmati rice 25kg", 20, 60), ("Cooking oil 5L", 8, 20), ("Fresh vegetables assorted crate", 12, 45), ("Tea leaves 1kg", 6, 30),
             ("Mineral water 20L jar", 2, 7), ("Catering lunch box", 3, 12), ("Sugar 50kg", 25, 55)],
    "construction": [("Cement bag 50kg", 4, 9), ("Steel rebar 12mm per ton", 600, 900), ("Ceramic floor tiles per sqm", 6, 40), ("Electrical wiring 100m roll", 25, 90),
                     ("Plumbing fixture set", 40, 220), ("Site labour per day", 15, 70), ("Paint 20L bucket", 30, 110)],
    "consulting": [("Business consulting hour", 60, 250), ("Data analysis project fee", 500, 4000), ("Training workshop day", 300, 1500),
                   ("Legal advisory retainer", 400, 2000), ("Audit services", 800, 5000), ("Website design and development", 600, 6000), ("Market research report", 300, 1800)],
}

# currency: (symbol used by some templates, number style, decimals)
CURRENCIES = {
    "USD": ("$", "us"), "EUR": ("€", "eu"), "GBP": ("£", "us"), "BDT": ("Tk.", "lakh"), "INR": ("Rs.", "lakh"),
    "AUD": ("$", "us"), "CAD": ("$", "us"), "SGD": ("$", "us"),
}
COUNTRY_CURRENCY = {"BD": ["BDT", "BDT", "USD"], "US": ["USD", "USD", "CAD"], "GB": ["GBP"], "DE": ["EUR"], "IN": ["INR", "USD"]}


def fmt_number(v: float, style: str) -> str:
    """Format a number the way different countries write them."""
    neg = v < 0
    s = f"{abs(v):.2f}"
    whole, frac = s.split(".")
    if style == "eu":
        g = f"{int(whole):,}".replace(",", ".")
        out = f"{g},{frac}"
    elif style == "ch":
        out = f"{int(whole):,}".replace(",", "'") + f".{frac}"
    elif style == "fr":
        out = f"{int(whole):,}".replace(",", " ") + f",{frac}"
    elif style == "lakh":
        n = whole
        if len(n) > 3:
            head, tail = n[:-3], n[-3:]
            parts = []
            while len(head) > 2:
                parts.insert(0, head[-2:])
                head = head[:-2]
            if head:
                parts.insert(0, head)
            n = ",".join(parts) + "," + tail
        out = f"{n}.{frac}"
    elif style == "plain":
        out = s
    else:
        out = f"{int(whole):,}.{frac}"
    return ("-" if neg else "") + out


def fmt_date(d: date, style: str) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    full = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    return {
        "iso": d.isoformat(),
        "dmy_slash": f"{d.day:02d}/{d.month:02d}/{d.year}",
        "mdy_slash": f"{d.month:02d}/{d.day:02d}/{d.year}",
        "dmy_dot": f"{d.day:02d}.{d.month:02d}.{d.year}",
        "dmy_mon": f"{d.day} {months[d.month - 1]} {d.year}",
        "dmy_dash_mon": f"{d.day:02d}-{months[d.month - 1]}-{d.year}",
        "mon_dmy": f"{full[d.month - 1]} {d.day}, {d.year}",
        "dmy_full": f"{d.day} {full[d.month - 1]} {d.year}",
    }[style]


@dataclass
class Party:
    name: str
    lines: list[str]
    email: str = ""
    phone: str = ""


@dataclass
class Item:
    description: str
    quantity: float
    unit_price: float
    amount: float
    category: str = ""
    printed_description: str | None = None  # set by a template that shortens the name (receipts)


@dataclass
class Invoice:
    number: str
    date: date
    due_date: date | None
    supplier: Party
    customer: Party | None
    currency: str
    items: list[Item]
    subtotal: float
    tax_rate: float
    tax: float
    discount: float
    shipping: float
    total: float
    po: str | None = None
    country: str = "US"
    category: str = ""
    faults: list[str] = field(default_factory=list)
    # What is actually printed, after faults. None means "not printed".
    p_number: str | None = None
    p_date: date | None = None
    p_due: date | None = None
    p_supplier: bool = True
    p_subtotal: float | None = None
    p_tax: float | None = None
    p_total: float | None = None
    p_item_amounts: list[float] = field(default_factory=list)
    expect_codes: set[str] = field(default_factory=set)
    key: str = ""
    date_style_override: str | None = None

    def __post_init__(self) -> None:
        self.p_number = self.number
        self.p_date = self.date
        self.p_due = self.due_date
        self.p_subtotal = self.subtotal
        self.p_tax = self.tax
        self.p_total = self.total
        self.p_item_amounts = [i.amount for i in self.items]


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


def make_party(rng: random.Random, country: str, *, person_ok: bool = False) -> Party:
    if person_ok and rng.random() < 0.15:
        name = rng.choice(PEOPLE)
    else:
        name = f"{rng.choice(A)} {rng.choice(B)} {rng.choice(SUFFIX)}".strip()
    city, zipc = rng.choice(CITIES[country])
    street = f"{rng.randint(1, 240)} {rng.choice(STREETS)}"
    slug = "".join(ch for ch in name.lower().split()[0] if ch.isalpha())
    domain = {"BD": "com.bd", "GB": "co.uk", "DE": "de", "IN": "in"}.get(country, "com")
    phone = {"BD": "+880 17" + str(rng.randint(10000000, 99999999)), "US": f"+1 {rng.randint(200, 989)}-{rng.randint(200, 989)}-{rng.randint(1000, 9999)}",
             "GB": f"+44 20 {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}", "DE": f"+49 30 {rng.randint(100000, 999999)}", "IN": f"+91 {rng.randint(70000, 99999)} {rng.randint(10000, 99999)}"}[country]
    return Party(name, [street, f"{city} {zipc}", COUNTRY_NAME[country]], f"billing@{slug}.{domain}", phone)


def make_number(rng: random.Random, year: int) -> str:
    style = rng.randrange(8)
    n = rng.randint(1, 99999)
    return [
        f"INV-{year}-{n:05d}", f"{year}/{n:04d}", f"INV{n:07d}", f"A-{n + 10000}", f"SI-{year % 100}-{n % 1000:03d}",
        f"{n + 100000}", f"INV/{year % 100}/{n % 10000:04d}", f"BD{year}{n:05d}",
    ][style]


def make_items(rng: random.Random, category: str, n: int, hours_ok: bool = True) -> list[Item]:
    pool = CATALOG[category]
    picks = rng.sample(pool, min(n, len(pool)))
    while len(picks) < n:  # more rows than the catalogue holds: allow repeats with a variant
        base = rng.choice(pool)
        picks.append((base[0] + f" (batch {len(picks)})", base[1], base[2]))
    items: list[Item] = []
    for desc, lo, hi in picks:
        qty = float(rng.choice([1, 1, 2, 3, 4, 5, 6, 8, 10, 12, 20])) if hi < 2000 else float(rng.choice([1, 1, 2]))
        if hours_ok and ("hour" in desc or "day" in desc) and rng.random() < 0.3:
            qty = rng.choice([1.5, 2.5, 7.5, 0.5])
        unit = r2(rng.uniform(lo, hi))
        items.append(Item(desc, qty, unit, r2(qty * unit), category))
    return items


def make_invoice(rng: random.Random, *, country: str | None = None, n_items: int | None = None, category: str | None = None,
                 tax_rate: float | None = None, with_discount: bool = False, with_shipping: bool = False,
                 numeric_date_unambiguous: bool = True, year: int | None = None) -> Invoice:
    country = country or rng.choice(["BD", "US", "GB", "DE", "IN"])
    category = category or rng.choice(list(CATALOG))
    year = year or rng.choice([2025, 2026])
    day = rng.randint(13, 28) if numeric_date_unambiguous else rng.randint(1, 12)
    d = date(year, rng.randint(1, 12), day)
    due = d + timedelta(days=rng.choice([7, 15, 30, 45, 60])) if rng.random() < 0.75 else None
    currency = rng.choice(COUNTRY_CURRENCY[country])
    items = make_items(rng, category, n_items or rng.randint(2, 9))
    subtotal = r2(sum(i.amount for i in items))
    rate = tax_rate if tax_rate is not None else rng.choice([0.0, 5.0, 7.5, 10.0, 15.0, 18.0, 19.0, 20.0])
    discount = r2(subtotal * rng.choice([0.05, 0.1])) if with_discount else 0.0
    shipping = r2(rng.uniform(8, 60)) if with_shipping else 0.0
    taxable = subtotal - discount
    tax = r2(taxable * rate / 100.0)
    total = r2(subtotal - discount + shipping + tax)
    inv = Invoice(
        number=make_number(rng, year), date=d, due_date=due, supplier=make_party(rng, country), customer=make_party(rng, rng.choice(["BD", country]), person_ok=True),
        currency=currency, items=items, subtotal=subtotal, tax_rate=rate, tax=tax, discount=discount, shipping=shipping, total=total,
        po=f"PO-{rng.randint(1000, 99999)}" if rng.random() < 0.4 else None, country=country, category=category,
    )
    return inv


def truth_record(inv: Invoice, prints: set[str], *, currency_printed: bool = True) -> dict:
    """What a perfect extractor must return for the document as printed."""
    def amt(x):  # noqa: ANN001
        return None if x is None else round(float(x), 2)

    items = []
    for it, printed_amount in zip(inv.items, inv.p_item_amounts):
        items.append({"description": it.printed_description or it.description, "quantity": it.quantity if "item_qty" in prints else None,
                      "unit_price": it.unit_price if "item_unit" in prints else None, "amount": amt(printed_amount) if "item_amount" in prints else None})
    return {
        "invoice_number": inv.p_number if "number" in prints else None,
        "invoice_date": inv.p_date.isoformat() if inv.p_date and "date" in prints else None,
        "due_date": inv.p_due.isoformat() if inv.p_due and "due" in prints else None,
        "supplier_name": inv.supplier.name if inv.p_supplier else None,
        "customer_name": inv.customer.name if inv.customer and "customer" in prints else None,
        "subtotal": amt(inv.p_subtotal) if "subtotal" in prints else None,
        "tax": amt(inv.p_tax) if "tax" in prints else None,
        "total": amt(inv.p_total),
        "currency": inv.currency if currency_printed else None,
        "line_items": items if "items" in prints else [],
    }

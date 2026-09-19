"""Turns OCR output (Textract text + Comprehend entities) into candidate values for the
invoice form.

Each candidate is a value the vendor can edit and tag as one of EXTRACTABLE_FIELDS. The
suggested tag is only a starting point: scanned invoices vary too much to map them
reliably, so the UI always lets the vendor correct both the value and the tag.

Pure functions only, so the rules are easy to test against sample documents.
"""

import re
from dataclasses import asdict, dataclass

from app.constants import EXTRACTABLE_FIELDS

MAX_CANDIDATES = 50
MIN_ENTITY_SCORE = 0.5

AMOUNT_FIELDS = {"invoiceAmount", "vatableSales", "vat", "nonVat"}
DATE_FIELDS = {"invoiceDate", "dateReceived"}
ID_FIELDS = {"invoiceNo", "poPrNo", "drNo"}

_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
DATE_RE = re.compile(
    rf"\b(?:(?:{_MONTHS})\.?\s+\d{{1,2}},?\s*\d{{4}}|\d{{1,2}}\s+(?:{_MONTHS})\.?,?\s+\d{{4}}"
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    re.IGNORECASE,
)
# 1,234.56 / ₱1,234.56 / PHP 1234.56 — two decimals required so IDs and quantities don't match.
AMOUNT_RE = re.compile(r"(?:₱|PHP|P|\$)?\s*-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\b", re.IGNORECASE)

# Label → field, checked in order (first match wins). A value is read from the rest of the
# line, or from the next few lines when the label stands alone (common in Textract output).
LABEL_RULES: list[tuple[str | None, re.Pattern]] = [
    # Registration numbers and contact details look like labels but are never form fields.
    (None, re.compile(r"^(vat\s*reg|tin\b|tel\b|telephone|fax\b|e-?mail|website|cp\s*#)", re.I)),
    ("nonVat", re.compile(r"^(vat[\s-]*exempt|zero[\s-]*rated|non[\s-]*vat)", re.I)),
    ("vatableSales", re.compile(r"^(vatable\s+sales|sub[\s-]*total|net\s+of\s+vat|amount\s+net\s+of)", re.I)),
    ("vat", re.compile(r"^(vat(\s*amount)?|vat\s*\(\s*\d+\s*%\s*\)|output\s+tax|tax)\b", re.I)),
    (
        "invoiceAmount",
        re.compile(r"^(total\s+amount\s+due|amount\s+due|total\s+amount|grand\s+total|total\s+sales|total)\b", re.I),
    ),
    (
        "invoiceNo",
        re.compile(r"^((sales\s+|service\s+|official\s+)?invoice\s*(no|number|#)|inv\.?\s*(no|#)|no)\b\.?", re.I),
    ),
    ("invoiceDate", re.compile(r"^(invoice\s+)?date(\s+issued)?\b", re.I)),
    (
        "poPrNo",
        re.compile(r"^(p\.?\s?o\.?|purchase\s+order|p\.?\s?r\.?|purchase\s+requisition)(\s*(no|number|#))?\b", re.I),
    ),
    ("drNo", re.compile(r"^(d\.?\s?r\.?|delivery\s+receipt)\s*(no|number|#)\b", re.I)),
    ("creditTerms", re.compile(r"^(payment\s+terms|terms(\s+of\s+payment)?)\b", re.I)),
    ("description", re.compile(r"^(description|particulars|item\s+description)\b", re.I)),
    ("vendorName", re.compile(r"^(vendor|supplier|seller|sold\s+by)\b", re.I)),
]

# Standalone reference numbers anywhere in the text.
ID_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("invoiceNo", re.compile(r"\bINV[-\s]?[A-Z0-9]*\d[\w-]*", re.I)),
    ("poPrNo", re.compile(r"\bP[OR][-\s]?\d[\w-]*", re.I)),
    ("drNo", re.compile(r"\bDR[-\s]?\d[\w-]*", re.I)),
]

# Document titles → invoice type option.
INVOICE_TYPE_TITLES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^credit\s+memo$", re.I), "Credit Memo"),
    (re.compile(r"^debit\s+memo$", re.I), "Debit Memo"),
    (re.compile(r"^pro[\s-]?forma(\s+invoice)?$", re.I), "Proforma Invoice"),
    (re.compile(r"^progress\s+billing$", re.I), "Progress Billing"),
    (re.compile(r"^(sales\s+|service\s+|commercial\s+|tax\s+)?invoice$", re.I), "Standard Invoice"),
]

# Table headers and boilerplate that are never values.
NOISE_WORDS = {
    "qty", "quantity", "unit", "unit price", "price", "amount", "description", "total",
    "bill to", "sold to", "ship to", "notes", "reference", "reference:", "cash",
}

LOOKAHEAD = {"description": 4}
DEFAULT_LOOKAHEAD = 2


@dataclass
class Candidate:
    id: str
    value: str
    # Where it came from: the label next to it ("TOTAL:") or the entity type ("DATE").
    source: str
    entity_type: str | None = None
    score: float | None = None
    suggested_field: str | None = None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_noise(value: str) -> bool:
    return _normalize(value).rstrip(":") in NOISE_WORDS


def _is_label(line: str) -> bool:
    return any(rule.match(line) for _, rule in LABEL_RULES)


def _fits(field: str, value: str) -> str | None:
    """The part of `value` usable for `field`, or None if it doesn't look right."""
    value = value.strip(" :#-\t")
    if not value or len(value) > 200 or _is_noise(value):
        return None
    if field in AMOUNT_FIELDS:
        match = AMOUNT_RE.search(value)
        return match.group(0).strip() if match else None
    if field in DATE_FIELDS:
        match = DATE_RE.search(value)
        return match.group(0) if match else None
    if field in ID_FIELDS:
        token = value.split()[0] if value.split() else ""
        return token if any(c.isdigit() for c in token) and len(token) <= 50 else None
    return value


def _label_candidates(lines: list[str]) -> list[tuple[str, str, str]]:
    """(field, value, label) for values found next to a known label."""
    found = []
    for i, line in enumerate(lines):
        for field, rule in LABEL_RULES:
            match = rule.match(line)
            if not match:
                continue
            if field is None:
                break
            label = line[: match.end()].strip()
            value = _fits(field, line[match.end():])
            ahead = LOOKAHEAD.get(field, DEFAULT_LOOKAHEAD)
            for nxt in lines[i + 1 : i + 1 + ahead]:
                if value:
                    break
                if _is_label(nxt) and field != "description":
                    continue
                value = _fits(field, nxt)
            if value:
                found.append((field, value, label))
            break
    return found


def build_candidates(text: str | None, entities: list[dict] | None) -> list[dict]:
    """Candidate values for the invoice form, suggested fields first."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    candidates: list[Candidate] = []
    by_value: dict[str, Candidate] = {}
    assigned: set[str] = set()

    def add(value: str, source: str, field: str | None = None, entity_type=None, score=None):
        value = value.strip()
        key = _normalize(value)
        if not key or len(value) > 500:
            return
        existing = by_value.get(key)
        if existing:
            if field and not existing.suggested_field and field not in assigned:
                existing.suggested_field = field
                assigned.add(field)
            return
        suggestion = field if field and field not in assigned else None
        if suggestion:
            assigned.add(suggestion)
        candidate = Candidate(
            id=f"c{len(candidates) + 1}",
            value=value,
            source=source,
            entity_type=entity_type,
            score=round(score, 3) if score is not None else None,
            suggested_field=suggestion,
        )
        candidates.append(candidate)
        by_value[key] = candidate

    for field, value, label in _label_candidates(lines):
        add(value, label, field)

    for line in lines:
        for pattern, invoice_type in INVOICE_TYPE_TITLES:
            if pattern.match(line):
                add(invoice_type, line, "invoiceType")
                break
        for field, pattern in ID_PATTERNS:
            for match in pattern.finditer(line):
                add(match.group(0), "Reference number", field)

    # The largest amount on an invoice is almost always its total.
    if "invoiceAmount" not in assigned:
        amounts = [m.group(0).strip() for line in lines for m in AMOUNT_RE.finditer(line)]
        if amounts:
            largest = max(amounts, key=lambda a: float(re.sub(r"[^\d.]", "", a) or 0))
            add(largest, "Largest amount", "invoiceAmount")

    for entity in entities or []:
        value = str(entity.get("text") or "").strip()
        entity_type = entity.get("type")
        score = entity.get("score")
        if len(value) < 2 or _is_noise(value) or (score is not None and score < MIN_ENTITY_SCORE):
            continue
        field = None
        if entity_type == "DATE" and DATE_RE.search(value):
            field = "invoiceDate"
        elif entity_type == "ORGANIZATION" and (score or 0) >= 0.75:
            field = "vendorName"
        add(value, entity_type or "Entity", field, entity_type, score)

    # Invoices usually open with the seller's name.
    if "vendorName" not in assigned and lines and not _is_label(lines[0]):
        add(lines[0], "First line", "vendorName")

    order = list(EXTRACTABLE_FIELDS)
    candidates.sort(
        key=lambda c: (
            order.index(c.suggested_field) if c.suggested_field else len(order),
            int(c.id[1:]),
        )
    )
    return [asdict(c) for c in candidates[:MAX_CANDIDATES]]

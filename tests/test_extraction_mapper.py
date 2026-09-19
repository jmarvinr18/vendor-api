import json
from pathlib import Path

from app.services.extraction_mapper import build_candidates

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_invoice_scanned.json").read_text("utf-8"))


def suggested(candidates):
    return {c["suggested_field"]: c["value"] for c in candidates if c["suggested_field"]}


def test_sample_invoice_suggestions():
    candidates = build_candidates(SAMPLE["text"], SAMPLE["entities"])

    assert suggested(candidates) == {
        "vendorName": "NORTHSTAR OFFICE SUPPLIES",
        "invoiceType": "Standard Invoice",
        "invoiceNo": "INV-2026-0919-0042",
        "invoiceDate": "September 19, 2026",
        "description": "Office workstation accessories",
        "creditTerms": "Payment due within 15 days from invoice date.",
        "invoiceAmount": "₱13,384.00",
        "vatableSales": "₱11,950.00",
        "vat": "₱1,434.00",
    }


def test_each_field_is_suggested_once_and_values_are_unique():
    candidates = build_candidates(SAMPLE["text"], SAMPLE["entities"])

    fields = [c["suggested_field"] for c in candidates if c["suggested_field"]]
    assert len(fields) == len(set(fields))
    values = [c["value"].lower() for c in candidates]
    assert len(values) == len(set(values))
    # Suggested values come first so the vendor sees them without scrolling.
    first_unsuggested = next(i for i, c in enumerate(candidates) if not c["suggested_field"])
    assert all(not c["suggested_field"] for c in candidates[first_unsuggested:])


def test_unlabelled_entities_are_offered_without_a_tag():
    candidates = build_candidates(SAMPLE["text"], SAMPLE["entities"])

    tin = next(c for c in candidates if c["value"] == "123-456-789-000")
    assert tin["suggested_field"] is None
    assert tin["entity_type"] == "OTHER"
    # Table headers are never offered as values.
    assert not any(c["value"] in {"QTY", "UNIT", "AMOUNT"} for c in candidates)


def test_labels_with_values_on_following_lines():
    text = "\n".join([
        "ACME TRADING",
        "SALES INVOICE",
        "No.",
        "0315",
        "Date:",
        "SOLD TO:",
        "OCTOBER 26,2019",
        "VATable Sales",
        "315,625.00",
        "VAT Exempt Sales",
        "COLOR",
        "VAT Amount",
        "37,875.00",
        "TOTAL AMOUNT DUE",
        "Reference:",
        "Checked by:",
        "353,500.00",
        "Vat Reg. Tin: 009-880-081-002",
    ])

    result = suggested(build_candidates(text, []))

    assert result["invoiceNo"] == "0315"
    assert result["invoiceDate"] == "OCTOBER 26,2019"
    assert result["vatableSales"] == "315,625.00"
    assert result["vat"] == "37,875.00"
    # No value next to the total label: fall back to the largest amount.
    assert result["invoiceAmount"] == "353,500.00"
    assert result["invoiceType"] == "Standard Invoice"
    assert result["vendorName"] == "ACME TRADING"
    assert "nonVat" not in result


def test_empty_output():
    assert build_candidates("", []) == []
    assert build_candidates(None, None) == []

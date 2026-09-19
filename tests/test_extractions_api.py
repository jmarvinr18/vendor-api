import io
import json
from pathlib import Path
from unittest.mock import patch

from app.services.storage import S3DocumentStorage, StorageError
from tests.conftest import PDF, PNG, bucket_keys

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_invoice_scanned.json").read_text("utf-8"))


def scan(client, headers, name="scan.pdf", content=PDF):
    return client.post(
        "/api/v1/extractions",
        data={"file": (io.BytesIO(content), name)},
        headers=headers,
        content_type="multipart/form-data",
    )


def complete(app, tmp_path, extraction_id, result=SAMPLE):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    output = app.test_cli_runner().invoke(args=["extraction", "complete", extraction_id, str(path)])
    assert "completed" in output.output, output.output


def test_upload_stores_scan_under_the_pipeline_prefix(client, vendor_headers, aws):
    response = scan(client, vendor_headers, "IMG_0001.png", PNG)

    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "pending"
    assert body["fileName"] == "IMG_0001.png"
    assert body["contentType"] == "image/png"
    assert body["candidates"] == []
    [key] = bucket_keys(aws)
    # The S3 → EventBridge rule for the OCR pipeline matches this prefix.
    assert key.startswith(f"vendor-portal/extractions/{body['id']}/")
    assert "IMG_0001" not in key


def test_poll_returns_text_and_candidates_once_completed(app, client, vendor_headers, tmp_path):
    extraction = scan(client, vendor_headers).get_json()
    complete(app, tmp_path, extraction["id"])

    body = client.get(f"/api/v1/extractions/{extraction['id']}", headers=vendor_headers).get_json()

    assert body["status"] == "completed"
    assert body["text"].startswith("NORTHSTAR OFFICE SUPPLIES")
    assert body["completedAt"]
    suggestions = {c["suggestedField"]: c["value"] for c in body["candidates"] if c["suggestedField"]}
    assert suggestions["invoiceNo"] == "INV-2026-0919-0042"
    assert suggestions["invoiceAmount"] == "₱13,384.00"


def test_failed_extraction_reports_the_error(app, client, vendor_headers):
    extraction = scan(client, vendor_headers).get_json()
    app.test_cli_runner().invoke(args=["extraction", "fail", extraction["id"], "Unreadable scan."])

    body = client.get(f"/api/v1/extractions/{extraction['id']}", headers=vendor_headers).get_json()

    assert body["status"] == "failed"
    assert body["errorMessage"] == "Unreadable scan."
    assert body["candidates"] == []


def test_invalid_file_is_rejected_and_nothing_stored(client, vendor_headers, aws):
    response = scan(client, vendor_headers, "invoice.pdf", b"<html>not a pdf</html>")

    assert response.status_code == 422
    assert "not a valid PDF" in response.get_json()["errors"]["files"][0]
    assert bucket_keys(aws) == []


def test_missing_file(client, vendor_headers):
    response = client.post("/api/v1/extractions", data={}, headers=vendor_headers)
    assert response.status_code == 422


def test_other_vendors_cannot_see_an_extraction(app, client, vendor_headers):
    extraction = scan(client, vendor_headers).get_json()
    from app.database import db
    from app.model import Vendor

    with app.app_context():
        other = Vendor(name="Other Vendor", vendor_code="OTHER-1")
        db.session.add(other)
        db.session.commit()
        other_headers = {"X-Vendor-Id": str(other.id)}

    assert client.get(f"/api/v1/extractions/{extraction['id']}", headers=other_headers).status_code == 404
    assert client.delete(f"/api/v1/extractions/{extraction['id']}", headers=other_headers).status_code == 404


def test_delete_removes_the_row_and_the_file(client, vendor_headers, aws):
    extraction = scan(client, vendor_headers).get_json()

    assert client.delete(f"/api/v1/extractions/{extraction['id']}", headers=vendor_headers).status_code == 204
    assert client.get(f"/api/v1/extractions/{extraction['id']}", headers=vendor_headers).status_code == 404
    assert bucket_keys(aws) == []


def test_storage_outage_leaves_no_row(client, vendor_headers):
    with patch.object(S3DocumentStorage, "save", side_effect=StorageError("down")):
        response = scan(client, vendor_headers)

    assert response.status_code == 503
    from app.model import DocumentExtraction

    with client.application.app_context():
        from app.database import db

        assert db.session.query(DocumentExtraction).count() == 0


def test_reference_data_lists_taggable_fields(client, vendor_headers):
    body = client.get("/api/v1/reference-data", headers=vendor_headers).get_json()
    keys = [f["key"] for f in body["extractableFields"]]
    assert keys[:3] == ["vendorName", "invoiceType", "invoiceNo"]
    assert "invoiceAmount" in keys

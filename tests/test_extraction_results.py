"""Polling picks up the OCR pipeline's processed/<stem>.jsonl output from S3."""

import json
import posixpath
from unittest.mock import patch

import pytest

from app.services.extraction_results import InvalidExtractionOutput, S3ExtractionResultSource, parse_output
from app.services.storage import StorageError
from tests.conftest import BUCKET, bucket_keys
from tests.test_extractions_api import SAMPLE, scan


def upload_scan(client, headers, aws):
    extraction = scan(client, headers).get_json()
    [scan_key] = [k for k in bucket_keys(aws) if extraction["id"] in k]
    stem = posixpath.splitext(posixpath.basename(scan_key))[0]
    return extraction, scan_key, f"processed/{stem}.jsonl"


def poll(client, headers, extraction_id):
    response = client.get(f"/api/v1/extractions/{extraction_id}", headers=headers)
    assert response.status_code == 200
    return response.get_json()


def write_result(aws, key, body):
    aws.put_object(Bucket=BUCKET, Key=key, Body=body.encode("utf-8"))


def test_upload_returns_immediately_while_the_pipeline_runs(client, vendor_headers, aws):
    # The result doesn't exist yet during the upload; that must not fail the request.
    response = scan(client, vendor_headers)
    assert response.status_code == 202
    assert response.get_json()["status"] == "pending"


def test_stays_pending_until_the_result_file_exists(client, vendor_headers, aws):
    extraction, scan_key, result_key = upload_scan(client, vendor_headers, aws)

    assert poll(client, vendor_headers, extraction["id"])["status"] == "pending"

    write_result(aws, result_key, json.dumps({**SAMPLE, "source_key": scan_key, "pii_spans": [], "char_count": 1}))
    body = poll(client, vendor_headers, extraction["id"])

    assert body["status"] == "completed"
    assert body["text"].startswith("NORTHSTAR OFFICE SUPPLIES")
    assert body["completedAt"] and body["errorMessage"] is None
    suggestions = {c["suggestedField"]: c["value"] for c in body["candidates"] if c["suggestedField"]}
    assert suggestions["invoiceNo"] == "INV-2026-0919-0042"


def test_reads_pretty_printed_result_files(client, vendor_headers, aws):
    extraction, scan_key, result_key = upload_scan(client, vendor_headers, aws)
    write_result(aws, result_key, json.dumps({**SAMPLE, "source_key": scan_key}, indent=4))

    assert poll(client, vendor_headers, extraction["id"])["status"] == "completed"


def test_result_is_recorded_once(client, vendor_headers, aws):
    extraction, scan_key, result_key = upload_scan(client, vendor_headers, aws)
    write_result(aws, result_key, json.dumps({**SAMPLE, "source_key": scan_key}))
    first = poll(client, vendor_headers, extraction["id"])

    # Later polls don't read S3 again.
    with patch.object(S3ExtractionResultSource, "fetch", side_effect=AssertionError("fetched again")):
        second = poll(client, vendor_headers, extraction["id"])
    assert second["completedAt"] == first["completedAt"]


def test_blank_text_fails_so_the_ui_stops_polling(client, vendor_headers, aws):
    extraction, scan_key, result_key = upload_scan(client, vendor_headers, aws)
    write_result(aws, result_key, json.dumps({"source_key": scan_key, "text": "  ", "entities": []}))

    body = poll(client, vendor_headers, extraction["id"])

    assert body["status"] == "failed"
    assert "couldn't find any text" in body["errorMessage"]


@pytest.mark.parametrize("content", ["not json", json.dumps({"text": "no source key"})])
def test_unusable_result_fails(client, vendor_headers, aws, content):
    extraction, _, result_key = upload_scan(client, vendor_headers, aws)
    write_result(aws, result_key, content)

    body = poll(client, vendor_headers, extraction["id"])

    assert body["status"] == "failed"
    assert "couldn't read text" in body["errorMessage"]


def test_result_for_a_different_scan_is_not_used(client, vendor_headers, aws):
    extraction, _, result_key = upload_scan(client, vendor_headers, aws)
    write_result(aws, result_key, json.dumps({**SAMPLE, "source_key": "vendor-portal/extractions/other/x.pdf"}))

    assert poll(client, vendor_headers, extraction["id"])["status"] == "failed"


def test_s3_outage_keeps_the_extraction_pending(client, vendor_headers, aws):
    extraction, _, _ = upload_scan(client, vendor_headers, aws)
    with patch.object(S3ExtractionResultSource, "fetch", side_effect=StorageError("down")):
        assert poll(client, vendor_headers, extraction["id"])["status"] == "pending"


def test_result_key_templates():
    key = "extractions/11d2ca2e/7490db87435e435e95933e285a0cafdf.pdf"
    source = S3ExtractionResultSource(None, BUCKET, key_prefix="vendor-portal")
    assert source.result_key(key) == "processed/7490db87435e435e95933e285a0cafdf.jsonl"
    custom = S3ExtractionResultSource(None, BUCKET, key_prefix="vendor-portal", key_template="processed/{key}.jsonl")
    assert custom.result_key(key) == "processed/vendor-portal/extractions/11d2ca2e/7490db87435e435e95933e285a0cafdf.jsonl"


def test_parse_output_accepts_json_lines():
    lines = "\n".join(json.dumps({"source_key": f"k{i}", "text": "t", "entities": []}) for i in range(2))
    assert [o.source_key for o in parse_output(lines)] == ["k0", "k1"]
    with pytest.raises(InvalidExtractionOutput):
        parse_output("")

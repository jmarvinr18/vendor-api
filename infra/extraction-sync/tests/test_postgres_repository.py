"""Runs the repository's SQL against a real PostgreSQL database migrated by the API.

Set SYNC_TEST_DB to "host:port:user:password:dbname" to run; skipped otherwise.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from app import ExtractionSync
from extraction_output import KeyMapper
from repository import PostgresExtractionRepository, pg8000_connector, ssl_context

DB = os.environ.get("SYNC_TEST_DB")
pytestmark = pytest.mark.skipif(not DB, reason="SYNC_TEST_DB not set")


@pytest.fixture
def connect():
    host, port, user, password, dbname = DB.split(":")
    config = {"host": host, "port": port, "username": user, "password": password, "dbname": dbname}
    return pg8000_connector(config, ssl_context("disable", None))


@pytest.fixture
def pending_row(connect):
    conn = connect()
    vendor_id, extraction_id = uuid.uuid4(), uuid.uuid4()
    storage_key = f"extractions/{extraction_id}/{uuid.uuid4().hex}.pdf"
    now = datetime.now(timezone.utc)
    conn.run(
        "INSERT INTO vendors (id, name, is_active, created_at, updated_at) VALUES (:id, 'Test', true, :now, :now)",
        id=vendor_id, now=now,
    )
    conn.run(
        "INSERT INTO document_extractions (id, vendor_id, status, file_name, extension, content_type, "
        "size_bytes, storage_key, created_at) VALUES (:id, :vendor, 'pending', 'scan.pdf', 'pdf', "
        "'application/pdf', 10, :key, :now)",
        id=extraction_id, vendor=vendor_id, key=storage_key, now=now,
    )
    conn.close()
    return storage_key


def fetch(connect, storage_key):
    conn = connect()
    [row] = conn.run(
        "SELECT status, extracted_text, entities, error_message, completed_at FROM document_extractions "
        "WHERE storage_key = :key",
        key=storage_key,
    )
    conn.close()
    return row


def test_complete_updates_all_columns_once(connect, pending_row):
    repo = PostgresExtractionRepository(connect)
    entities = [{"type": "DATE", "text": "September 19, 2026", "score": 0.99}]

    assert repo.complete(pending_row, "INVOICE\n₱13,384.00", entities) is True
    status, text, stored_entities, error, completed_at = fetch(connect, pending_row)
    assert (status, text, error) == ("completed", "INVOICE\n₱13,384.00", None)
    assert stored_entities == entities
    assert completed_at is not None

    # Retried execution: the finished row isn't touched again.
    assert repo.complete(pending_row, "other", []) is False
    assert repo.fail(pending_row, "late failure") is False
    assert fetch(connect, pending_row)[0] == "completed"


def test_fail_sets_message(connect, pending_row):
    repo = PostgresExtractionRepository(connect)
    assert repo.fail(pending_row, "x" * 1500) is True
    status, text, entities, error, completed_at = fetch(connect, pending_row)
    assert (status, text, entities, len(error)) == ("failed", None, None, 1000)
    assert completed_at is not None


def test_end_to_end_with_sample_jsonl(connect, pending_row):
    source_key = "vendor-portal/" + pending_row
    stem = pending_row.rsplit("/", 1)[1].removesuffix(".pdf")
    body = json.dumps(
        {"source_key": source_key, "text": "NORTHSTAR OFFICE SUPPLIES", "entities": [{"type": "ORGANIZATION", "text": "NORTHSTAR OFFICE SUPPLIES", "score": 0.8}], "pii_spans": [], "char_count": 25},
        indent=4,
    )
    sync = ExtractionSync(
        lambda bucket, key: {f"processed/{stem}.jsonl": body}[key],
        PostgresExtractionRepository(connect),
        KeyMapper("vendor-portal", "processed/{stem}.jsonl"),
    )

    result = sync.handle({"bucket": "slaif-bucket", "sourceKey": source_key})

    assert result["results"][0]["status"] == "completed"
    assert tuple(fetch(connect, pending_row)[:2]) == ("completed", "NORTHSTAR OFFICE SUPPLIES")

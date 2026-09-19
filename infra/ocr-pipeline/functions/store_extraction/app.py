"""Stores OCR results for a scanned invoice in the vendor portal database.

Invoked by the ExtractInvoice state machine with either
  {"bucket": ..., "key": ..., "jobId": ...}   after Textract finished, or
  {"bucket": ..., "key": ..., "error": {...}} when an earlier step failed.

It reads the Textract text-detection result, runs Comprehend entity detection on the text,
and updates the `document_extractions` row whose storage_key matches the S3 object (the API
created that row, status "pending", when it stored the file).

Output shape matches sample_invoice_scanned.jsonl: {"text": ..., "entities": [{type, text, score}]}.
"""

import json
import logging
import os
import ssl
from datetime import datetime, timezone

import boto3
import pg8000.native

log = logging.getLogger()
log.setLevel(logging.INFO)

KEY_PREFIX = os.environ.get("KEY_PREFIX", "").strip("/")
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
LANGUAGE_CODE = os.environ.get("LANGUAGE_CODE", "en")
# Comprehend DetectEntities accepts up to 100KB of UTF-8 per request.
COMPREHEND_MAX_BYTES = 95_000
MIN_ENTITY_SCORE = float(os.environ.get("MIN_ENTITY_SCORE", "0.3"))

textract = boto3.client("textract")
comprehend = boto3.client("comprehend")
secrets = boto3.client("secretsmanager")

_db_config = None


def _storage_key(s3_key: str) -> str:
    """The API stores keys without S3_KEY_PREFIX; strip it to match the row."""
    if KEY_PREFIX and s3_key.startswith(KEY_PREFIX + "/"):
        return s3_key[len(KEY_PREFIX) + 1 :]
    return s3_key


def _connect():
    global _db_config
    if _db_config is None:
        # RDS-style secret: {"host", "port", "username", "password", "dbname"}
        _db_config = json.loads(secrets.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"])
    return pg8000.native.Connection(
        user=_db_config["username"],
        password=_db_config["password"],
        host=_db_config["host"],
        port=int(_db_config.get("port", 5432)),
        database=_db_config.get("dbname") or _db_config.get("database"),
        ssl_context=ssl.create_default_context(),
        timeout=10,
    )


def _text_lines(job_id: str) -> list[str]:
    """All LINE blocks of a finished text-detection job, in reading order."""
    lines, token = [], None
    while True:
        kwargs = {"JobId": job_id, "MaxResults": 1000}
        if token:
            kwargs["NextToken"] = token
        page = textract.get_document_text_detection(**kwargs)
        lines += [b["Text"] for b in page.get("Blocks", []) if b["BlockType"] == "LINE"]
        token = page.get("NextToken")
        if not token:
            return lines


def _chunks(lines: list[str]):
    """Groups lines into chunks under Comprehend's size limit, keeping lines whole."""
    chunk, size = [], 0
    for line in lines:
        encoded = len(line.encode("utf-8")) + 1
        if chunk and size + encoded > COMPREHEND_MAX_BYTES:
            yield "\n".join(chunk)
            chunk, size = [], 0
        chunk.append(line[: COMPREHEND_MAX_BYTES // 4])
        size += encoded
    if chunk:
        yield "\n".join(chunk)


def _entities(lines: list[str]) -> list[dict]:
    found = []
    for chunk in _chunks(lines):
        response = comprehend.detect_entities(Text=chunk, LanguageCode=LANGUAGE_CODE)
        found += [
            {"type": e["Type"], "text": e["Text"], "score": e["Score"]}
            for e in response["Entities"]
            if e["Score"] >= MIN_ENTITY_SCORE
        ]
    return found


def _update(storage_key: str, sql: str, **params) -> int:
    conn = _connect()
    try:
        conn.run(sql, storage_key=storage_key, now=datetime.now(timezone.utc), **params)
        return conn.row_count
    finally:
        conn.close()


def handler(event, context):
    storage_key = _storage_key(event["key"])

    if "error" in event:
        error = event["error"] or {}
        log.error("Extraction failed for %s: %s", storage_key, error)
        # The vendor sees this message; keep internal details in the logs.
        _update(
            storage_key,
            "UPDATE document_extractions SET status = 'failed', error_message = :message, "
            "completed_at = :now WHERE storage_key = :storage_key AND status = 'pending'",
            message="We couldn't read text from this document. Please enter the details manually.",
        )
        return {"status": "failed", "storageKey": storage_key}

    lines = _text_lines(event["jobId"])
    text = "\n".join(lines)
    entities = _entities(lines) if lines else []

    updated = _update(
        storage_key,
        "UPDATE document_extractions SET status = 'completed', extracted_text = :text, "
        "entities = CAST(:entities AS json), error_message = NULL, completed_at = :now "
        "WHERE storage_key = :storage_key AND status = 'pending'",
        text=text,
        entities=json.dumps(entities),
    )
    if updated == 0:
        # Discarded by the vendor before OCR finished, or already processed.
        log.warning("No pending extraction for %s; result not stored.", storage_key)
    return {"status": "completed" if updated else "skipped", "storageKey": storage_key, "lines": len(lines)}

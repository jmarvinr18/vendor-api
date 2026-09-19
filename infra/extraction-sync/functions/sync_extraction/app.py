"""SyncExtraction: copies the OCR pipeline's JSONL result into document_extractions.

Added to the ExtractInvoice state machine after the step that writes processed/<...>.jsonl.
Input (one of):
  {"bucket": "...", "key": "processed/<...>.jsonl"}          read this JSONL
  {"bucket": "...", "sourceKey": "vendor-portal/extractions/..."}  JSONL key derived from
                                                              PROCESSED_KEY_TEMPLATE
  {"sourceKey": "...", "error": {...}}                        an earlier step failed

For each record the row is matched on storage_key (the record's source_key without the API's
S3_KEY_PREFIX) and only updated while it is still pending, so retries are safe. Records for
objects that aren't scanned-invoice extractions (e.g. <prefix>/invoices/...) are skipped.
"""

import json
import logging
import os

from extraction_output import ExtractionOutput, InvalidOutput, KeyMapper, parse_output
from repository import ExtractionRepository

log = logging.getLogger()
log.setLevel(logging.INFO)

# Shown to the vendor; the technical cause stays in the logs.
NO_TEXT_MESSAGE = "We couldn't find any text in this document. Please enter the details manually."
FAILED_MESSAGE = "We couldn't read text from this document. Please enter the details manually."


class ExtractionSync:
    def __init__(self, read_object, repository: ExtractionRepository, keys: KeyMapper):
        self._read_object = read_object  # (bucket, key) -> str
        self._repository = repository
        self._keys = keys

    def handle(self, event: dict) -> dict:
        if event.get("error") is not None:
            return self._mark_failed(event)

        bucket = event["bucket"]
        key = event.get("key") or self._keys.processed_key(event["sourceKey"])
        outputs = parse_output(self._read_object(bucket, key))

        expected = event.get("sourceKey")
        if expected and all(o.source_key != expected for o in outputs):
            raise InvalidOutput(f"{key} has no record for {expected}.")

        results = [self._store(o) for o in outputs if not expected or o.source_key == expected]
        return {"processedKey": key, "results": results}

    def _store(self, output: ExtractionOutput) -> dict:
        storage_key = self._keys.storage_key(output.source_key)
        if storage_key is None:
            log.info("Skipping %s: not a scanned-invoice extraction.", output.source_key)
            return {"sourceKey": output.source_key, "status": "skipped", "reason": "not an extraction"}

        if output.text.strip():
            updated = self._repository.complete(storage_key, output.text, output.entities)
            status = "completed"
        else:
            updated = self._repository.fail(storage_key, NO_TEXT_MESSAGE)
            status = "failed"

        if not updated:
            # Discarded by the vendor before OCR finished, or already processed.
            log.warning("No pending extraction for %s; nothing updated.", storage_key)
            return {"storageKey": storage_key, "status": "skipped", "reason": "no pending row"}
        log.info("Extraction %s %s (%d entities).", storage_key, status, len(output.entities))
        return {"storageKey": storage_key, "status": status, "entities": len(output.entities)}

    def _mark_failed(self, event: dict) -> dict:
        source_key = event["sourceKey"]
        log.error("Extraction failed for %s: %s", source_key, json.dumps(event["error"], default=str)[:2000])
        storage_key = self._keys.storage_key(source_key)
        if storage_key is None:
            return {"results": [{"sourceKey": source_key, "status": "skipped", "reason": "not an extraction"}]}
        updated = self._repository.fail(storage_key, FAILED_MESSAGE)
        return {"results": [{"storageKey": storage_key, "status": "failed" if updated else "skipped"}]}


# ---------- Lambda wiring (created once per container) ----------

_sync = None


def _build() -> ExtractionSync:
    import boto3

    from repository import PostgresExtractionRepository, pg8000_connector, ssl_context

    s3 = boto3.client("s3")
    secret = json.loads(
        boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])["SecretString"]
    )
    context = ssl_context(os.environ.get("DB_SSL_MODE", "verify"), os.environ.get("DB_SSL_ROOT_CERT"))

    def read_object(bucket: str, key: str) -> str:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")

    return ExtractionSync(
        read_object,
        PostgresExtractionRepository(pg8000_connector(secret, context)),
        KeyMapper(os.environ.get("KEY_PREFIX", ""), os.environ.get("PROCESSED_KEY_TEMPLATE", "processed/{stem}.jsonl")),
    )


def handler(event, context):
    global _sync
    if _sync is None:
        _sync = _build()
    return _sync.handle(event)

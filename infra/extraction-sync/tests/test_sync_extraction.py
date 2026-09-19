import json
from pathlib import Path

import pytest

from app import FAILED_MESSAGE, NO_TEXT_MESSAGE, ExtractionSync
from extraction_output import InvalidOutput, KeyMapper, parse_output
from repository import ExtractionRepository

SAMPLE = Path(__file__).resolve().parents[3] / "sample_invoice_scanned.jsonl"
SCAN = "vendor-portal/extractions/11d2ca2e-6cdf-424b-8f9c-a5d659562fd1/3953e684b04b456ea2f01b5b3f8606c4.pdf"
STORAGE_KEY = SCAN.removeprefix("vendor-portal/")
PROCESSED = "processed/3953e684b04b456ea2f01b5b3f8606c4.jsonl"


class FakeRepository(ExtractionRepository):
    def __init__(self, pending=(STORAGE_KEY,)):
        self.pending = set(pending)
        self.rows = {}

    def complete(self, storage_key, text, entities):
        if storage_key not in self.pending:
            return False
        self.pending.discard(storage_key)
        self.rows[storage_key] = ("completed", text, entities, None)
        return True

    def fail(self, storage_key, message):
        if storage_key not in self.pending:
            return False
        self.pending.discard(storage_key)
        self.rows[storage_key] = ("failed", None, None, message)
        return True


def record(source_key=SCAN, text="INVOICE\nINV-2026-0042", entities=None):
    return {
        "source_key": source_key,
        "text": text,
        "entities": entities if entities is not None else [{"type": "OTHER", "text": "INV-2026-0042", "score": 0.9}],
        "pii_spans": [{"type": "ADDRESS", "begin": 0, "end": 7}],
        "char_count": len(text),
    }


def make_sync(files, repository):
    reads = []

    def read_object(bucket, key):
        reads.append((bucket, key))
        return files[key]

    sync = ExtractionSync(read_object, repository, KeyMapper("vendor-portal", "processed/{stem}.jsonl"))
    return sync, reads


def test_completes_pending_row_from_explicit_jsonl_key():
    repo = FakeRepository()
    sync, reads = make_sync({PROCESSED: json.dumps(record())}, repo)

    result = sync.handle({"bucket": "slaif-bucket", "key": PROCESSED})

    assert reads == [("slaif-bucket", PROCESSED)]
    assert result["results"] == [{"storageKey": STORAGE_KEY, "status": "completed", "entities": 1}]
    status, text, entities, error = repo.rows[STORAGE_KEY]
    assert (status, text, error) == ("completed", "INVOICE\nINV-2026-0042", None)
    # pii_spans / char_count aren't stored; entities keep the {type, text, score} shape.
    assert entities == [{"type": "OTHER", "text": "INV-2026-0042", "score": 0.9}]


def test_derives_jsonl_key_from_source_key():
    repo = FakeRepository()
    sync, reads = make_sync({PROCESSED: json.dumps(record(), indent=4)}, repo)

    sync.handle({"bucket": "slaif-bucket", "sourceKey": SCAN})

    assert reads == [("slaif-bucket", PROCESSED)]
    assert repo.rows[STORAGE_KEY][0] == "completed"


def test_real_pipeline_sample_file():
    # The pretty-printed sample written by the deployed pipeline.
    [output] = parse_output(SAMPLE.read_text(encoding="utf-8"))
    assert output.source_key == "vendor-portal/invoices/sample_invoice_scanned.pdf"
    assert output.text.startswith("NORTHSTAR OFFICE SUPPLIES")
    assert len(output.entities) == 22 and set(output.entities[0]) == {"type", "text", "score"}


def test_skips_objects_that_are_not_extractions():
    # The same pipeline also processes invoice documents; they have no extraction row.
    repo = FakeRepository()
    other = "vendor-portal/invoices/sample_invoice_scanned.pdf"
    sync, _ = make_sync({"processed/sample_invoice_scanned.jsonl": json.dumps(record(other))}, repo)

    result = sync.handle({"bucket": "b", "sourceKey": other})

    assert result["results"][0]["status"] == "skipped"
    assert repo.rows == {}


def test_empty_text_marks_failed_so_the_ui_stops_polling():
    repo = FakeRepository()
    sync, _ = make_sync({PROCESSED: json.dumps(record(text="   ", entities=[]))}, repo)

    result = sync.handle({"bucket": "b", "key": PROCESSED})

    assert result["results"][0]["status"] == "failed"
    assert repo.rows[STORAGE_KEY] == ("failed", None, None, NO_TEXT_MESSAGE)


def test_already_processed_or_discarded_row_is_left_alone():
    repo = FakeRepository(pending=())
    sync, _ = make_sync({PROCESSED: json.dumps(record())}, repo)

    result = sync.handle({"bucket": "b", "key": PROCESSED})

    assert result["results"][0] == {"storageKey": STORAGE_KEY, "status": "skipped", "reason": "no pending row"}


def test_error_payload_marks_failed_without_reading_s3():
    repo = FakeRepository()
    sync, reads = make_sync({}, repo)

    result = sync.handle({"sourceKey": SCAN, "error": {"Error": "Textract.JobFailed", "Cause": "..."}})

    assert reads == []
    assert result["results"] == [{"storageKey": STORAGE_KEY, "status": "failed"}]
    assert repo.rows[STORAGE_KEY][3] == FAILED_MESSAGE


def test_jsonl_with_several_records():
    second = "vendor-portal/extractions/abc/def.png"
    repo = FakeRepository(pending=(STORAGE_KEY, "extractions/abc/def.png"))
    lines = "\n".join(json.dumps(r) for r in [record(), record(second, text="DR-1")])
    sync, _ = make_sync({"processed/batch.jsonl": lines}, repo)

    result = sync.handle({"bucket": "b", "key": "processed/batch.jsonl"})

    assert [r["status"] for r in result["results"]] == ["completed", "completed"]


def test_source_key_mismatch_is_an_error():
    repo = FakeRepository()
    sync, _ = make_sync({PROCESSED: json.dumps(record("vendor-portal/extractions/x/other.pdf"))}, repo)
    with pytest.raises(InvalidOutput, match="has no record for"):
        sync.handle({"bucket": "b", "sourceKey": SCAN})


@pytest.mark.parametrize("raw", ["", "not json", '{"text": "no source key"}', "[1, 2]"])
def test_invalid_output_raises(raw):
    with pytest.raises(InvalidOutput):
        parse_output(raw)


def test_entity_normalization_drops_junk():
    [output] = parse_output(
        json.dumps(record(entities=[{"type": "DATE", "text": "Sep 19", "score": "0.5"}, {"text": ""}, "x", {"text": "PHP 1"}]))
    )
    assert output.entities == [
        {"type": "DATE", "text": "Sep 19", "score": 0.5},
        {"type": "OTHER", "text": "PHP 1", "score": None},
    ]


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("processed/{stem}.jsonl", "processed/3953e684b04b456ea2f01b5b3f8606c4.jsonl"),
        ("processed/{key}.jsonl", "processed/" + SCAN.removesuffix(".pdf") + ".jsonl"),
        ("processed/{name}.jsonl", "processed/3953e684b04b456ea2f01b5b3f8606c4.pdf.jsonl"),
    ],
)
def test_processed_key_templates(template, expected):
    assert KeyMapper("vendor-portal", template).processed_key(SCAN) == expected

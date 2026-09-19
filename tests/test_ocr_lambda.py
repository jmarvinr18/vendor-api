"""The OCR pipeline's StoreExtraction Lambda, with AWS and the database stubbed out."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

LAMBDA_PATH = Path(__file__).resolve().parents[1] / "infra" / "ocr-pipeline" / "functions" / "store_extraction" / "app.py"


class FakeConnection:
    def __init__(self, log, row_count=1):
        self.log = log
        self.row_count = row_count

    def run(self, sql, **params):
        self.log.append((sql, params))

    def close(self):
        pass


@pytest.fixture
def handler_module(monkeypatch):
    # pg8000 is only installed in the Lambda package.
    monkeypatch.setitem(sys.modules, "pg8000", types.ModuleType("pg8000"))
    monkeypatch.setitem(sys.modules, "pg8000.native", types.ModuleType("pg8000.native"))
    monkeypatch.setenv("DB_SECRET_ARN", "arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:db")
    monkeypatch.setenv("KEY_PREFIX", "vendor-portal")
    spec = importlib.util.spec_from_file_location("store_extraction_app", LAMBDA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_log(handler_module, monkeypatch):
    log = []
    monkeypatch.setattr(handler_module, "_connect", lambda: FakeConnection(log))
    return log


def test_stores_text_and_entities(handler_module, db_log, monkeypatch):
    pages = [
        {"Blocks": [{"BlockType": "PAGE"}, {"BlockType": "LINE", "Text": "NORTHSTAR OFFICE SUPPLIES"}], "NextToken": "p2"},
        {"Blocks": [{"BlockType": "LINE", "Text": "TOTAL:"}, {"BlockType": "WORD", "Text": "TOTAL:"}, {"BlockType": "LINE", "Text": "₱13,384.00"}]},
    ]
    calls = []

    def get_document_text_detection(**kwargs):
        calls.append(kwargs)
        return pages[len(calls) - 1]

    monkeypatch.setattr(handler_module.textract, "get_document_text_detection", get_document_text_detection)
    monkeypatch.setattr(
        handler_module.comprehend,
        "detect_entities",
        lambda Text, LanguageCode: {
            "Entities": [
                {"Type": "ORGANIZATION", "Text": "NORTHSTAR OFFICE SUPPLIES", "Score": 0.8},
                {"Type": "OTHER", "Text": "noise", "Score": 0.1},
            ]
        },
    )

    result = handler_module.handler(
        {"bucket": "docs", "key": "vendor-portal/extractions/abc/123.pdf", "jobId": "job-1"}, None
    )

    assert result == {"status": "completed", "storageKey": "extractions/abc/123.pdf", "lines": 3}
    assert calls[1]["NextToken"] == "p2"
    [(sql, params)] = db_log
    assert "status = 'completed'" in sql and "status = 'pending'" in sql
    # The row is matched on the key without the S3 prefix, as the API stores it.
    assert params["storage_key"] == "extractions/abc/123.pdf"
    assert params["text"] == "NORTHSTAR OFFICE SUPPLIES\nTOTAL:\n₱13,384.00"
    # Low-confidence entities are dropped.
    assert json.loads(params["entities"]) == [
        {"type": "ORGANIZATION", "text": "NORTHSTAR OFFICE SUPPLIES", "score": 0.8}
    ]


def test_marks_failed_with_a_vendor_friendly_message(handler_module, db_log):
    result = handler_module.handler(
        {"bucket": "docs", "key": "vendor-portal/extractions/abc/123.pdf", "error": {"Error": "Textract.JobFailed"}},
        None,
    )

    assert result["status"] == "failed"
    [(sql, params)] = db_log
    assert "status = 'failed'" in sql
    assert "Textract" not in params["message"]


def test_long_text_is_split_under_the_comprehend_limit(handler_module):
    lines = ["x" * 1000] * 250  # ~250KB
    chunks = list(handler_module._chunks(lines))
    assert len(chunks) == 3
    assert all(len(c.encode("utf-8")) <= handler_module.COMPREHEND_MAX_BYTES for c in chunks)
    assert sum(c.count("\n") + 1 for c in chunks) == 250

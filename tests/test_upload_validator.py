import io

import pytest

from app.services.errors import ValidationFailed
from app.services.upload_validator import IncomingFile, UploadValidator, guess_document_type
from tests.conftest import JPG, PDF, PNG


def incoming(name, content):
    return IncomingFile(name, io.BytesIO(content))


def test_accepts_supported_files_and_derives_content_type():
    uploads = UploadValidator().validate(
        [incoming("INV-1.pdf", PDF), incoming("C:\\scans\\receipt.PNG", PNG), incoming("po.jpeg", JPG)],
        ["", "Delivery Receipt"],
        existing_count=0,
    )
    assert [(u.name, u.doc_type, u.content_type, u.size) for u in uploads] == [
        ("INV-1.pdf", "Invoice", "application/pdf", len(PDF)),
        ("receipt.PNG", "Delivery Receipt", "image/png", len(PNG)),
        ("po.jpeg", "Purchase Order", "image/jpeg", len(JPG)),
    ]
    # Streams are rewound for the storage backend.
    assert all(u.stream.tell() == 0 for u in uploads)


@pytest.mark.parametrize(
    ("name", "content", "problem"),
    [
        ("virus.exe", b"MZ", "unsupported format"),
        ("empty.pdf", b"", "file is empty"),
        ("fake.pdf", b"<html>not a pdf</html>", "not a valid PDF"),
        ("big.pdf", b"%PDF-" + b"0" * 2048, "exceeds the"),
    ],
)
def test_rejects_bad_files(name, content, problem):
    validator = UploadValidator(max_file_size=1024)
    with pytest.raises(ValidationFailed) as exc:
        validator.validate([incoming(name, content)], [], existing_count=0)
    assert problem in exc.value.errors["files"][0]


def test_reports_every_problem_at_once():
    with pytest.raises(ValidationFailed) as exc:
        UploadValidator().validate(
            [incoming("ok.pdf", PDF), incoming("a.exe", b"x"), incoming("b.pdf", PDF)],
            ["", "", "Receipt"],
            existing_count=0,
        )
    problems = exc.value.errors["files"]
    assert len(problems) == 2 and "unknown document type 'Receipt'" in problems[1]


def test_enforces_file_count_including_existing_documents():
    with pytest.raises(ValidationFailed) as exc:
        UploadValidator(max_files=3).validate([incoming("a.pdf", PDF), incoming("b.pdf", PDF)], [], existing_count=2)
    assert "up to 3 files" in exc.value.errors["files"][0]


def test_requires_at_least_one_file():
    with pytest.raises(ValidationFailed):
        UploadValidator().validate([], [], existing_count=0)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("INV_001.pdf", "Invoice"), ("pr-22.pdf", "Purchase Order"), ("DR-9.png", "Delivery Receipt"), ("x.pdf", "Other")],
)
def test_guess_document_type(name, expected):
    assert guess_document_type(name) == expected

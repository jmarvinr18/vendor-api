import io

import boto3
import pytest

from app.services.storage import (
    LocalDocumentStorage,
    S3DocumentStorage,
    StorageError,
    StoredObjectNotFound,
    build_document_storage,
)
from tests.conftest import BUCKET, PDF, REGION, bucket_keys


@pytest.fixture
def storage(aws):
    return S3DocumentStorage(boto3.client("s3", region_name=REGION), BUCKET, key_prefix="/vendor-portal/")


def test_save_open_delete_round_trip(storage, aws):
    stored = storage.save("invoices/1/a.pdf", io.BytesIO(PDF), content_type="application/pdf", size=len(PDF))

    assert stored.key == "invoices/1/a.pdf"
    assert bucket_keys(aws) == ["vendor-portal/invoices/1/a.pdf"]
    head = aws.head_object(Bucket=BUCKET, Key="vendor-portal/invoices/1/a.pdf")
    assert head["ContentType"] == "application/pdf"
    assert head["ServerSideEncryption"] == "AES256"
    assert storage.open("invoices/1/a.pdf").read() == PDF

    storage.delete("invoices/1/a.pdf")
    assert bucket_keys(aws) == []


def test_open_missing_object_raises_not_found(storage):
    with pytest.raises(StoredObjectNotFound):
        storage.open("invoices/nope.pdf")


def test_delete_many(storage, aws):
    for i in range(3):
        storage.save(f"k/{i}.pdf", io.BytesIO(PDF), content_type="application/pdf", size=len(PDF))
    storage.delete_many(["k/0.pdf", "k/2.pdf", "k/missing.pdf"])
    assert bucket_keys(aws) == ["vendor-portal/k/1.pdf"]


def test_missing_bucket_is_a_storage_error(aws):
    storage = S3DocumentStorage(boto3.client("s3", region_name=REGION), "no-such-bucket")
    with pytest.raises(StorageError):
        storage.save("a.pdf", io.BytesIO(PDF), content_type="application/pdf", size=len(PDF))


def test_factory_builds_configured_backend(tmp_path, aws):
    s3 = build_document_storage({"STORAGE_BACKEND": "s3", "S3_BUCKET": BUCKET, "S3_REGION": REGION})
    assert isinstance(s3, S3DocumentStorage)
    local = build_document_storage({"STORAGE_BACKEND": "local", "UPLOAD_FOLDER": str(tmp_path)})
    assert isinstance(local, LocalDocumentStorage)
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        build_document_storage({"STORAGE_BACKEND": "s3"})
    with pytest.raises(RuntimeError, match="Unknown STORAGE_BACKEND"):
        build_document_storage({"STORAGE_BACKEND": "ftp"})


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "root")
    with pytest.raises(StorageError):
        storage.save("../outside.pdf", io.BytesIO(PDF), content_type="application/pdf", size=len(PDF))

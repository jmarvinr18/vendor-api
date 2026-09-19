import io
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Never let tests reach real AWS, even if app/.env holds real credentials.
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_DEFAULT_REGION="ap-southeast-1",
)
os.environ.pop("AWS_PROFILE", None)

from flask_migrate import upgrade  # noqa: E402

from app import create_app  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parents[1]
BUCKET = "vendor-portal-test"
REGION = "ap-southeast-1"

PDF = b"%PDF-1.4\n% test document\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


@pytest.fixture
def aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield s3


@pytest.fixture
def app(tmp_path, aws):
    app = create_app(
        "sqlite:///" + (tmp_path / "test.db").as_posix(),
        config_overrides={
            "TESTING": True,
            "STORAGE_BACKEND": "s3",
            "S3_BUCKET": BUCKET,
            "S3_REGION": REGION,
            "S3_ENDPOINT_URL": None,
            "S3_KEY_PREFIX": "vendor-portal",
            "DEFAULT_VENDOR_ID": None,
        },
    )
    with app.app_context():
        upgrade(directory=str(PROJECT_DIR / "migrations"))
    return app


@pytest.fixture
def vendor_headers(app):
    output = app.test_cli_runner().invoke(args=["seed-demo"]).output
    vendor_id = output.split("X-Vendor-Id: ")[1].split()[0]
    return {"X-Vendor-Id": vendor_id}


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def draft(client, vendor_headers):
    body = {
        "invoiceNo": "INV-2026-0900",
        "invoiceDate": "2026-09-10",
        "dateReceived": "2026-09-12",
        "description": "Office supplies - Sep 2026",
        "poPrNo": "PO-2026-0300",
        "invoiceAmount": 11200,
        "vatableSales": 10000,
        "vat": 1200,
        "nonVat": 0,
    }
    response = client.post("/api/v1/invoices", json=body, headers=vendor_headers)
    assert response.status_code == 201
    return response.get_json()


def upload(client, headers, invoice_id, files, doc_types=None):
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    if doc_types:
        data["docType"] = doc_types
    return client.post(
        f"/api/v1/invoices/{invoice_id}/documents",
        data=data,
        headers=headers,
        content_type="multipart/form-data",
    )


def bucket_keys(s3):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []))

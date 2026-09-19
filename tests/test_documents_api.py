from unittest.mock import patch

from app.services.storage import S3DocumentStorage, StorageError
from tests.conftest import BUCKET, JPG, PDF, bucket_keys, upload


def test_upload_stores_documents_in_s3(client, vendor_headers, draft, aws):
    response = upload(
        client, vendor_headers, draft["id"],
        [("INV-2026-0900.pdf", PDF), ("delivery.jpg", JPG)],
        doc_types=["", "Delivery Receipt"],
    )

    assert response.status_code == 201
    docs = response.get_json()
    assert [(d["name"], d["docType"], d["contentType"], d["hasFile"]) for d in docs] == [
        ("INV-2026-0900.pdf", "Invoice", "application/pdf", True),
        ("delivery.jpg", "Delivery Receipt", "image/jpeg", True),
    ]
    keys = bucket_keys(aws)
    assert len(keys) == 2
    assert all(k.startswith(f"vendor-portal/invoices/{draft['id']}/") for k in keys)
    # The vendor's file name never becomes part of the object key.
    assert not any("INV-2026-0900" in k for k in keys)


def test_download_streams_from_s3(client, vendor_headers, draft):
    [doc] = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)]).get_json()

    response = client.get(f"/api/v1/invoices/{draft['id']}/documents/{doc['id']}/file?download=true", headers=vendor_headers)

    assert response.status_code == 200
    assert response.data == PDF
    assert response.mimetype == "application/pdf"
    assert 'attachment; filename=INV-1.pdf' in response.headers["Content-Disposition"]


def test_invalid_upload_stores_nothing(client, vendor_headers, draft, aws):
    response = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF), ("fake.pdf", b"<script>")])

    assert response.status_code == 422
    assert "not a valid PDF" in response.get_json()["errors"]["files"][0]
    assert bucket_keys(aws) == []


def test_remove_document_deletes_the_object(client, vendor_headers, draft, aws):
    [doc] = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)]).get_json()

    response = client.delete(f"/api/v1/invoices/{draft['id']}/documents/{doc['id']}", headers=vendor_headers)

    assert response.status_code == 204
    assert bucket_keys(aws) == []


def test_deleting_a_draft_deletes_its_objects(client, vendor_headers, draft, aws):
    upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF), ("PO-1.pdf", PDF)])
    assert len(bucket_keys(aws)) == 2

    assert client.delete(f"/api/v1/invoices/{draft['id']}", headers=vendor_headers).status_code == 204
    assert bucket_keys(aws) == []


def test_submitted_invoice_documents_are_locked(client, vendor_headers, draft, aws):
    [doc] = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)]).get_json()
    assert client.post(f"/api/v1/invoices/{draft['id']}/submit", headers=vendor_headers).status_code == 200

    assert upload(client, vendor_headers, draft["id"], [("PO-1.pdf", PDF)]).status_code == 409
    assert client.delete(f"/api/v1/invoices/{draft['id']}/documents/{doc['id']}", headers=vendor_headers).status_code == 409
    assert len(bucket_keys(aws)) == 1


def test_s3_outage_returns_503(client, vendor_headers, draft, aws):
    with patch.object(S3DocumentStorage, "save", side_effect=StorageError("down")):
        response = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)])

    assert response.status_code == 503
    assert client.get(f"/api/v1/invoices/{draft['id']}/documents", headers=vendor_headers).get_json() == []


def test_missing_object_returns_404(client, vendor_headers, draft, aws):
    [doc] = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)]).get_json()
    for key in bucket_keys(aws):
        aws.delete_object(Bucket=BUCKET, Key=key)

    response = client.get(f"/api/v1/invoices/{draft['id']}/documents/{doc['id']}/file", headers=vendor_headers)
    assert response.status_code == 404


def test_other_vendors_cannot_reach_documents(client, vendor_headers, draft):
    [doc] = upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)]).get_json()
    response = client.get(
        f"/api/v1/invoices/{draft['id']}/documents/{doc['id']}/file",
        headers={"X-Vendor-Id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 401

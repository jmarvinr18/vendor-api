import io
import uuid

from app.database import db
from app.model import Invoice, InvoiceDocument
from app.services.document_migration import DocumentMigration
from app.services.storage import LocalDocumentStorage
from tests.conftest import BUCKET, PDF, bucket_keys, upload


def add_local_document(app, invoice_id, local, name="old.pdf"):
    key = f"invoices/{invoice_id}/{uuid.uuid4().hex}.pdf"
    local.save(key, io.BytesIO(PDF), content_type="application/pdf", size=len(PDF))
    with app.app_context():
        invoice = db.session.get(Invoice, uuid.UUID(invoice_id))
        invoice.documents.append(
            InvoiceDocument(file_name=name, doc_type="Other", extension="pdf", content_type="application/pdf",
                            size_bytes=len(PDF), storage_path=key)
        )
        db.session.commit()
    return key


def test_push_to_s3_copies_local_documents_once(app, client, vendor_headers, draft, aws, tmp_path):
    local = LocalDocumentStorage(tmp_path / "uploads")
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    upload(client, vendor_headers, draft["id"], [("INV-1.pdf", PDF)])  # already in S3
    local_key = add_local_document(app, draft["id"], local)
    lost_key = f"invoices/{draft['id']}/{uuid.uuid4().hex}.pdf"
    with app.app_context():
        invoice = db.session.get(Invoice, uuid.UUID(draft["id"]))
        invoice.documents.append(InvoiceDocument(file_name="lost.pdf", doc_type="Other", extension="pdf",
                                                 content_type="application/pdf", size_bytes=1, storage_path=lost_key))
        db.session.commit()
    runner = app.test_cli_runner()

    dry = runner.invoke(args=["documents", "push-to-s3", "--dry-run"]).output
    assert f"Would copy: {local_key}" in dry
    assert f"vendor-portal/{local_key}" not in bucket_keys(aws)

    output = runner.invoke(args=["documents", "push-to-s3"]).output
    assert f"Copied: {local_key}" in output
    assert "lost.pdf" in output and "1 already in S3, 1 copied, 1 missing." in output
    head = aws.head_object(Bucket=BUCKET, Key=f"vendor-portal/{local_key}")
    assert head["ContentType"] == "application/pdf" and head["ServerSideEncryption"] == "AES256"

    # Re-running changes nothing.
    again = runner.invoke(args=["documents", "push-to-s3"]).output
    assert "2 already in S3, 0 copied, 1 missing." in again

    # The API can now serve the migrated file from S3.
    with app.app_context():
        doc_id = db.session.query(InvoiceDocument.id).filter_by(storage_path=local_key).scalar()
    response = client.get(f"/api/v1/invoices/{draft['id']}/documents/{doc_id}/file", headers=vendor_headers)
    assert response.status_code == 200 and response.data == PDF


def test_migration_service_with_fakes(app, draft, tmp_path):
    source = LocalDocumentStorage(tmp_path / "src")
    target = LocalDocumentStorage(tmp_path / "dst")
    key = add_local_document(app, draft["id"], source)
    with app.app_context():
        report = DocumentMigration(db.session, source, target).run()
    assert report.copied == [key]
    assert target.open(key).read() == PDF

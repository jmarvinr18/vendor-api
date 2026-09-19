import io
import uuid

import pytest

from app.database import db
from app.model import Invoice, InvoiceDocument
from app.services.document_service import DocumentService
from app.services.errors import StorageUnavailable
from app.services.storage import DocumentStorage, StorageError, StoredObject
from app.services.upload_validator import IncomingFile, UploadValidator
from tests.conftest import PDF


class FlakyStorage(DocumentStorage):
    """In-memory storage that fails on the Nth save."""

    def __init__(self, fail_on_save: int | None = None):
        self.objects: dict[str, bytes] = {}
        self.saves = 0
        self.fail_on_save = fail_on_save

    def save(self, key, stream, *, content_type, size):
        self.saves += 1
        if self.saves == self.fail_on_save:
            raise StorageError("S3 is down")
        self.objects[key] = stream.read()
        return StoredObject(key, size, content_type)

    def open(self, key):
        return io.BytesIO(self.objects[key])

    def delete(self, key):
        self.objects.pop(key, None)


def files(*names):
    return [IncomingFile(name, io.BytesIO(PDF)) for name in names]


def test_upload_is_all_or_nothing_when_storage_fails(app, draft):
    storage = FlakyStorage(fail_on_save=2)
    with app.app_context():
        invoice = db.session.get(Invoice, uuid.UUID(draft["id"]))
        service = DocumentService(db.session, storage, UploadValidator())

        with pytest.raises(StorageUnavailable):
            service.upload(invoice, files("INV-1.pdf", "PO-1.pdf", "DR-1.pdf"), [])

        # The first file was stored, then removed again; nothing was recorded.
        assert storage.objects == {}
        assert db.session.query(InvoiceDocument).filter_by(invoice_id=invoice.id).count() == 0


def test_uses_injected_key_factory(app, draft):
    storage = FlakyStorage()
    with app.app_context():
        invoice = db.session.get(Invoice, uuid.UUID(draft["id"]))
        service = DocumentService(db.session, storage, UploadValidator(), key_factory=lambda inv, ext: f"custom/{ext}")
        [document] = service.upload(invoice, files("INV-1.pdf"), [])

        assert document.storage_path == "custom/pdf"
        assert storage.objects == {"custom/pdf": PDF}

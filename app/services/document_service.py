import logging
import uuid
from collections.abc import Callable
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.model import Invoice, InvoiceDocument
from app.services.errors import NotFound, StorageUnavailable
from app.services.invoice_rules import require_draft
from app.services.storage import DocumentStorage, StorageError, StoredObjectNotFound
from app.services.upload_validator import IncomingFile, UploadValidator

log = logging.getLogger(__name__)


def default_document_key(invoice_id: uuid.UUID, extension: str) -> str:
    # Random object names: the vendor's file name is kept only in the database.
    return f"invoices/{invoice_id}/{uuid.uuid4().hex}.{extension}"


class DocumentService:
    """Invoice and supporting documents: upload, list, read and remove.

    Depends on the DocumentStorage abstraction (S3 in production, local disk in development)
    and an UploadValidator. Neither is created here, so either can be swapped or mocked.
    """

    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        validator: UploadValidator,
        key_factory: Callable[[uuid.UUID, str], str] = default_document_key,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._key_factory = key_factory

    def get(self, invoice: Invoice, document_id: uuid.UUID) -> InvoiceDocument:
        document = self._session.get(InvoiceDocument, document_id)
        if document is None or document.invoice_id != invoice.id:
            raise NotFound("Document not found.")
        return document

    def upload(self, invoice: Invoice, files: list[IncomingFile], doc_types: list[str]) -> list[InvoiceDocument]:
        """Validates every file, stores them, then records them on the invoice.

        All or nothing: if any file fails validation nothing is stored, and if storing or
        saving fails part-way, the objects already stored are removed again.
        """
        require_draft(invoice, "changed")
        uploads = self._validator.validate(files, doc_types, existing_count=len(invoice.documents))

        stored_keys: list[str] = []
        created: list[InvoiceDocument] = []
        try:
            for upload in uploads:
                key = self._key_factory(invoice.id, upload.extension)
                self._storage.save(key, upload.stream, content_type=upload.content_type, size=upload.size)
                stored_keys.append(key)
                document = InvoiceDocument(
                    file_name=upload.name,
                    doc_type=upload.doc_type,
                    extension=upload.extension,
                    content_type=upload.content_type,
                    size_bytes=upload.size,
                    storage_path=key,
                )
                invoice.documents.append(document)
                created.append(document)
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            self._discard(stored_keys)
            if isinstance(exc, StorageError):
                raise StorageUnavailable("The document store is unavailable. Please try again.") from exc
            raise
        return created

    def remove(self, invoice: Invoice, document: InvoiceDocument) -> None:
        require_draft(invoice, "changed")
        key = document.storage_path
        self._session.delete(document)
        self._session.commit()
        if key:
            self._discard([key])

    def open(self, document: InvoiceDocument) -> BinaryIO:
        if not document.storage_path:
            raise NotFound("The file for this document is not available.")
        try:
            return self._storage.open(document.storage_path)
        except StoredObjectNotFound as exc:
            raise NotFound("The file for this document is not available.") from exc
        except StorageError as exc:
            raise StorageUnavailable("The document store is unavailable. Please try again.") from exc

    def discard_files(self, documents: list[InvoiceDocument]) -> None:
        """Removes stored objects for documents whose rows are already gone (e.g. a deleted draft)."""
        self._discard([d.storage_path for d in documents if d.storage_path])

    def _discard(self, keys: list[str]) -> None:
        # Best effort: the database is already correct, so a failure here only leaves an
        # orphaned object behind. Log it for cleanup rather than failing the request.
        if not keys:
            return
        try:
            self._storage.delete_many(keys)
        except StorageError:
            log.warning("Orphaned document objects left in storage: %s", keys)

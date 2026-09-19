import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import EXTRACTION_KEY_PREFIX
from app.model import DocumentExtraction, Vendor
from app.model.vendor import utcnow
from app.services.errors import Conflict, NotFound, StorageUnavailable, ValidationFailed
from app.services.extraction_mapper import build_candidates
from app.services.storage import DocumentStorage, StorageError
from app.services.upload_validator import IncomingFile, UploadValidator

log = logging.getLogger(__name__)


def default_extraction_key(extraction_id: uuid.UUID, extension: str) -> str:
    # The pipeline's EventBridge rule matches on this prefix. Random object name: the
    # vendor's file name only lives in the database.
    return f"{EXTRACTION_KEY_PREFIX}/{extraction_id}/{uuid.uuid4().hex}.{extension}"


class ExtractionService:
    """Scanned invoices for OCR auto-fill.

    upload() stores the file in S3 and records a pending extraction; the S3 → EventBridge →
    Step Functions → Textract → Lambda pipeline then writes the result to the same row
    (see infra/ocr-pipeline). complete()/fail() are what that Lambda does, and are used by the
    `flask extraction` commands to simulate the pipeline in development.
    """

    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        validator: UploadValidator,
        key_factory: Callable[[uuid.UUID, str], str] = default_extraction_key,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._key_factory = key_factory

    def upload(self, vendor: Vendor, file: IncomingFile | None) -> DocumentExtraction:
        if file is None:
            raise ValidationFailed("Attach the scanned invoice in the 'file' field.")
        [upload] = self._validator.validate([file], ["Invoice"], existing_count=0)

        extraction = DocumentExtraction(
            id=uuid.uuid4(),
            vendor_id=vendor.id,
            status="pending",
            file_name=upload.name,
            extension=upload.extension,
            content_type=upload.content_type,
            size_bytes=upload.size,
        )
        extraction.storage_key = self._key_factory(extraction.id, upload.extension)
        # Commit the row first so it exists before the pipeline can possibly finish.
        self._session.add(extraction)
        self._session.commit()
        try:
            self._storage.save(
                extraction.storage_key, upload.stream, content_type=upload.content_type, size=upload.size
            )
        except StorageError as exc:
            self._session.delete(extraction)
            self._session.commit()
            raise StorageUnavailable("The document store is unavailable. Please try again.") from exc
        return extraction

    def get(self, vendor: Vendor, extraction_id: uuid.UUID) -> DocumentExtraction:
        extraction = self._session.get(DocumentExtraction, extraction_id)
        # Another vendor's extraction is reported as missing rather than forbidden.
        if extraction is None or extraction.vendor_id != vendor.id:
            raise NotFound("Extraction not found.")
        return extraction

    def delete(self, vendor: Vendor, extraction_id: uuid.UUID) -> None:
        extraction = self.get(vendor, extraction_id)
        key = extraction.storage_key
        self._session.delete(extraction)
        self._session.commit()
        try:
            self._storage.delete(key)
        except StorageError:
            log.warning("Orphaned scanned invoice left in storage: %s", key)

    @staticmethod
    def candidates(extraction: DocumentExtraction) -> list[dict]:
        if extraction.status != "completed":
            return []
        return build_candidates(extraction.extracted_text, extraction.entities)

    # ----- Written by the OCR pipeline -----

    def _by_key(self, storage_key: str) -> DocumentExtraction:
        extraction = self._session.scalar(
            select(DocumentExtraction).where(DocumentExtraction.storage_key == storage_key)
        )
        if extraction is None:
            raise NotFound(f"No extraction for {storage_key}.")
        return extraction

    def complete(self, storage_key: str, text: str, entities: list[dict]) -> DocumentExtraction:
        extraction = self._by_key(storage_key)
        if extraction.status != "pending":
            raise Conflict(f"Extraction {extraction.id} is already {extraction.status}.")
        extraction.status = "completed"
        extraction.extracted_text = text
        extraction.entities = [
            {"type": e.get("type"), "text": e.get("text"), "score": e.get("score")} for e in entities
        ]
        extraction.error_message = None
        extraction.completed_at = utcnow()
        self._session.commit()
        return extraction

    def fail(self, storage_key: str, message: str) -> DocumentExtraction:
        extraction = self._by_key(storage_key)
        extraction.status = "failed"
        extraction.error_message = message[:1000]
        extraction.completed_at = utcnow()
        self._session.commit()
        return extraction

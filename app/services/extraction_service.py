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
from app.services.extraction_results import (
    ExtractionResultSource,
    InvalidExtractionOutput,
    NoExtractionResults,
)
from app.services.upload_validator import IncomingFile, UploadValidator

log = logging.getLogger(__name__)

# Shown to the vendor; technical details stay in the logs.
NO_TEXT_MESSAGE = "We couldn't find any text in this document. Please enter the details manually."
UNREADABLE_MESSAGE = "We couldn't read text from this document. Please enter the details manually."

def default_extraction_key(extraction_id: uuid.UUID, extension: str) -> str:
    # The pipeline's EventBridge rule matches on this prefix. Random object name: the
    # vendor's file name only lives in the database.
    return f"{EXTRACTION_KEY_PREFIX}/{extraction_id}/{uuid.uuid4().hex}.{extension}"


class ExtractionService:
    """Scanned invoices for OCR auto-fill.

    upload() stores the file in S3 and records a pending extraction. The S3 → EventBridge →
    Step Functions → Textract pipeline then writes its result to processed/<stem>.jsonl, which
    takes seconds to minutes, so it can't be read during the upload. Instead get(), which the
    Vue app polls, picks the result up from the ExtractionResultSource while the row is
    pending and records it with complete()/fail(). Those are also what the
    `flask extraction` commands and the optional SyncExtraction Lambda use.
    """

    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        validator: UploadValidator,
        key_factory: Callable[[uuid.UUID, str], str] = default_extraction_key,
        results: ExtractionResultSource | None = None,
    ):
        self._session = session
        self._storage = storage
        self._validator = validator
        self._key_factory = key_factory
        self._results = results or NoExtractionResults()

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
        if extraction.status == "pending":
            self._collect_result(extraction)
        return extraction

    def _collect_result(self, extraction: DocumentExtraction) -> None:
        """Records the pipeline's result if it's ready. Never fails the poll: while the result
        is missing or the store is unreachable the extraction simply stays pending."""
        try:
            output = self._results.fetch(extraction.storage_key)
        except StorageError:
            return
        except InvalidExtractionOutput:
            log.exception("Unusable OCR result for %s", extraction.storage_key)
            self.fail(extraction.storage_key, UNREADABLE_MESSAGE)
            return
        if output is None:
            return
        if output.text.strip():
            self.complete(extraction.storage_key, output.text, output.entities)
        else:
            self.fail(extraction.storage_key, NO_TEXT_MESSAGE)

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

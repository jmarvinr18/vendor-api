from flask import Flask, current_app

from app.database import db
from app.services.document_service import DocumentService
from app.services.extraction_results import ExtractionResultSource
from app.services.extraction_service import ExtractionService
from app.services.storage import (
    DocumentStorage,
    build_document_storage,
    build_extraction_result_source,
)
from app.services.upload_validator import UploadValidator

_EXTENSION_KEY = "document_storage"
_RESULTS_KEY = "extraction_results"


def init_storage(
    app: Flask,
    storage: DocumentStorage | None = None,
    extraction_results: ExtractionResultSource | None = None,
) -> None:
    """Creates the document storage and the OCR result source once per app.
    Pass either to override it (tests)."""
    app.extensions[_EXTENSION_KEY] = storage or build_document_storage(app.config)
    app.extensions[_RESULTS_KEY] = extraction_results or build_extraction_result_source(app.config)


def get_document_storage() -> DocumentStorage:
    return current_app.extensions[_EXTENSION_KEY]


def get_document_service() -> DocumentService:
    return DocumentService(db.session, get_document_storage(), UploadValidator())


def get_extraction_service() -> ExtractionService:
    return ExtractionService(
        db.session, get_document_storage(), UploadValidator(), results=current_app.extensions[_RESULTS_KEY]
    )

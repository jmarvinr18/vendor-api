from flask import Flask, current_app

from app.database import db
from app.services.document_service import DocumentService
from app.services.extraction_service import ExtractionService
from app.services.storage import DocumentStorage, build_document_storage
from app.services.upload_validator import UploadValidator

_EXTENSION_KEY = "document_storage"


def init_storage(app: Flask, storage: DocumentStorage | None = None) -> None:
    """Creates the document storage once per app. Pass `storage` to override it (tests)."""
    app.extensions[_EXTENSION_KEY] = storage or build_document_storage(app.config)


def get_document_storage() -> DocumentStorage:
    return current_app.extensions[_EXTENSION_KEY]


def get_document_service() -> DocumentService:
    return DocumentService(db.session, get_document_storage(), UploadValidator())


def get_extraction_service() -> ExtractionService:
    return ExtractionService(db.session, get_document_storage(), UploadValidator())

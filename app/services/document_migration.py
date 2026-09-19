"""Copies documents that were stored on local disk into the configured document storage (S3).

For documents uploaded while the API ran with STORAGE_BACKEND=local. Idempotent: documents
already in the target are left alone, so it can be re-run safely.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.model import InvoiceDocument
from app.services.storage import DocumentStorage, StoredObjectNotFound


@dataclass
class MigrationReport:
    already_stored: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    # In neither the target nor the local folder: the file is lost and must be re-uploaded.
    missing: list[InvoiceDocument] = field(default_factory=list)


class DocumentMigration:
    def __init__(self, session: Session, source: DocumentStorage, target: DocumentStorage):
        self._session = session
        self._source = source
        self._target = target

    def run(self, dry_run: bool = False) -> MigrationReport:
        report = MigrationReport()
        documents = (
            self._session.query(InvoiceDocument)
            .filter(InvoiceDocument.storage_path.isnot(None))
            .order_by(InvoiceDocument.uploaded_at)
        )
        for document in documents:
            key = document.storage_path
            if self._target.exists(key):
                report.already_stored.append(key)
                continue
            try:
                stream = self._source.open(key)
            except StoredObjectNotFound:
                report.missing.append(document)
                continue
            with stream:
                if not dry_run:
                    self._target.save(
                        key,
                        stream,
                        content_type=document.content_type or "application/octet-stream",
                        size=document.size_bytes,
                    )
            report.copied.append(key)
        return report

from app.database.db import db
from app.constants import EXTRACTION_STATUSES
from app.model.invoice import _in
from app.model.vendor import utcnow
import uuid


class DocumentExtraction(db.Model):
    """A scanned invoice sent through the OCR pipeline to pre-fill the invoice form.

    The API creates the row (status "pending") when it stores the file in S3. The pipeline's
    Lambda fills in the text and entities and sets the status, matching on storage_key.
    """

    __tablename__ = "document_extractions"
    __table_args__ = (
        db.CheckConstraint(_in("status", EXTRACTION_STATUSES), name="ck_document_extractions_status"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    vendor_id = db.Column(
        db.Uuid, db.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = db.Column(db.String(20), nullable=False, default="pending")
    file_name = db.Column(db.String(255), nullable=False)
    extension = db.Column(db.String(10), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    # Backend-neutral storage key (without the S3 key prefix), e.g. extractions/<id>/<random>.pdf
    storage_key = db.Column(db.String(500), nullable=False, unique=True)
    extracted_text = db.Column(db.Text)
    # [{"type": "DATE", "text": "September 19, 2026", "score": 0.99}, ...]
    entities = db.Column(db.JSON)
    error_message = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=True))

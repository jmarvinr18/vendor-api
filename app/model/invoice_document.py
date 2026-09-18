from app.database.db import db
from app.constants import DOCUMENT_TYPES
from app.model.invoice import _in
from app.model.vendor import utcnow
import uuid


class InvoiceDocument(db.Model):
    __tablename__ = "invoice_documents"
    __table_args__ = (
        db.CheckConstraint(_in("doc_type", DOCUMENT_TYPES), name="ck_invoice_documents_doc_type"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id = db.Column(
        db.Uuid, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name = db.Column(db.String(255), nullable=False)
    doc_type = db.Column(db.String(30), nullable=False, default="Other")
    extension = db.Column(db.String(10), nullable=False)
    content_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer, nullable=False)
    # Path relative to UPLOAD_FOLDER; null when no file is stored (e.g. seeded demo data).
    storage_path = db.Column(db.String(500))
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    invoice = db.relationship("Invoice", back_populates="documents")

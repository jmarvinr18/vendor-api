from app.database.db import db
from app.model.vendor import utcnow
import uuid


class InvoiceStage(db.Model):
    """When an invoice reached each processing stage (one row per stage reached)."""

    __tablename__ = "invoice_stages"
    __table_args__ = (
        db.UniqueConstraint("invoice_id", "stage_index", name="uq_invoice_stages_invoice_stage"),
        db.CheckConstraint("stage_index BETWEEN 0 AND 6", name="ck_invoice_stages_stage_index"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id = db.Column(db.Uuid, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    stage_index = db.Column(db.SmallInteger, nullable=False)
    stage_name = db.Column(db.String(50), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    invoice = db.relationship("Invoice", back_populates="stages")

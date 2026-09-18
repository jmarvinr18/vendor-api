from app.database.db import db
from app.constants import INVOICE_STATUSES
from app.model.vendor import utcnow
import uuid


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Invoice(db.Model):
    __tablename__ = "invoices"
    __table_args__ = (
        db.CheckConstraint(_in("status", INVOICE_STATUSES), name="ck_invoices_status"),
        db.CheckConstraint("current_stage BETWEEN -1 AND 7", name="ck_invoices_current_stage"),
        db.Index("ix_invoices_vendor_status", "vendor_id", "status"),
        db.Index("ix_invoices_vendor_invoice_date", "vendor_id", "invoice_date"),
        db.Index("ix_invoices_vendor_invoice_no", "vendor_id", "invoice_no"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    vendor_id = db.Column(db.Uuid, db.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False)
    # Assigned on submission, e.g. SUB-2026-004217.
    reference_no = db.Column(db.String(30), unique=True)

    # Drafts may be incomplete, so the form fields are nullable; they're validated on submit.
    vendor_name = db.Column(db.String(255))
    invoice_type = db.Column(db.String(50))
    invoice_no = db.Column(db.String(50))
    invoice_date = db.Column(db.Date)
    description = db.Column(db.String(500))
    po_pr_no = db.Column(db.String(50))
    dr_no = db.Column(db.String(50))
    date_received = db.Column(db.Date)
    credit_terms = db.Column(db.String(50))
    invoice_amount = db.Column(db.Numeric(15, 2))
    vatable_sales = db.Column(db.Numeric(15, 2))
    vat = db.Column(db.Numeric(15, 2))
    non_vat = db.Column(db.Numeric(15, 2))

    status = db.Column(db.String(20), nullable=False, default="Draft")
    # Index into constants.STAGES of the stage in progress; 7 once paid, -1 for drafts.
    current_stage = db.Column(db.SmallInteger, nullable=False, default=-1)
    submitted_on = db.Column(db.DateTime(timezone=True))

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    vendor = db.relationship("Vendor", back_populates="invoices")
    documents = db.relationship(
        "InvoiceDocument",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceDocument.uploaded_at",
    )
    comments = db.relationship(
        "InvoiceComment",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceComment.posted_at",
    )
    stages = db.relationship(
        "InvoiceStage",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceStage.stage_index",
    )

    @property
    def stage_times(self):
        """Start time of each stage reached so far, aligned with constants.STAGES."""
        return [stage.started_at for stage in self.stages]

    @property
    def comment_count(self):
        return len(self.comments)

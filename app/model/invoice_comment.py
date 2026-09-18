from app.database.db import db
from app.constants import COMMENT_AUTHORS
from app.model.invoice import _in
from app.model.vendor import utcnow
import uuid


class InvoiceComment(db.Model):
    __tablename__ = "invoice_comments"
    __table_args__ = (
        db.CheckConstraint(_in("author", COMMENT_AUTHORS), name="ck_invoice_comments_author"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id = db.Column(
        db.Uuid, db.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    posted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    invoice = db.relationship("Invoice", back_populates="comments")

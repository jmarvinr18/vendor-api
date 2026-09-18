from app.database.db import db
import uuid
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


class Vendor(db.Model):
    __tablename__ = "vendors"

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    vendor_code = db.Column(db.String(50), unique=True)
    email = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    invoices = db.relationship("Invoice", back_populates="vendor")

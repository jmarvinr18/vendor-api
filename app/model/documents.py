from app.database.db import db
import uuid
from datetime import datetime

class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = db.Column(db.Text)
    source = db.Column(db.Text)
    md_location = db.Column(db.Text)
    file_path = db.Column(db.String)
    status = db.Column(db.String)
    doc_type = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    chunks = db.relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan"
    )    
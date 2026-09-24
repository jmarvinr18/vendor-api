from app.database.db import db
from app.model.invoice import _in
from app.model.vendor import utcnow
import uuid


class AiSession(db.Model):
    """A vendor's conversation with the AI assistant.

    The agent runs as a separate service; this is the portal's own transcript of it."""

    __tablename__ = "ai_sessions"
    __table_args__ = (db.Index("ix_ai_sessions_vendor_updated", "vendor_id", "updated_at"),)

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    vendor_id = db.Column(db.Uuid, db.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    agent_id = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    # {"invoiceId": "..."}: what the vendor was looking at when the conversation started.
    context = db.Column(db.JSON)
    # Kept on the row so the conversation list needs no per-session queries.
    message_count = db.Column(db.Integer, nullable=False, default=0)
    preview = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    messages = db.relationship(
        "AiMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AiMessage.sequence",
    )


class AiMessage(db.Model):
    __tablename__ = "ai_messages"
    __table_args__ = (
        db.CheckConstraint(_in("role", ["user", "assistant"]), name="ck_ai_messages_role"),
        db.UniqueConstraint("session_id", "sequence", name="uq_ai_messages_session_sequence"),
        db.Index("ix_ai_messages_created", "created_at"),
    )

    id = db.Column(db.Uuid, primary_key=True, default=uuid.uuid4)
    session_id = db.Column(db.Uuid, db.ForeignKey("ai_sessions.id", ondelete="CASCADE"), nullable=False)
    # 1, 2, 3 ... within the session: a question is always followed by its answer.
    sequence = db.Column(db.Integer, nullable=False, default=0)
    role = db.Column(db.String(10), nullable=False)
    content = db.Column(db.Text, nullable=False)
    # [{"type": "invoice" | "document" | "policy", "id": "...", "label": "..."}]
    citations = db.Column(db.JSON)
    # What the agent reported for the answer, for cost tracking.
    model = db.Column(db.String(100))
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    tool_calls = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    session = db.relationship("AiSession", back_populates="messages")

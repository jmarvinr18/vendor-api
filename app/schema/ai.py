"""Schemas for the AI message endpoint. Shapes match vendor-portal/src/schema/ai.ts."""

from marshmallow import Schema, fields, validate

from app.schema.fields import UtcDateTime

NOT_BLANK = validate.Regexp(r"\s*\S", error="Cannot be blank.")


class AiCitationSchema(Schema):
    type = fields.Str(metadata={"description": "invoice | document | policy"})
    id = fields.Str()
    label = fields.Str()


class AiMessageSchema(Schema):
    id = fields.UUID()
    role = fields.Str(metadata={"description": "user | assistant"})
    content = fields.Str()
    created_at = UtcDateTime(data_key="createdAt")
    citations = fields.Function(lambda m: m.citations or [], metadata={"description": "AiCitation[]"})


class AiContextSchema(Schema):
    invoice_id = fields.UUID(data_key="invoiceId", load_default=None)


class AiMessageCreateSchema(Schema):
    content = fields.Str(required=True, validate=[validate.Length(max=4000), NOT_BLANK])
    session_id = fields.UUID(
        data_key="sessionId",
        load_default=None,
        allow_none=True,
        metadata={"description": "Continue this conversation. Omit to start a new one."},
    )
    agent_id = fields.Str(
        data_key="agentId",
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=50),
        metadata={"description": "Which agent to route to; defined by the agent service. Ignored with sessionId."},
    )
    context = fields.Nested(
        AiContextSchema,
        load_default=None,
        allow_none=True,
        metadata={"description": "What the vendor was looking at. Ignored with sessionId."},
    )


class AiReplySchema(Schema):
    session_id = fields.UUID(data_key="sessionId")
    user_message = fields.Nested(AiMessageSchema, data_key="userMessage")
    assistant_message = fields.Nested(AiMessageSchema, data_key="assistantMessage")

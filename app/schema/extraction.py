from marshmallow import Schema, fields

from app.schema.fields import UtcDateTime


class ExtractionEntitySchema(Schema):
    type = fields.Str()
    text = fields.Str()
    score = fields.Float(allow_none=True)


class ExtractionCandidateSchema(Schema):
    id = fields.Str()
    value = fields.Str()
    source = fields.Str()
    entity_type = fields.Str(data_key="entityType", allow_none=True)
    score = fields.Float(allow_none=True)
    suggested_field = fields.Str(
        data_key="suggestedField",
        allow_none=True,
        metadata={"description": "Invoice form field this value most likely belongs to."},
    )


class ExtractionSchema(Schema):
    """A scanned invoice and, once the OCR pipeline has finished, what it found."""

    id = fields.UUID()
    status = fields.Str(metadata={"description": "pending | completed | failed"})
    file_name = fields.Str(data_key="fileName")
    extension = fields.Str()
    content_type = fields.Str(data_key="contentType")
    size = fields.Int(attribute="size_bytes")
    text = fields.Str(attribute="extracted_text", allow_none=True)
    entities = fields.List(fields.Nested(ExtractionEntitySchema), allow_none=True)
    candidates = fields.Method("get_candidates")
    error_message = fields.Str(data_key="errorMessage", allow_none=True)
    created_at = UtcDateTime(data_key="createdAt")
    completed_at = UtcDateTime(data_key="completedAt", allow_none=True)

    def get_candidates(self, extraction):
        from app.services.extraction_service import ExtractionService

        return ExtractionCandidateSchema(many=True).dump(ExtractionService.candidates(extraction))

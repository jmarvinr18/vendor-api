from marshmallow import Schema, fields

class DocumentSchema(Schema):
    id = fields.UUID(dump_only=True)
    title = fields.Str(required=True)
    source = fields.Str(required=True)
    doc_type = fields.Str(required=True)
    created_at = fields.DateTime()
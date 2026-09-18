from marshmallow import Schema, fields, validate

from app.constants import COMMENT_MAX_LENGTH
from app.schema.fields import UtcDateTime


class InvoiceCommentSchema(Schema):
    id = fields.UUID(dump_only=True)
    author = fields.Str(dump_only=True)
    message = fields.Str(dump_only=True)
    posted_on = UtcDateTime(attribute="posted_at", data_key="postedOn", dump_only=True)


class InvoiceCommentInputSchema(Schema):
    message = fields.Str(
        required=True,
        validate=[
            validate.Length(max=COMMENT_MAX_LENGTH),
            validate.Regexp(r"\s*\S",error="Message cannot be blank."),
        ],
    )

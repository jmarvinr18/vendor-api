from marshmallow import Schema, fields

from app.schema.fields import UtcDateTime


class InvoiceDocumentSchema(Schema):
    id = fields.UUID(dump_only=True)
    name = fields.Str(attribute="file_name", dump_only=True)
    doc_type = fields.Str(data_key="docType", dump_only=True)
    uploaded_on = UtcDateTime(attribute="uploaded_at", data_key="uploadedOn", dump_only=True)
    size = fields.Int(attribute="size_bytes", dump_only=True)
    extension = fields.Str(dump_only=True)
    content_type = fields.Str(data_key="contentType", dump_only=True)
    has_file = fields.Function(lambda doc: doc.storage_path is not None, data_key="hasFile")


class DocumentDownloadQuerySchema(Schema):
    download = fields.Bool(load_default=False)

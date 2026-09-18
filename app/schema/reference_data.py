from marshmallow import Schema, fields


class UploadRulesSchema(Schema):
    accepted_extensions = fields.List(fields.Str(), data_key="acceptedExtensions")
    max_file_size = fields.Int(data_key="maxFileSize")
    max_files = fields.Int(data_key="maxFiles")


class ReferenceDataSchema(Schema):
    invoice_types = fields.List(fields.Str(), data_key="invoiceTypes")
    credit_terms = fields.List(fields.Str(), data_key="creditTerms")
    invoice_statuses = fields.List(fields.Str(), data_key="invoiceStatuses")
    stages = fields.List(fields.Str())
    document_types = fields.List(fields.Str(), data_key="documentTypes")
    vat_rate = fields.Float(data_key="vatRate")
    upload = fields.Nested(UploadRulesSchema)

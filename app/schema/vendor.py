from marshmallow import Schema, fields


class VendorSchema(Schema):
    id = fields.UUID(dump_only=True)
    name = fields.Str(dump_only=True)
    vendor_code = fields.Str(dump_only=True, data_key="vendorCode")
    email = fields.Str(dump_only=True)

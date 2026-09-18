from flask.views import MethodView
from flask_smorest import Blueprint

from app import constants
from app.schema import ReferenceDataSchema, VendorSchema
from app.services.current_vendor import get_current_vendor

blp = Blueprint(
    "reference_data",
    __name__,
    url_prefix="/api/v1",
    description="Lookup values for the invoice form, and the current vendor",
)


@blp.route("/reference-data")
class ReferenceData(MethodView):
    @blp.response(200, ReferenceDataSchema)
    def get(self):
        """Dropdown options, statuses, stages and upload rules"""
        return {
            "invoice_types": constants.INVOICE_TYPES,
            "credit_terms": constants.CREDIT_TERMS,
            "invoice_statuses": constants.INVOICE_STATUSES,
            "stages": constants.STAGES,
            "document_types": constants.DOCUMENT_TYPES,
            "vat_rate": constants.VAT_RATE,
            "upload": {
                "accepted_extensions": constants.UPLOAD_EXTENSIONS,
                "max_file_size": constants.UPLOAD_MAX_FILE_SIZE,
                "max_files": constants.UPLOAD_MAX_FILES,
            },
        }


@blp.route("/vendors/me")
class CurrentVendor(MethodView):
    @blp.response(200, VendorSchema)
    def get(self):
        """The vendor making the request"""
        return get_current_vendor()

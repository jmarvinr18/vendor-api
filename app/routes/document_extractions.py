from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.storage import get_extraction_service
from app.schema.extraction import ExtractionSchema
from app.services.current_vendor import get_current_vendor
from app.services.upload_validator import IncomingFile

blp = Blueprint(
    "document_extractions",
    __name__,
    url_prefix="/api/v1/extractions",
    description="Scanned invoices read by OCR to pre-fill the Submit Invoice form",
)


@blp.route("")
class ExtractionList(MethodView):
    @blp.response(202, ExtractionSchema)
    @blp.alt_response(422, description="The file failed validation; nothing was stored")
    @blp.alt_response(503, description="The document store (S3) is unavailable; nothing was stored")
    def post(self):
        """Upload a scanned invoice for text extraction

        multipart/form-data with one `file` part (PDF, JPG or PNG, up to 10MB). The file is
        stored in S3, which starts the OCR pipeline; poll GET /extractions/{id} until the
        status is `completed` or `failed`. This does not create an invoice.
        """
        upload = request.files.get("file")
        file = IncomingFile(upload.filename, upload.stream) if upload and upload.filename else None
        return get_extraction_service().upload(get_current_vendor(), file)


@blp.route("/<uuid:extraction_id>")
class ExtractionDetail(MethodView):
    @blp.response(200, ExtractionSchema)
    def get(self, extraction_id):
        """Extraction status, text, entities and candidate values for the invoice form"""
        return get_extraction_service().get(get_current_vendor(), extraction_id)

    @blp.response(204)
    def delete(self, extraction_id):
        """Discard a scanned invoice and its extracted text"""
        get_extraction_service().delete(get_current_vendor(), extraction_id)

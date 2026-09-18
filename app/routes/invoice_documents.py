from flask import request, send_file
from flask.views import MethodView
from flask_smorest import Blueprint, abort

from app.schema import DocumentDownloadQuerySchema, InvoiceDocumentSchema
from app.services import file_storage, invoice_service
from app.services.current_vendor import get_current_vendor

blp = Blueprint(
    "invoice_documents",
    __name__,
    url_prefix="/api/v1/invoices/<uuid:invoice_id>/documents",
    description="Supporting documents attached to an invoice",
)


@blp.route("")
class InvoiceDocumentList(MethodView):
    @blp.response(200, InvoiceDocumentSchema(many=True))
    def get(self, invoice_id):
        """List an invoice's documents"""
        return invoice_service.get_invoice(get_current_vendor(), invoice_id).documents

    @blp.response(201, InvoiceDocumentSchema(many=True))
    @blp.alt_response(422, description="A file failed validation; nothing was saved")
    def post(self, invoice_id):
        """Upload documents to a draft invoice

        multipart/form-data with one or more `files` parts (PDF, JPG or PNG, 10MB each, 10 per
        invoice). Optional `docType` parts, in the same order, set each file's document type;
        otherwise it's guessed from the file name (INV..., PO/PR..., DR...).
        """
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        return invoice_service.add_documents(
            invoice, request.files.getlist("files"), request.form.getlist("docType")
        )


@blp.route("/<uuid:document_id>")
class InvoiceDocumentDetail(MethodView):
    @blp.response(204)
    def delete(self, invoice_id, document_id):
        """Remove a document from a draft invoice"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        invoice_service.remove_document(invoice, invoice_service.get_document(invoice, document_id))


@blp.route("/<uuid:document_id>/file")
class InvoiceDocumentFile(MethodView):
    @blp.arguments(DocumentDownloadQuerySchema, location="query")
    @blp.response(200, content_type="application/octet-stream")
    def get(self, args, invoice_id, document_id):
        """View or download a document (?download=true for an attachment)"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        document = invoice_service.get_document(invoice, document_id)
        path = file_storage.absolute_path(document.storage_path) if document.storage_path else None
        if path is None:
            abort(404, message="The file for this document is not available.")
        return send_file(
            path,
            mimetype=document.content_type or None,
            as_attachment=args["download"],
            download_name=document.file_name,
        )

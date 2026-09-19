from flask import request, send_file
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions.storage import get_document_service
from app.schema import DocumentDownloadQuerySchema, InvoiceDocumentSchema
from app.services import invoice_service
from app.services.current_vendor import get_current_vendor
from app.services.upload_validator import IncomingFile

blp = Blueprint(
    "invoice_documents",
    __name__,
    url_prefix="/api/v1/invoices/<uuid:invoice_id>/documents",
    description="Invoice and supporting documents attached to an invoice (stored in S3)",
)


@blp.route("")
class InvoiceDocumentList(MethodView):
    @blp.response(200, InvoiceDocumentSchema(many=True))
    def get(self, invoice_id):
        """List an invoice's documents"""
        return invoice_service.get_invoice(get_current_vendor(), invoice_id).documents

    @blp.response(201, InvoiceDocumentSchema(many=True))
    @blp.alt_response(422, description="A file failed validation; nothing was stored")
    @blp.alt_response(503, description="The document store (S3) is unavailable; nothing was stored")
    def post(self, invoice_id):
        """Upload the invoice and supporting documents to a draft invoice

        multipart/form-data with one or more `files` parts (PDF, JPG or PNG, 10MB each, 10 per
        invoice). Optional `docType` parts, in the same order, set each file's document type
        (Invoice, Purchase Order, Delivery Receipt, Other); otherwise it's guessed from the file
        name (INV..., PO/PR..., DR...).
        """
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        files = [IncomingFile(f.filename, f.stream) for f in request.files.getlist("files") if f.filename]
        return get_document_service().upload(invoice, files, request.form.getlist("docType"))


@blp.route("/<uuid:document_id>")
class InvoiceDocumentDetail(MethodView):
    @blp.response(204)
    def delete(self, invoice_id, document_id):
        """Remove a document from a draft invoice"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        documents = get_document_service()
        documents.remove(invoice, documents.get(invoice, document_id))


@blp.route("/<uuid:document_id>/file")
class InvoiceDocumentFile(MethodView):
    @blp.arguments(DocumentDownloadQuerySchema, location="query")
    @blp.response(200, content_type="application/octet-stream")
    def get(self, args, invoice_id, document_id):
        """View or download a document (?download=true for an attachment)

        Streamed through the API so the vendor check applies; the bucket stays private.
        """
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        documents = get_document_service()
        document = documents.get(invoice, document_id)
        return send_file(
            documents.open(document),
            mimetype=document.content_type or "application/octet-stream",
            as_attachment=args["download"],
            download_name=document.file_name,
        )

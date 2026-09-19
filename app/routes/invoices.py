from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import (
    InvoiceInputSchema,
    InvoiceListQuerySchema,
    InvoicePageSchema,
    InvoiceSchema,
    InvoiceSummarySchema,
    InvoiceTimelineSchema,
)
from app.extensions.storage import get_document_service
from app.services import invoice_service
from app.services.current_vendor import get_current_vendor

blp = Blueprint(
    "invoices",
    __name__,
    url_prefix="/api/v1/invoices",
    description="Submit invoices and track their status (Submit Invoice / Invoice Status pages)",
)


@blp.route("")
class InvoiceList(MethodView):
    @blp.arguments(InvoiceListQuerySchema, location="query")
    @blp.response(200, InvoicePageSchema)
    def get(self, args):
        """List the vendor's invoices

        Search matches invoice no., PO/PR no. and description. Sorted by invoice date, newest first.
        """
        return invoice_service.list_invoices(get_current_vendor(), args)

    @blp.arguments(InvoiceInputSchema)
    @blp.response(201, InvoiceSchema)
    def post(self, data):
        """Create a draft invoice

        Upload supporting documents to the draft, then call /submit.
        """
        return invoice_service.create_draft(get_current_vendor(), data)


@blp.route("/summary")
class InvoiceSummary(MethodView):
    @blp.response(200, InvoiceSummarySchema)
    def get(self):
        """Invoice counts and totals per status"""
        return invoice_service.status_summary(get_current_vendor())


@blp.route("/<uuid:invoice_id>")
class InvoiceDetail(MethodView):
    @blp.response(200, InvoiceSchema)
    def get(self, invoice_id):
        """Get an invoice with its documents, comments and stage times"""
        return invoice_service.get_invoice(get_current_vendor(), invoice_id)

    @blp.arguments(InvoiceInputSchema)
    @blp.response(200, InvoiceSchema)
    def patch(self, data, invoice_id):
        """Update a draft invoice"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        return invoice_service.update_draft(invoice, data)

    @blp.response(204)
    def delete(self, invoice_id):
        """Discard a draft invoice and its uploaded files"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        invoice_service.delete_draft(invoice, get_document_service())


@blp.route("/<uuid:invoice_id>/submit")
class InvoiceSubmit(MethodView):
    @blp.response(200, InvoiceSchema)
    @blp.alt_response(422, description="The invoice is incomplete; `errors` is keyed by form field")
    @blp.alt_response(409, description="Not a draft, or the invoice no. was already submitted")
    def post(self, invoice_id):
        """Submit a draft invoice for AP validation

        Runs the same checks as the Invoice Details and Supporting Documents steps, assigns a
        reference number and starts the AP Validation stage.
        """
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        return invoice_service.submit(invoice)


@blp.route("/<uuid:invoice_id>/timeline")
class InvoiceTimeline(MethodView):
    @blp.response(200, InvoiceTimelineSchema)
    def get(self, invoice_id):
        """Status message, next step and per-stage progress"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        return invoice_service.timeline(invoice)

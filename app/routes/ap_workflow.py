from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import AdvanceInvoiceSchema, InvoiceSchema, RejectInvoiceSchema
from app.services import invoice_service

# Back-office actions that move invoices through the stages the vendor sees.
# TODO: restrict to AP staff once authentication is in place; these are currently open.
blp = Blueprint(
    "ap_workflow",
    __name__,
    url_prefix="/api/v1/ap/invoices",
    description="AP team actions: advance or reject an invoice (not vendor-facing)",
)


@blp.route("/<uuid:invoice_id>/advance")
class AdvanceInvoice(MethodView):
    @blp.arguments(AdvanceInvoiceSchema)
    @blp.response(200, InvoiceSchema)
    def post(self, data, invoice_id):
        """Move an invoice to its next stage

        AP Validation → Business Approval → Ariba Processing → S/4HANA Transfer →
        Payment Scheduled → Paid. The status follows the stage.
        """
        invoice = invoice_service.get_invoice_for_ap(invoice_id)
        return invoice_service.advance(invoice, data["comment"])


@blp.route("/<uuid:invoice_id>/reject")
class RejectInvoice(MethodView):
    @blp.arguments(RejectInvoiceSchema)
    @blp.response(200, InvoiceSchema)
    def post(self, data, invoice_id):
        """Reject an invoice in AP validation or business approval"""
        invoice = invoice_service.get_invoice_for_ap(invoice_id)
        return invoice_service.reject(invoice, data["reason"])

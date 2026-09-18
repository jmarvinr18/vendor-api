from flask.views import MethodView
from flask_smorest import Blueprint

from app.schema import InvoiceCommentInputSchema, InvoiceCommentSchema
from app.services import invoice_service
from app.services.current_vendor import get_current_vendor

blp = Blueprint(
    "invoice_comments",
    __name__,
    url_prefix="/api/v1/invoices/<uuid:invoice_id>/comments",
    description="Conversation between the vendor and the AP team",
)


@blp.route("")
class InvoiceCommentList(MethodView):
    @blp.response(200, InvoiceCommentSchema(many=True))
    def get(self, invoice_id):
        """List comments, oldest first"""
        return invoice_service.get_invoice(get_current_vendor(), invoice_id).comments

    @blp.arguments(InvoiceCommentInputSchema)
    @blp.response(201, InvoiceCommentSchema)
    def post(self, data, invoice_id):
        """Post a comment as the vendor"""
        invoice = invoice_service.get_invoice(get_current_vendor(), invoice_id)
        return invoice_service.add_comment(invoice, "Vendor", data["message"])

from marshmallow import Schema, fields, validate

from app.constants import (
    COMMENT_MAX_LENGTH,
    CREDIT_TERMS,
    DESCRIPTION_MAX_LENGTH,
    INVOICE_STATUSES,
    INVOICE_TYPES,
)
from app.schema.fields import Money, UtcDateTime
from app.schema.invoice_comment import InvoiceCommentSchema
from app.schema.invoice_document import InvoiceDocumentSchema

NON_NEGATIVE = validate.Range(min=0)
NOT_BLANK = validate.Regexp(r"\s*\S",error="Cannot be blank.")


class InvoiceInputSchema(Schema):
    """Invoice form fields, used to create and update drafts. Everything is optional here;
    the complete set of rules is enforced when the invoice is submitted."""

    vendor_name = fields.Str(data_key="vendorName", allow_none=True, validate=validate.Length(max=255))
    invoice_type = fields.Str(
        data_key="invoiceType", allow_none=True, validate=validate.OneOf(INVOICE_TYPES)
    )
    invoice_no = fields.Str(data_key="invoiceNo", allow_none=True, validate=validate.Length(max=50))
    invoice_date = fields.Date(data_key="invoiceDate", allow_none=True)
    description = fields.Str(allow_none=True, validate=validate.Length(max=DESCRIPTION_MAX_LENGTH))
    po_pr_no = fields.Str(data_key="poPrNo", allow_none=True, validate=validate.Length(max=50))
    dr_no = fields.Str(data_key="drNo", allow_none=True, validate=validate.Length(max=50))
    date_received = fields.Date(data_key="dateReceived", allow_none=True)
    credit_terms = fields.Str(
        data_key="creditTerms", allow_none=True, validate=validate.OneOf(CREDIT_TERMS)
    )
    invoice_amount = Money(data_key="invoiceAmount", allow_none=True, validate=NON_NEGATIVE)
    vatable_sales = Money(data_key="vatableSales", allow_none=True, validate=NON_NEGATIVE)
    vat = Money(allow_none=True, validate=NON_NEGATIVE)
    non_vat = Money(data_key="nonVat", allow_none=True, validate=NON_NEGATIVE)


class InvoiceListQuerySchema(Schema):
    search = fields.Str(load_default="")
    status = fields.Str(load_default=None, validate=validate.OneOf(INVOICE_STATUSES))
    date_from = fields.Date(data_key="dateFrom", load_default=None)
    date_to = fields.Date(data_key="dateTo", load_default=None)
    page = fields.Int(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Int(
        data_key="pageSize", load_default=10, validate=validate.Range(min=1, max=100)
    )


class InvoiceListItemSchema(Schema):
    id = fields.UUID()
    reference_no = fields.Str(data_key="referenceNo")
    invoice_no = fields.Str(data_key="invoiceNo")
    invoice_type = fields.Str(data_key="invoiceType")
    invoice_date = fields.Date(data_key="invoiceDate")
    po_pr_no = fields.Str(data_key="poPrNo")
    dr_no = fields.Str(data_key="drNo")
    description = fields.Str()
    vendor_name = fields.Str(data_key="vendorName")
    credit_terms = fields.Str(data_key="creditTerms")
    date_received = fields.Date(data_key="dateReceived")
    invoice_amount = Money(data_key="invoiceAmount")
    vatable_sales = Money(data_key="vatableSales")
    vat = Money()
    non_vat = Money(data_key="nonVat")
    status = fields.Str()
    submitted_on = UtcDateTime(data_key="submittedOn")
    current_stage = fields.Int(data_key="currentStage")
    stage_times = fields.List(UtcDateTime(), data_key="stageTimes")
    comment_count = fields.Int(data_key="commentCount")
    created_at = UtcDateTime(data_key="createdAt")
    updated_at = UtcDateTime(data_key="updatedAt")


class InvoiceSchema(InvoiceListItemSchema):
    """Full invoice record, matching InvoiceRecord in the Vue app."""

    documents = fields.List(fields.Nested(InvoiceDocumentSchema))
    comments = fields.List(fields.Nested(InvoiceCommentSchema))


class InvoicePageSchema(Schema):
    items = fields.List(fields.Nested(InvoiceListItemSchema))
    total = fields.Int()
    page = fields.Int()
    page_size = fields.Int(data_key="pageSize")
    page_count = fields.Int(data_key="pageCount")


class InvoiceStatusSummarySchema(Schema):
    status = fields.Str()
    count = fields.Int()
    total_amount = Money(data_key="totalAmount")


class InvoiceSummarySchema(Schema):
    total = fields.Int()
    by_status = fields.List(fields.Nested(InvoiceStatusSummarySchema), data_key="byStatus")


class StageViewSchema(Schema):
    label = fields.Str()
    state = fields.Str(metadata={"description": "done | current | rejected | pending"})
    time = UtcDateTime(allow_none=True)
    note = fields.Str()


class StatusInfoSchema(Schema):
    message = fields.Str()
    next_step = fields.Str(attribute="nextStep", data_key="nextStep")
    next_step_detail = fields.Str(attribute="nextStepDetail", data_key="nextStepDetail")
    estimated_time = fields.Str(
        attribute="estimatedTime", data_key="estimatedTime", allow_none=True
    )


class InvoiceTimelineSchema(Schema):
    status = fields.Str()
    status_info = fields.Nested(StatusInfoSchema, data_key="statusInfo")
    stages = fields.List(fields.Nested(StageViewSchema))


class AdvanceInvoiceSchema(Schema):
    comment = fields.Str(
        load_default=None,
        validate=[validate.Length(max=COMMENT_MAX_LENGTH), NOT_BLANK],
        metadata={"description": "Optional AP Team comment to post with the transition."},
    )


class RejectInvoiceSchema(Schema):
    reason = fields.Str(
        required=True,
        validate=[validate.Length(max=COMMENT_MAX_LENGTH), NOT_BLANK],
        metadata={"description": "Posted to the invoice as an AP Team comment."},
    )

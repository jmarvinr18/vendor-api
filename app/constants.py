"""Reference values shared with the Vue app (see vendor-portal/src/config/brand.ts and src/data/invoices.ts)."""

INVOICE_STATUSES = ["Draft", "Submitted", "Under Review", "Approved", "Paid", "Rejected"]

# Processing stages in order. An invoice's current_stage indexes into this list;
# len(STAGES) means fully paid, -1 means still a draft.
STAGES = [
    "Submitted",
    "AP Validation",
    "Business Approval",
    "Ariba Processing",
    "S/4HANA Transfer",
    "Payment Scheduled",
    "Paid",
]

STAGE_SUBMITTED = 0
STAGE_AP_VALIDATION = 1
STAGE_BUSINESS_APPROVAL = 2
STAGE_COMPLETED = len(STAGES)

INVOICE_TYPES = [
    "Standard Invoice",
    "Progress Billing",
    "Credit Memo",
    "Debit Memo",
    "Proforma Invoice",
]

CREDIT_TERMS = [
    "Cash on Delivery",
    "15 Days Net",
    "30 Days Net",
    "45 Days Net",
    "60 Days Net",
]

DOCUMENT_TYPES = ["Invoice", "Purchase Order", "Delivery Receipt", "Other"]

COMMENT_AUTHORS = ["Vendor", "AP Team"]

VAT_RATE = 0.12

UPLOAD_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]
UPLOAD_MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_MAX_FILES = 10

DESCRIPTION_MAX_LENGTH = 500
COMMENT_MAX_LENGTH = 1000

STATUS_INFO = {
    "Draft": {
        "message": "This invoice has not been submitted yet.",
        "nextStep": "Submit Invoice",
        "nextStepDetail": "Complete the invoice details and submit it for AP validation.",
        "estimatedTime": None,
    },
    "Submitted": {
        "message": "Your invoice has been submitted and is now with AP for validation.",
        "nextStep": "AP Validation",
        "nextStepDetail": "The AP team is reviewing your invoice for accuracy and completeness.",
        "estimatedTime": "1 - 2 Business Days",
    },
    "Under Review": {
        "message": "Your invoice passed AP validation and is awaiting business approval.",
        "nextStep": "Business Approval",
        "nextStepDetail": "The requesting business unit is reviewing and approving your invoice.",
        "estimatedTime": "2 - 3 Business Days",
    },
    "Approved": {
        "message": "Your invoice has been approved and is being processed for payment.",
        "nextStep": "Ariba Processing",
        "nextStepDetail": "Your invoice is being processed in the procurement system.",
        "estimatedTime": "1 - 2 Business Days",
    },
    "Paid": {
        "message": "Payment has been released for this invoice.",
        "nextStep": "Completed",
        "nextStepDetail": "No further action is required.",
        "estimatedTime": None,
    },
    "Rejected": {
        "message": "Your invoice was rejected during business approval. Please see the comments.",
        "nextStep": "Resubmit Invoice",
        "nextStepDetail": "Review the comments, correct the issue and submit a new invoice.",
        "estimatedTime": None,
    },
}

STAGE_COPY = {
    "Submitted": {"done": "Invoice submitted by vendor.", "current": "Invoice submitted by vendor."},
    "AP Validation": {
        "done": "Invoice validated by AP team.",
        "current": "Invoice is under validation by AP team.",
    },
    "Business Approval": {
        "done": "Invoice approved by business unit.",
        "current": "Awaiting approval from business unit.",
    },
    "Ariba Processing": {
        "done": "Invoice processed in Ariba.",
        "current": "Invoice is being processed in Ariba.",
    },
    "S/4HANA Transfer": {
        "done": "Invoice posted to S/4HANA.",
        "current": "Invoice is being transferred to S/4HANA.",
    },
    "Payment Scheduled": {
        "done": "Payment scheduled for release.",
        "current": "Payment is being scheduled.",
    },
    "Paid": {"done": "Payment released to vendor.", "current": "Payment is being released."},
}


def status_for_stage(stage: int) -> str:
    """The invoice status shown while the given stage is in progress."""
    if stage <= STAGE_AP_VALIDATION:
        return "Submitted"
    if stage == STAGE_BUSINESS_APPROVAL:
        return "Under Review"
    if stage >= STAGE_COMPLETED:
        return "Paid"
    return "Approved"


# ---------- Invoice scanning (OCR auto-fill) ----------

# pending: stored in S3, waiting for the Textract pipeline; completed / failed: set by the
# pipeline's Lambda (see infra/ocr-pipeline).
EXTRACTION_STATUSES = ["pending", "completed", "failed"]

# Invoice form fields an extracted value can be tagged as (keys match the Vue form).
EXTRACTABLE_FIELDS = {
    "vendorName": "Vendor Name",
    "invoiceType": "Invoice Type",
    "invoiceNo": "Invoice No.",
    "invoiceDate": "Invoice Date",
    "description": "Description",
    "poPrNo": "PO/PR #",
    "drNo": "DR#",
    "dateReceived": "Date Received",
    "creditTerms": "Credit Terms",
    "invoiceAmount": "Invoice Amount",
    "vatableSales": "Vatable Sales",
    "vat": "VAT",
    "nonVat": "Non-Vat",
}

# Storage keys for scanned invoices live under this prefix; the S3 EventBridge rule that
# starts the OCR pipeline matches on it.
EXTRACTION_KEY_PREFIX = "extractions"

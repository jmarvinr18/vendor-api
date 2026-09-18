import math
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from flask_smorest import abort
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from werkzeug.datastructures import FileStorage

from app.constants import (
    DOCUMENT_TYPES,
    STAGE_AP_VALIDATION,
    STAGE_BUSINESS_APPROVAL,
    STAGE_COMPLETED,
    STAGE_COPY,
    STAGE_SUBMITTED,
    STAGES,
    STATUS_INFO,
    UPLOAD_EXTENSIONS,
    UPLOAD_MAX_FILE_SIZE,
    UPLOAD_MAX_FILES,
    status_for_stage,
)
from app.database import db
from app.model import Invoice, InvoiceComment, InvoiceDocument, InvoiceStage, Vendor
from app.services import file_storage

MAX_FILE_SIZE_LABEL = f"{UPLOAD_MAX_FILE_SIZE // (1024 * 1024)}MB"


def _now():
    return datetime.now(timezone.utc)


# ---------- Queries ----------


def list_invoices(vendor: Vendor, args: dict) -> dict:
    query = select(Invoice).where(Invoice.vendor_id == vendor.id)

    if args["status"]:
        query = query.where(Invoice.status == args["status"])
    if args["date_from"]:
        query = query.where(Invoice.invoice_date >= args["date_from"])
    if args["date_to"]:
        query = query.where(Invoice.invoice_date <= args["date_to"])
    term = args["search"].strip()
    if term:
        query = query.where(
            or_(
                Invoice.invoice_no.icontains(term, autoescape=True),
                Invoice.po_pr_no.icontains(term, autoescape=True),
                Invoice.description.icontains(term, autoescape=True),
            )
        )

    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    page_size = args["page_size"]
    page_count = max(1, math.ceil(total / page_size))
    page = min(args["page"], page_count)

    items = db.session.scalars(
        query.options(selectinload(Invoice.stages), selectinload(Invoice.comments))
        .order_by(
            Invoice.invoice_date.desc().nulls_last(),
            Invoice.submitted_on.desc().nulls_last(),
            Invoice.created_at.desc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
    }


def status_summary(vendor: Vendor) -> dict:
    rows = db.session.execute(
        select(Invoice.status, func.count(), func.coalesce(func.sum(Invoice.invoice_amount), 0))
        .where(Invoice.vendor_id == vendor.id)
        .group_by(Invoice.status)
    ).all()
    found = {status: (count, amount) for status, count, amount in rows}
    by_status = [
        {"status": status, "count": found.get(status, (0, 0))[0], "total_amount": found.get(status, (0, 0))[1]}
        for status in STATUS_INFO
    ]
    return {"total": sum(row["count"] for row in by_status), "by_status": by_status}


def get_invoice(vendor: Vendor, invoice_id: uuid.UUID) -> Invoice:
    invoice = db.session.get(Invoice, invoice_id)
    # Another vendor's invoice is reported as missing rather than forbidden.
    if invoice is None or invoice.vendor_id != vendor.id:
        abort(404, message="Invoice not found.")
    return invoice


def get_document(invoice: Invoice, document_id: uuid.UUID) -> InvoiceDocument:
    document = db.session.get(InvoiceDocument, document_id)
    if document is None or document.invoice_id != invoice.id:
        abort(404, message="Document not found.")
    return document


def stage_views(invoice: Invoice) -> list[dict]:
    """Per-stage state for the timeline, same rules as stagesFor() in the Vue app."""
    times = invoice.stage_times
    views = []
    for i, label in enumerate(STAGES):
        state = "pending"
        if i < invoice.current_stage:
            state = "done"
        elif i == invoice.current_stage:
            state = "rejected" if invoice.status == "Rejected" else "current"
        if state == "rejected":
            note = "Invoice rejected by business unit."
        elif state == "pending":
            note = "Pending"
        else:
            note = STAGE_COPY[label][state]
        views.append(
            {"label": label, "state": state, "time": times[i] if i < len(times) else None, "note": note}
        )
    return views


def timeline(invoice: Invoice) -> dict:
    return {
        "status": invoice.status,
        "status_info": STATUS_INFO[invoice.status],
        "stages": stage_views(invoice),
    }


# ---------- Drafts ----------


def _require_draft(invoice: Invoice, action: str):
    if invoice.status != "Draft":
        abort(409, message=f"Only draft invoices can be {action}.")


def _apply_fields(invoice: Invoice, data: dict):
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(invoice, key, value)


def create_draft(vendor: Vendor, data: dict) -> Invoice:
    invoice = Invoice(
        vendor=vendor,
        status="Draft",
        current_stage=-1,
        vendor_name=vendor.name,
        invoice_type="Standard Invoice",
        credit_terms="30 Days Net",
        vat=Decimal("0.00"),
        non_vat=Decimal("0.00"),
    )
    _apply_fields(invoice, data)
    db.session.add(invoice)
    db.session.commit()
    return invoice


def update_draft(invoice: Invoice, data: dict) -> Invoice:
    _require_draft(invoice, "edited")
    _apply_fields(invoice, data)
    db.session.commit()
    return invoice


def delete_draft(invoice: Invoice):
    _require_draft(invoice, "deleted")
    paths = [doc.storage_path for doc in invoice.documents]
    db.session.delete(invoice)
    db.session.commit()
    for path in paths:
        file_storage.delete_file(path)


# ---------- Documents ----------


def guess_doc_type(name: str) -> str:
    upper = name.upper()
    if upper.startswith("INV"):
        return "Invoice"
    if upper.startswith("PO") or upper.startswith("PR"):
        return "Purchase Order"
    if upper.startswith("DR"):
        return "Delivery Receipt"
    return "Other"


def add_documents(invoice: Invoice, uploads: list[FileStorage], doc_types: list[str]) -> list[InvoiceDocument]:
    _require_draft(invoice, "changed")
    uploads = [u for u in uploads if u and u.filename]
    if not uploads:
        abort(422, message="Attach at least one file in the 'files' field.")

    # Validate everything first so a bad file doesn't leave a partial upload behind.
    problems = []
    checked = []
    for i, upload in enumerate(uploads):
        name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1][:255]
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        size = file_storage.file_size(upload)
        doc_type = doc_types[i] if i < len(doc_types) and doc_types[i] else guess_doc_type(name)
        if extension not in UPLOAD_EXTENSIONS:
            problems.append(f"{name}: unsupported format. Use PDF, JPG or PNG.")
        elif size > UPLOAD_MAX_FILE_SIZE:
            problems.append(f"{name}: exceeds the {MAX_FILE_SIZE_LABEL} limit.")
        elif size == 0:
            problems.append(f"{name}: file is empty.")
        elif doc_type not in DOCUMENT_TYPES:
            problems.append(f"{name}: unknown document type '{doc_type}'.")
        checked.append((upload, name, extension, size, doc_type))

    if len(invoice.documents) + len(uploads) > UPLOAD_MAX_FILES:
        problems.append(f"You can upload up to {UPLOAD_MAX_FILES} files.")
    if problems:
        abort(422, message="Some files could not be uploaded.", errors={"files": problems})

    saved_paths = []
    created = []
    try:
        for upload, name, extension, size, doc_type in checked:
            path = file_storage.save_invoice_file(invoice.id, upload, extension)
            saved_paths.append(path)
            document = InvoiceDocument(
                file_name=name,
                doc_type=doc_type,
                extension=extension,
                content_type=upload.mimetype or None,
                size_bytes=size,
                storage_path=path,
            )
            invoice.documents.append(document)
            created.append(document)
        db.session.commit()
    except Exception:
        db.session.rollback()
        for path in saved_paths:
            file_storage.delete_file(path)
        raise
    return created


def remove_document(invoice: Invoice, document: InvoiceDocument):
    _require_draft(invoice, "changed")
    path = document.storage_path
    db.session.delete(document)
    db.session.commit()
    file_storage.delete_file(path)


# ---------- Submission ----------


def validation_errors(invoice: Invoice) -> dict:
    """The same checks the Invoice Details and Supporting Documents steps run in the UI."""
    e = {}
    if not invoice.vendor_name:
        e["vendorName"] = "Vendor name is required."
    if not invoice.invoice_type:
        e["invoiceType"] = "Select an invoice type."
    if not invoice.invoice_no:
        e["invoiceNo"] = "Invoice number is required."
    if not invoice.invoice_date:
        e["invoiceDate"] = "Invoice date is required."
    if not invoice.description:
        e["description"] = "Description is required."
    if not invoice.date_received:
        e["dateReceived"] = "Date received is required."
    elif invoice.invoice_date and invoice.date_received < invoice.invoice_date:
        e["dateReceived"] = "Date received cannot be earlier than the invoice date."
    if not invoice.credit_terms:
        e["creditTerms"] = "Select credit terms."
    if invoice.invoice_amount is None or invoice.invoice_amount <= 0:
        e["invoiceAmount"] = "Enter an amount greater than zero."
    if invoice.vatable_sales is None:
        e["vatableSales"] = "Vatable sales is required."
    if invoice.vat is None:
        e["vat"] = "VAT is required."
    if invoice.non_vat is None:
        e["nonVat"] = "Non-VAT is required."
    if not e.keys() & {"invoiceAmount", "vatableSales", "vat", "nonVat"}:
        breakdown = invoice.vatable_sales + invoice.vat + invoice.non_vat
        if abs(breakdown - invoice.invoice_amount) > Decimal("0.01"):
            e["invoiceAmount"] = (
                f"Vatable Sales + VAT + Non-Vat (PHP {breakdown:,.2f}) must equal the invoice amount."
            )
    if not invoice.documents:
        e["documents"] = "Upload at least one supporting document."
    return e


def _new_reference_no(year: int) -> str:
    for _ in range(10):
        candidate = f"SUB-{year}-{secrets.randbelow(1_000_000):06d}"
        if not db.session.scalar(select(Invoice.id).where(Invoice.reference_no == candidate)):
            return candidate
    raise RuntimeError("Could not allocate a unique reference number.")


def submit(invoice: Invoice) -> Invoice:
    _require_draft(invoice, "submitted")
    errors = validation_errors(invoice)
    if errors:
        abort(422, message="The invoice is incomplete.", errors=errors)

    duplicate = db.session.scalar(
        select(Invoice.reference_no).where(
            Invoice.vendor_id == invoice.vendor_id,
            Invoice.id != invoice.id,
            func.lower(Invoice.invoice_no) == invoice.invoice_no.lower(),
            Invoice.status.not_in(["Draft", "Rejected"]),
        )
    )
    if duplicate:
        abort(409, message=f"Invoice {invoice.invoice_no} was already submitted ({duplicate}).")

    now = _now()
    invoice.reference_no = _new_reference_no(now.year)
    invoice.submitted_on = now
    invoice.status = "Submitted"
    invoice.current_stage = STAGE_AP_VALIDATION
    invoice.stages.append(
        InvoiceStage(stage_index=STAGE_SUBMITTED, stage_name=STAGES[STAGE_SUBMITTED], started_at=now)
    )
    invoice.stages.append(
        InvoiceStage(stage_index=STAGE_AP_VALIDATION, stage_name=STAGES[STAGE_AP_VALIDATION], started_at=now)
    )
    db.session.commit()
    return invoice


# ---------- Comments ----------


def add_comment(invoice: Invoice, author: str, message: str) -> InvoiceComment:
    comment = InvoiceComment(invoice=invoice, author=author, message=message.strip())
    db.session.add(comment)
    db.session.commit()
    return comment


# ---------- AP workflow ----------


def get_invoice_for_ap(invoice_id: uuid.UUID) -> Invoice:
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None:
        abort(404, message="Invoice not found.")
    return invoice


def advance(invoice: Invoice, comment: str | None) -> Invoice:
    """Moves a submitted invoice to its next processing stage."""
    if invoice.status in ("Draft", "Rejected", "Paid"):
        abort(409, message=f"A {invoice.status.lower()} invoice cannot be advanced.")

    now = _now()
    next_stage = invoice.current_stage + 1
    if next_stage < STAGE_COMPLETED:
        invoice.stages.append(InvoiceStage(stage_index=next_stage, stage_name=STAGES[next_stage], started_at=now))
    invoice.current_stage = next_stage
    invoice.status = status_for_stage(next_stage)
    if comment:
        invoice.comments.append(InvoiceComment(author="AP Team", message=comment.strip(), posted_at=now))
    db.session.commit()
    return invoice


def reject(invoice: Invoice, reason: str) -> Invoice:
    """Rejects an invoice during AP validation or business approval."""
    if invoice.status not in ("Submitted", "Under Review"):
        abort(409, message="Only invoices in AP validation or business approval can be rejected.")

    now = _now()
    # Rejected invoices stop at Business Approval, as in the UI.
    if invoice.current_stage < STAGE_BUSINESS_APPROVAL:
        invoice.stages.append(
            InvoiceStage(
                stage_index=STAGE_BUSINESS_APPROVAL, stage_name=STAGES[STAGE_BUSINESS_APPROVAL], started_at=now
            )
        )
    invoice.current_stage = STAGE_BUSINESS_APPROVAL
    invoice.status = "Rejected"
    invoice.comments.append(InvoiceComment(author="AP Team", message=f"Invoice rejected: {reason.strip()}", posted_at=now))
    db.session.commit()
    return invoice

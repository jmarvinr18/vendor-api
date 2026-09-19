import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import click
from flask.cli import AppGroup
from sqlalchemy import select

from app.constants import STAGES
from app.database import db
from app.model import DocumentExtraction, Invoice, InvoiceComment, InvoiceDocument, InvoiceStage, Vendor

# Same demo data as vendor-portal/src/data/invoices.ts.
SEEDS = [
    ("0524", "2026-05-20", "0158", "Office supplies and stationery - May 2026", 125000, "Submitted"),
    ("0523", "2026-05-17", "0145", "IT accessories - May 2026", 98750, "Under Review"),
    ("0522", "2026-05-14", "0130", "Office furniture - May 2026", 250000, "Approved"),
    ("0521", "2026-05-10", "0127", "Printing services - Apr 2026", 75300, "Paid"),
    ("0520", "2026-05-05", "0108", "Maintenance services - Apr 2026", 120000, "Paid"),
    ("0519", "2026-04-30", "0099", "Consulting services - Apr 2026", 180000, "Rejected"),
    ("0518", "2026-04-28", "0098", "Software license renewal - Apr 2026", 89000, "Draft"),
    ("0517", "2026-04-25", "0096", "Janitorial services - Apr 2026", 64500, "Paid"),
    ("0516", "2026-04-22", "0093", "Courier services - Apr 2026", 18750, "Paid"),
    ("0515", "2026-04-18", "0090", "Printer toner cartridges - Apr 2026", 42300, "Paid"),
    ("0514", "2026-04-15", "0087", "Network cabling works - Apr 2026", 156000, "Paid"),
    ("0513", "2026-04-11", "0084", "Pantry supplies - Apr 2026", 22480, "Paid"),
    ("0512", "2026-04-08", "0081", "Security guard services - Mar 2026", 210000, "Paid"),
    ("0511", "2026-04-03", "0078", "Laptop repair services - Mar 2026", 35600, "Rejected"),
    ("0510", "2026-03-30", "0075", "Office chairs - Mar 2026", 97500, "Paid"),
    ("0509", "2026-03-26", "0072", "Aircon maintenance - Mar 2026", 48000, "Paid"),
    ("0508", "2026-03-21", "0069", "Event venue rental - Mar 2026", 185000, "Paid"),
    ("0507", "2026-03-17", "0066", "Stationery restock - Mar 2026", 15920, "Paid"),
    ("0506", "2026-03-12", "0063", "Cloud hosting - Feb 2026", 132750, "Paid"),
    ("0505", "2026-03-06", "0060", "Team building catering - Feb 2026", 58400, "Paid"),
    ("0504", "2026-03-02", "0057", "Pest control services - Feb 2026", 12500, "Paid"),
    ("0503", "2026-02-25", "0054", "Signage printing - Feb 2026", 27300, "Paid"),
    ("0502", "2026-02-19", "0051", "Water dispenser rental - Feb 2026", 9800, "Paid"),
    ("0501", "2026-02-12", "0048", "Training services - Jan 2026", 145000, "Paid"),
]

STAGE_FOR_STATUS = {"Draft": -1, "Submitted": 1, "Under Review": 2, "Approved": 3, "Rejected": 2, "Paid": 7}
# Hours after submission at which each stage starts.
STAGE_OFFSETS_HOURS = [0, 25 / 60, 26, 50, 52, 75, 240]
CENT = Decimal("0.01")


def register_cli(app):
    @app.cli.command("seed-demo")
    @click.option("--vendor-name", default="ABC Supplies Inc.", show_default=True)
    def seed_demo(vendor_name):
        """Create a demo vendor with the same invoices as the Vue app's mock data."""
        vendor = db.session.scalar(select(Vendor).where(Vendor.vendor_code == "DEMO-001"))
        if vendor is None:
            vendor = Vendor(name=vendor_name, vendor_code="DEMO-001", email="billing@example.com")
            db.session.add(vendor)
        elif vendor.invoices:
            click.echo(f"Demo vendor already seeded. X-Vendor-Id: {vendor.id}")
            return

        for index, seed in enumerate(SEEDS):
            db.session.add(_build_invoice(vendor, index, *seed))
        db.session.commit()
        click.echo(f"Seeded {len(SEEDS)} invoices for {vendor.name}")
        click.echo(f"X-Vendor-Id: {vendor.id}  (or set DEFAULT_VENDOR_ID in app/.env)")

    extraction = AppGroup("extraction", help="Simulate the OCR pipeline in development.")
    app.cli.add_command(extraction)

    @extraction.command("list")
    def extraction_list():
        """Show recent scanned-invoice extractions."""
        rows = db.session.scalars(
            select(DocumentExtraction).order_by(DocumentExtraction.created_at.desc()).limit(20)
        )
        for row in rows:
            click.echo(f"{row.id}  {row.status:<9}  {row.file_name}")

    @extraction.command("complete")
    @click.argument("extraction_id", type=click.UUID)
    @click.argument("result_file", type=click.File("r", encoding="utf-8"))
    def extraction_complete(extraction_id, result_file):
        """Record OCR output for an extraction, as the pipeline's Lambda would.

        RESULT_FILE is the Lambda's JSON output: {"text": "...", "entities": [...]}
        (e.g. sample_invoice_scanned.jsonl).
        """
        from app.extensions.storage import get_extraction_service

        row = db.session.get(DocumentExtraction, extraction_id)
        if row is None:
            raise click.ClickException("Extraction not found.")
        result = json.load(result_file)
        get_extraction_service().complete(
            row.storage_key, result.get("text") or "", result.get("entities") or []
        )
        click.echo(f"Extraction {extraction_id} completed.")

    @extraction.command("fail")
    @click.argument("extraction_id", type=click.UUID)
    @click.argument("message", default="Text could not be extracted from this document.")
    def extraction_fail(extraction_id, message):
        """Mark an extraction as failed, as the pipeline would on a Textract error."""
        from app.extensions.storage import get_extraction_service

        row = db.session.get(DocumentExtraction, extraction_id)
        if row is None:
            raise click.ClickException("Extraction not found.")
        get_extraction_service().fail(row.storage_key, message)
        click.echo(f"Extraction {extraction_id} marked failed.")


def _build_invoice(vendor, index, no, day, po, description, amount, status):
    invoice_date = date.fromisoformat(day)
    amount = Decimal(amount).quantize(CENT)
    vatable = (amount / Decimal("1.12")).quantize(CENT, ROUND_HALF_UP)
    dr_no = f"DR-2026-{487 - index * 7:04d}"
    submitted_on = None
    if status != "Draft":
        # 2:45 PM Manila time.
        submitted_on = datetime.combine(invoice_date, datetime.min.time(), timezone.utc) + timedelta(hours=6, minutes=45)
    current_stage = STAGE_FOR_STATUS[status]

    invoice = Invoice(
        vendor=vendor,
        reference_no=f"SUB-2026-{int(no):06d}" if submitted_on else None,
        vendor_name=vendor.name,
        invoice_type="Standard Invoice",
        invoice_no=f"INV-2026-{no}",
        invoice_date=invoice_date,
        description=description,
        po_pr_no=f"PO-2026-{po}",
        dr_no=dr_no,
        date_received=invoice_date,
        credit_terms="30 Days Net",
        invoice_amount=amount,
        vatable_sales=vatable,
        vat=amount - vatable,
        non_vat=Decimal("0.00"),
        status=status,
        current_stage=current_stage,
        submitted_on=submitted_on,
    )

    stage_times = []
    if submitted_on:
        reached = min(current_stage + 1, len(STAGES))
        stage_times = [submitted_on + timedelta(hours=h) for h in STAGE_OFFSETS_HOURS[:reached]]
        for i, started_at in enumerate(stage_times):
            invoice.stages.append(InvoiceStage(stage_index=i, stage_name=STAGES[i], started_at=started_at))

    uploaded_at = submitted_on or datetime.combine(invoice_date, datetime.min.time(), timezone.utc) + timedelta(hours=2)
    for name, doc_type, kb in (
        (f"Invoice_2026-{no}.pdf", "Invoice", 245),
        (f"PO-2026-{po}.pdf", "Purchase Order", 198),
        (f"{dr_no}.pdf", "Delivery Receipt", 176),
    ):
        # Metadata only; there is no file behind demo documents.
        invoice.documents.append(
            InvoiceDocument(
                file_name=name,
                doc_type=doc_type,
                extension="pdf",
                content_type="application/pdf",
                size_bytes=kb * 1024,
                uploaded_at=uploaded_at,
            )
        )

    if submitted_on:
        if no == "0524":
            invoice.comments.append(
                InvoiceComment(
                    author="Vendor",
                    message="Please let us know if you need additional documents.",
                    posted_at=submitted_on + timedelta(minutes=3),
                )
            )
        invoice.comments.append(
            InvoiceComment(
                author="AP Team",
                message="Invoice received and is now under AP validation.",
                posted_at=stage_times[1],
            )
        )
        if status == "Rejected":
            invoice.comments.append(
                InvoiceComment(
                    author="AP Team",
                    message=(
                        "Invoice rejected: the invoice amount does not match the approved PO amount. "
                        "Please coordinate with your requestor and resubmit."
                    ),
                    posted_at=stage_times[2] + timedelta(hours=4),
                )
            )
        if status == "Paid":
            invoice.comments.append(
                InvoiceComment(
                    author="AP Team",
                    message="Payment has been released. Please allow 1 - 2 banking days for crediting.",
                    posted_at=stage_times[-1],
                )
            )
    return invoice

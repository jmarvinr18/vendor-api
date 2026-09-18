"""Create vendor portal tables: vendors, invoices, invoice_documents, invoice_comments, invoice_stages

Revision ID: 0001_vendor_portal
Revises:
Create Date: 2026-09-18

"""
from alembic import op
import sqlalchemy as sa


revision = "0001_vendor_portal"
down_revision = None
branch_labels = None
depends_on = None

INVOICE_STATUSES = ("Draft", "Submitted", "Under Review", "Approved", "Paid", "Rejected")
DOCUMENT_TYPES = ("Invoice", "Purchase Order", "Delivery Receipt", "Other")
COMMENT_AUTHORS = ("Vendor", "AP Team")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade():
    op.create_table(
        "vendors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("vendor_code", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_vendors"),
        sa.UniqueConstraint("vendor_code", name="uq_vendors_vendor_code"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("reference_no", sa.String(length=30), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("invoice_type", sa.String(length=50), nullable=True),
        sa.Column("invoice_no", sa.String(length=50), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("po_pr_no", sa.String(length=50), nullable=True),
        sa.Column("dr_no", sa.String(length=50), nullable=True),
        sa.Column("date_received", sa.Date(), nullable=True),
        sa.Column("credit_terms", sa.String(length=50), nullable=True),
        sa.Column("invoice_amount", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("vatable_sales", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("vat", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("non_vat", sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_stage", sa.SmallInteger(), nullable=False),
        sa.Column("submitted_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendors.id"], name="fk_invoices_vendor_id_vendors", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("reference_no", name="uq_invoices_reference_no"),
        sa.CheckConstraint(_in("status", INVOICE_STATUSES), name="ck_invoices_status"),
        sa.CheckConstraint("current_stage BETWEEN -1 AND 7", name="ck_invoices_current_stage"),
    )
    op.create_index("ix_invoices_vendor_status", "invoices", ["vendor_id", "status"])
    op.create_index("ix_invoices_vendor_invoice_date", "invoices", ["vendor_id", "invoice_date"])
    op.create_index("ix_invoices_vendor_invoice_no", "invoices", ["vendor_id", "invoice_no"])

    op.create_table(
        "invoice_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=False),
        sa.Column("extension", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_documents"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name="fk_invoice_documents_invoice_id_invoices", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("doc_type", DOCUMENT_TYPES), name="ck_invoice_documents_doc_type"),
    )
    op.create_index("ix_invoice_documents_invoice_id", "invoice_documents", ["invoice_id"])

    op.create_table(
        "invoice_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("author", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_comments"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name="fk_invoice_comments_invoice_id_invoices", ondelete="CASCADE"
        ),
        sa.CheckConstraint(_in("author", COMMENT_AUTHORS), name="ck_invoice_comments_author"),
    )
    op.create_index("ix_invoice_comments_invoice_id", "invoice_comments", ["invoice_id"])

    op.create_table(
        "invoice_stages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("stage_index", sa.SmallInteger(), nullable=False),
        sa.Column("stage_name", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_stages"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name="fk_invoice_stages_invoice_id_invoices", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("invoice_id", "stage_index", name="uq_invoice_stages_invoice_stage"),
        sa.CheckConstraint("stage_index BETWEEN 0 AND 6", name="ck_invoice_stages_stage_index"),
    )


def downgrade():
    op.drop_table("invoice_stages")
    op.drop_index("ix_invoice_comments_invoice_id", table_name="invoice_comments")
    op.drop_table("invoice_comments")
    op.drop_index("ix_invoice_documents_invoice_id", table_name="invoice_documents")
    op.drop_table("invoice_documents")
    op.drop_index("ix_invoices_vendor_invoice_no", table_name="invoices")
    op.drop_index("ix_invoices_vendor_invoice_date", table_name="invoices")
    op.drop_index("ix_invoices_vendor_status", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("vendors")

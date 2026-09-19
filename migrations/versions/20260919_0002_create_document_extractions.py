"""Create document_extractions for scanned-invoice OCR results

Revision ID: 0002_document_extractions
Revises: 0001_vendor_portal
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


revision = "0002_document_extractions"
down_revision = "0001_vendor_portal"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=10), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')", name="ck_document_extractions_status"
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendors.id"], name="fk_document_extractions_vendor_id_vendors",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_extractions"),
        sa.UniqueConstraint("storage_key", name="uq_document_extractions_storage_key"),
    )
    op.create_index(
        "ix_document_extractions_vendor_id", "document_extractions", ["vendor_id"], unique=False
    )


def downgrade():
    op.drop_index("ix_document_extractions_vendor_id", table_name="document_extractions")
    op.drop_table("document_extractions")

# Database schema

Created by migration `0001_vendor_portal`
([migrations/versions/20260918_0001_create_vendor_portal_tables.py](../migrations/versions/20260918_0001_create_vendor_portal_tables.py)).

```mermaid
erDiagram
    vendors ||--o{ invoices : "submits"
    invoices ||--o{ invoice_documents : "has supporting"
    invoices ||--o{ invoice_comments : "has"
    invoices ||--o{ invoice_stages : "reached"

    vendors {
        uuid id PK
        varchar_255 name "NOT NULL"
        varchar_50 vendor_code UK
        varchar_255 email
        boolean is_active "NOT NULL"
        timestamptz created_at "NOT NULL"
        timestamptz updated_at "NOT NULL"
    }

    invoices {
        uuid id PK
        uuid vendor_id FK "NOT NULL, ON DELETE RESTRICT"
        varchar_30 reference_no UK "SUB-YYYY-NNNNNN, set on submit"
        varchar_255 vendor_name
        varchar_50 invoice_type "Standard Invoice, Progress Billing, ..."
        varchar_50 invoice_no
        date invoice_date
        varchar_500 description
        varchar_50 po_pr_no
        varchar_50 dr_no
        date date_received
        varchar_50 credit_terms "30 Days Net, ..."
        numeric_15_2 invoice_amount
        numeric_15_2 vatable_sales
        numeric_15_2 vat
        numeric_15_2 non_vat
        varchar_20 status "NOT NULL, CHECK in 6 statuses"
        smallint current_stage "NOT NULL, -1 draft .. 7 paid"
        timestamptz submitted_on
        timestamptz created_at "NOT NULL"
        timestamptz updated_at "NOT NULL"
    }

    invoice_documents {
        uuid id PK
        uuid invoice_id FK "NOT NULL, ON DELETE CASCADE"
        varchar_255 file_name "NOT NULL, original name"
        varchar_30 doc_type "NOT NULL, CHECK"
        varchar_10 extension "NOT NULL"
        varchar_100 content_type
        integer size_bytes "NOT NULL"
        varchar_500 storage_path "relative to UPLOAD_FOLDER"
        timestamptz uploaded_at "NOT NULL"
    }

    invoice_comments {
        uuid id PK
        uuid invoice_id FK "NOT NULL, ON DELETE CASCADE"
        varchar_20 author "NOT NULL, Vendor | AP Team"
        varchar_1000 message "NOT NULL"
        timestamptz posted_at "NOT NULL"
    }

    invoice_stages {
        uuid id PK
        uuid invoice_id FK "NOT NULL, ON DELETE CASCADE"
        smallint stage_index "NOT NULL, 0..6, UK with invoice_id"
        varchar_50 stage_name "NOT NULL"
        timestamptz started_at "NOT NULL"
    }
```

## Tables

| Table | Serves (Vue app) | Notes |
| --- | --- | --- |
| `vendors` | Header / "current vendor" | Each invoice belongs to one vendor. Until auth exists the API picks the vendor from the `X-Vendor-Id` header. |
| `invoices` | Submit Invoice form, Invoice Status list, Invoice Overview, Details page | Drafts keep the form fields nullable. The full validation runs on submit. Money is `NUMERIC(15,2)`. |
| `invoice_documents` | Supporting Documents step, Details page document list | Files are stored on disk under `UPLOAD_FOLDER/invoices/<invoice_id>/<random>.<ext>`. The DB keeps the original name and metadata. |
| `invoice_comments` | Comments page | `author` is `Vendor` (posted through the vendor API) or `AP Team` (posted through AP actions). |
| `invoice_stages` | Status Timeline (mini and full) | One row for each stage reached, with its start time. Ordered by `stage_index`, these rows are the UI's `stageTimes[]`. |

## Status and stage model

`invoices.current_stage` indexes the processing stages:

| index | stage | `status` while in progress |
| --- | --- | --- |
| -1 | *(draft)* | Draft |
| 0 | Submitted | – |
| 1 | AP Validation | Submitted |
| 2 | Business Approval | Under Review (or Rejected) |
| 3 | Ariba Processing | Approved |
| 4 | S/4HANA Transfer | Approved |
| 5 | Payment Scheduled | Approved |
| 6 | Paid | Approved |
| 7 | *(completed)* | Paid |

When an invoice is submitted, the API writes stage rows 0 and 1 and sets `current_stage = 1`. Each AP "advance" adds the next stage row. A rejection leaves the invoice at stage 2 with status `Rejected`.

## Indexes and constraints

- `invoices (vendor_id, status)`, `(vendor_id, invoice_date)` and `(vendor_id, invoice_no)` back the Invoice Status filters and the duplicate-invoice check.
- `invoices.reference_no` is unique. `invoice_stages (invoice_id, stage_index)` is unique.
- CHECK constraints limit `status`, `doc_type`, `author`, `current_stage` and `stage_index` to the values the UI knows.
- Documents, comments and stages cascade-delete with their invoice. A vendor can't be deleted while it still has invoices.

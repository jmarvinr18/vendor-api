# Vendor Portal API

Flask backend for the `vendor-portal` Vue app: invoice submission, supporting documents, invoice status tracking, the status timeline and comments.

## Structure

```
app/
  __init__.py        create_app(): config, DB, migrations, blueprints, CORS
  config.py          settings (loads app/.env)
  constants.py       invoice types, credit terms, statuses, stages (mirrors the Vue app)
  cli.py             `flask seed-demo`
  database/          SQLAlchemy instance
  model/             vendor, invoice, invoice_document, invoice_comment, invoice_stage
  schema/            marshmallow request/response schemas (camelCase JSON)
  services/          business rules: invoice_service, file_storage, current_vendor
  routes/            flask-smorest blueprints
migrations/          Alembic (Flask-Migrate)
docs/database-schema.md   ER diagram and table notes
```

## Setup

1. Point `DATABASE_URL` in `app/.env` at a PostgreSQL database (see `app/.env.example`).
2. Apply the migration and load demo data:

   ```bash
   uv run flask db upgrade
   uv run flask seed-demo
   ```

   `seed-demo` prints the demo vendor's id. Send it as `X-Vendor-Id`, or set it as `DEFAULT_VENDOR_ID` in `app/.env` for local development.

3. Run the server:

   ```bash
   uv run flask run
   ```

   Swagger UI: http://localhost:5000/swagger-ui

## Endpoints (`/api/v1`)

Every vendor endpoint needs `X-Vendor-Id` (or `DEFAULT_VENDOR_ID`). This is a placeholder for real authentication.

| Method | Path | UI feature |
| --- | --- | --- |
| GET | `/reference-data` | Invoice form dropdowns, statuses, stages, upload rules |
| GET | `/vendors/me` | Header / default vendor name |
| GET | `/invoices?search=&status=&dateFrom=&dateTo=&page=&pageSize=` | Invoice Status list (filters and pagination) |
| GET | `/invoices/summary` | Counts and totals per status (dashboard) |
| POST | `/invoices` | Create a draft (Save as Draft / start Submit Invoice) |
| GET | `/invoices/{id}` | Invoice Overview and Details page |
| PATCH | `/invoices/{id}` | Edit a draft |
| DELETE | `/invoices/{id}` | Cancel / discard a draft |
| POST | `/invoices/{id}/submit` | Review & Submit. Returns `referenceNo`. A 422 response has per-field `errors`. |
| GET | `/invoices/{id}/timeline` | Status Timeline page (stage states and status message) |
| GET, POST | `/invoices/{id}/documents` | Supporting Documents step. The upload is multipart `files[]` with an optional `docType[]`. |
| DELETE | `/invoices/{id}/documents/{docId}` | Remove an uploaded file |
| GET | `/invoices/{id}/documents/{docId}/file?download=true` | View or download a document |
| GET, POST | `/invoices/{id}/comments` | Comments page |
| POST | `/ap/invoices/{id}/advance` | AP back office: move to the next stage. **No auth yet.** |
| POST | `/ap/invoices/{id}/reject` | AP back office: reject with a reason. **No auth yet.** |

Submit flow from the UI: `POST /invoices` → `POST /invoices/{id}/documents` → `POST /invoices/{id}/submit`.

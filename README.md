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
  extensions/        storage.py: builds the document storage once, wires DocumentService per request
  model/             vendor, invoice, invoice_document, invoice_comment, invoice_stage
  schema/            marshmallow request/response schemas (camelCase JSON)
  services/          business logic (no HTTP code; raises errors from services/errors.py)
    invoice_service.py    drafts, submission, status workflow
    document_service.py   upload / read / remove invoice and supporting documents
    upload_validator.py   file rules: type, magic bytes, size, count, document type
    invoice_rules.py      rules shared by services (e.g. only drafts can change)
    storage/              DocumentStorage interface + S3 and local implementations
  routes/            flask-smorest blueprints (thin: parse request, call a service)
migrations/          Alembic (Flask-Migrate)
tests/               pytest; S3 is mocked with moto
docs/database-schema.md   ER diagram and table notes
```

## Document storage (AWS S3)

Uploaded invoice and supporting documents are stored in S3. The database keeps only the object key and metadata.

- **Object keys:** `<S3_KEY_PREFIX>/invoices/<invoice id>/<random>.<ext>`. The vendor's file name is stored only in the database.
- **Security:** objects are private and encrypted at rest (SSE-S3 `AES256` by default, or `aws:kms`). Downloads are streamed through the API, so the vendor check still applies and the bucket never needs to be public or have CORS.
- **Validation before upload:** the file type is checked by its first bytes, not just its extension, and the stored content type comes from the file type rather than the client. The size, file count and document type are also checked. If any file is invalid, nothing is uploaded.
- **Consistency:** if storing a file or saving the record fails part-way, the objects already uploaded are deleted again. Removing a document, or deleting a draft, deletes its objects. If S3 is down, the API returns 503.
- **Credentials:** these come from the standard AWS chain: `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, or the ECS task / EC2 instance role. The IAM identity only needs `s3:PutObject`, `s3:GetObject` and `s3:DeleteObject` on `arn:aws:s3:::<bucket>/<prefix>/*`.

| Setting | Default | |
| --- | --- | --- |
| `STORAGE_BACKEND` | `s3` | `local` stores files on disk under `UPLOAD_FOLDER` (development only) |
| `S3_BUCKET` | – | Required for `s3` |
| `S3_REGION` | `AWS_DEFAULT_REGION` | |
| `S3_KEY_PREFIX` | `vendor-portal` | |
| `S3_SERVER_SIDE_ENCRYPTION` | `AES256` | `aws:kms` with `S3_KMS_KEY_ID`, or empty for the bucket default |
| `S3_ENDPOINT_URL` | – | Only for S3-compatible emulators |

Documents uploaded while the API ran with `STORAGE_BACKEND=local` stay on local disk. To copy them into S3, run `flask documents push-to-s3` (or `docker compose exec api flask documents push-to-s3`); add `--dry-run` to preview. It skips files already in S3 and lists any file that exists in neither place, which must be re-uploaded.

To add another backend (for example Azure Blob), implement `DocumentStorage` and register it in `services/storage/factory.py`. The services don't change.

## Tests

```bash
uv sync
uv run pytest
```

## Run with Docker

```bash
cp .env.example .env              # then set POSTGRES_PASSWORD
docker compose up -d --build      # Postgres + API on http://localhost:8000
docker compose exec api flask seed-demo
```

Set `S3_BUCKET` and the AWS credentials in `.env`. To try it without AWS, uncomment the "Local S3 emulator" lines in `.env` and start the stack with `docker compose --profile local-s3 up -d`. This runs a moto S3 server and creates the bucket for you.

- The API container applies migrations on startup (`RUN_MIGRATIONS=1`). Set it to `0` when you run several replicas, and run `flask db upgrade` once from a separate job.
- The app is served by gunicorn on port 8000 as a non-root user. `GET /healthz` returns 503 when the database is unreachable.
- Data lives in named volumes: `db-data` (Postgres) and `uploads` (supporting documents at `/data/uploads`).
- The image only installs the runtime dependencies. The old RAG packages are in the optional `rag` group (`uv sync --group rag`).
- `app/.env` is never copied into the image. Configuration comes from the environment (`DATABASE_URL`, `UPLOAD_FOLDER`, `CORS_ORIGINS`, `DEFAULT_VENDOR_ID`).

## Local setup (without Docker)

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
| POST | `/extractions` | Scan invoice: upload one scanned invoice (multipart `file`). Stored in S3, which starts the OCR pipeline. Returns 202 with a `pending` extraction. |
| GET | `/extractions/{id}` | Poll until `completed`/`failed`. Returns the text, entities and `candidates` (values with a suggested invoice field). |
| DELETE | `/extractions/{id}` | Discard a scan and its extracted text |

Submit flow from the UI: `POST /invoices` → `POST /invoices/{id}/documents` → `POST /invoices/{id}/submit`.

Scan-to-fill: `POST /extractions` → S3 → EventBridge → Step Functions → Textract → Lambda → `document_extractions` → `GET /extractions/{id}`. See [infra/ocr-pipeline/README.md](infra/ocr-pipeline/README.md) for the AWS stack and `flask extraction complete <id> <result.json>` for simulating the pipeline locally.

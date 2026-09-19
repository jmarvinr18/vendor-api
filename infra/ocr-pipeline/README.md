# OCR pipeline (scanned invoice → invoice form)

```
Vue app ──POST /api/v1/extractions──▶ API ──PutObject──▶ S3  <prefix>/extractions/<id>/<random>.<ext>
                                        │                  │ Object Created
                                        │                  ▼
                                        │              EventBridge rule
                                        │                  ▼
                                        │        Step Functions: ExtractInvoice
                                        │          StartDocumentTextDetection → poll → StoreExtraction
                                        │                  ▼
                                        │   Lambda: Textract lines + Comprehend entities
                                        │                  ▼
Vue app ◀──GET /api/v1/extractions/{id}── document_extractions (PostgreSQL)
```

1. The API validates the file (PDF/JPG/PNG, 10MB, file signature), inserts a `pending` row in
   `document_extractions` and stores the file under `<S3_KEY_PREFIX>/extractions/`.
2. The S3 `Object Created` event starts the `ExtractInvoice` state machine.
3. The state machine runs an asynchronous Textract text-detection job (multi-page PDFs are
   supported) and polls it every 5 seconds, up to 15 minutes.
4. The `StoreExtraction` Lambda reads every text line, runs Comprehend `DetectEntities` on it and
   updates the row (`completed` with `extracted_text` and `entities`). If any step fails, the
   row is set to `failed` and the vendor is asked to enter the details manually.
5. The Vue app polls `GET /api/v1/extractions/{id}`. The API turns the text and entities into
   candidate values with a suggested invoice field (`app/services/extraction_mapper.py`).

## Deploy (AWS SAM)

Prerequisites: the document bucket the API uses, the vendor portal database in a VPC, and a
Secrets Manager secret with `{"host", "port", "username", "password", "dbname"}`.

```bash
# 1. Send the bucket's events to EventBridge (once per bucket).
aws s3api put-bucket-notification-configuration --bucket <S3_BUCKET> \
  --notification-configuration '{"EventBridgeConfiguration": {}}'

# 2. Build and deploy.
cd infra/ocr-pipeline
sam build
sam deploy --guided   # stack name e.g. vendor-portal-ocr
```

`sam deploy --guided` asks for `DocumentBucketName`, `KeyPrefix`, `DatabaseSecretArn`,
`SubnetIds` and `SecurityGroupIds`.

The Lambda runs in the database's VPC, so it needs a route to Textract, Comprehend and Secrets
Manager: a NAT gateway, or interface VPC endpoints for `textract`, `comprehend` and
`secretsmanager`. The database security group must allow the Lambda's security group on 5432.

Recommended on the bucket: a lifecycle rule that expires `<S3_KEY_PREFIX>/extractions/` after
a few days. Scans are only needed to pre-fill the form; the vendor attaches the invoice to the
invoice itself on submit, and discarding a scan deletes it right away.

## IAM for the API

In addition to the existing document permissions, the API's role needs `s3:PutObject`,
`s3:GetObject` and `s3:DeleteObject` on `arn:aws:s3:::<S3_BUCKET>/<S3_KEY_PREFIX>/extractions/*`.
It never calls Textract or Comprehend itself.

## Local development (no AWS)

Moto and local storage don't emit EventBridge events, so the pipeline is simulated with the
Flask CLI, which performs the same database update as the Lambda:

```bash
flask extraction list                                   # find the pending extraction id
flask extraction complete <id> sample_invoice_scanned.jsonl
flask extraction fail <id> "Text could not be extracted from this document."
```

With Docker: `docker compose exec api flask extraction complete <id> /path/in/container.json`.

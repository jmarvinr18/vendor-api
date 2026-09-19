# SyncExtraction Lambda (processed JSONL → `document_extractions`)

The deployed ExtractInvoice pipeline already writes each scan's OCR result to
`processed/<...>.jsonl`, and the text and metadata to `kb-source/`. This Lambda is the step
added after that. It copies the JSONL result into the vendor portal database so
`GET /api/v1/extractions/{id}` returns it and the Vue app stops polling.

```
S3 <prefix>/extractions/<id>/<random>.pdf ─▶ EventBridge ─▶ Step Functions
    … existing states (Textract, Comprehend, write processed/ + kb-source/) …
    ─▶ SyncExtraction ─▶ UPDATE document_extractions
         status, extracted_text, entities, error_message, completed_at
    on any failure ─▶ MarkExtractionFailed (same Lambda) ─▶ status = failed
```

## What it does

| JSONL record | Row update (only while `status = 'pending'`) |
| --- | --- |
| `text` is not blank | `completed`, `extracted_text` = text, `entities` = `[{type, text, score}]`, `error_message` = NULL, `completed_at` = now |
| `text` is blank | `failed`, with "We couldn't find any text in this document. Please enter the details manually." |
| Invoked with `error` (an earlier step failed) | `failed`, with "We couldn't read text from this document. Please enter the details manually." |

- **Matching the row:** the row is found by `storage_key`, which is the record's `source_key` without the API's `S3_KEY_PREFIX`. For example, `vendor-portal/extractions/<id>/<random>.pdf` becomes `extractions/<id>/<random>.pdf`.
- **Skipped records:** records for objects outside `<prefix>/extractions/` (e.g. `vendor-portal/invoices/…`) are skipped, because they have no extraction row.
- **Safe retries:** a finished row is never overwritten, so retried or duplicate executions are harmless. A scan the vendor discarded mid-pipeline is also left alone.
- **Not stored:** `pii_spans` and `char_count` have no columns.
- **Accepted formats:** both true JSON Lines and a single pretty-printed JSON document, like `sample_invoice_scanned.jsonl`.

### Input

```jsonc
{ "bucket": "slaif-bucket", "key": "processed/3953e684….jsonl" }         // explicit JSONL key
{ "bucket": "slaif-bucket", "sourceKey": "vendor-portal/extractions/…" }  // JSONL key from PROCESSED_KEY_TEMPLATE
{ "sourceKey": "vendor-portal/extractions/…", "error": { … } }            // mark failed
```

`PROCESSED_KEY_TEMPLATE` (default `processed/{stem}.jsonl`) must match how your pipeline names its output. The placeholders are `{stem}` (the file name without extension), `{name}`, `{dir}` and `{key}` (the source key without extension). If the state before SyncExtraction outputs the JSONL key, pass `key` instead and the template isn't used.

## Deploy

```bash
cd infra/extraction-sync
sam build
sam deploy --guided --stack-name vendor-portal-extraction-sync
```

`sam deploy --guided` asks for these parameters:

| Parameter | Value |
| --- | --- |
| `DocumentBucketName` | `slaif-bucket` |
| `KeyPrefix` | the API's `S3_KEY_PREFIX` (`vendor-portal`) |
| `ProcessedPrefix` / `ProcessedKeyTemplate` | where the pipeline writes the JSONL |
| `DatabaseSecretArn` | Secrets Manager secret `{"host","port","username","password","dbname"}` of the database the API uses |
| `SubnetIds`, `SecurityGroupIds` | when the database is in a VPC. The database's security group must allow this Lambda on 5432, and the subnets need a route to S3 and Secrets Manager (NAT or VPC endpoints). |
| `DatabaseSslMode` | `verify` (default). For RDS, add the [RDS CA bundle](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem) to `functions/sync_extraction/rds-ca.pem` and set `DatabaseSslRootCert=rds-ca.pem`. |
| `StateMachineRoleName` | the ExtractInvoice execution role's name. The stack then grants it `lambda:InvokeFunction` on this function. |

## Add it to the state machine

Merge the states from [statemachine/sync_extraction_states.asl.json](statemachine/sync_extraction_states.asl.json) into the deployed ExtractInvoice definition:

1. Replace `<SyncExtractionFunctionArn>` with the stack output `SyncExtractionFunctionArn`.
2. Change the state that writes `processed/…jsonl` from `"End": true` to `"Next": "SyncExtraction"`.
3. Point that pipeline's failure `Catch` blocks at `MarkExtractionFailed`, so a failed OCR run doesn't leave the vendor waiting on a `pending` scan.
4. SyncExtraction reads `$.detail.bucket.name` and `$.detail.object.key` from the original S3 event. Earlier states must keep `$.detail` by using `ResultPath` rather than replacing the whole state with `OutputPath`. Otherwise, adjust the two JSONPaths.

## Test

```bash
cd infra/extraction-sync
pip install pytest "pg8000>=1.31,<2"
pytest                                    # unit tests, no AWS or database
SYNC_TEST_DB=localhost:5432:user:password:dbname pytest   # also run the SQL against a database migrated with `flask db upgrade`
```

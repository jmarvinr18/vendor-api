# Integration guide

Everything an external service needs to work with this API: how the AgentCore-hosted AI agent
plugs into the message endpoint, and how to call every other endpoint.

- **Part 1 — [The AI agent and AgentCore](#part-1--the-ai-agent-and-agentcore)**: the request/response
  contract, building the agent, deploying it, pointing this API at it, and how the agent reads
  vendor data back out of this API.
- **Part 2 — [API reference](#part-2--api-reference)**: every endpoint, with request and response shapes.

Interactive docs are served by the running API at `/swagger-ui`, and the raw spec at
`/openapi.json`. This file is the narrative version: what to call, in what order, and why.

## Conventions

| | |
| --- | --- |
| Base path | `/api/v1` |
| JSON keys | `camelCase` in and out. Unknown keys in a request body are rejected with 422. |
| Dates | `YYYY-MM-DD` (e.g. `2026-09-10`) |
| Timestamps | ISO 8601 with a UTC offset (e.g. `2026-09-24T01:33:36.125211+00:00`) |
| Money | JSON numbers with 2 decimal places (e.g. `11200.00`) |
| Identifiers | UUIDs as strings |

### Authentication

There is **no authentication yet**. Every vendor-facing endpoint identifies the caller by an
`X-Vendor-Id` header holding the vendor's UUID:

```bash
curl localhost:8000/api/v1/vendors/me -H "X-Vendor-Id: $VENDOR_ID"
```

Without the header the API falls back to the `DEFAULT_VENDOR_ID` setting, and answers `401` if
that is unset too. An unknown or inactive vendor is also `401`.

Data is scoped to that vendor throughout: another vendor's invoice, document, extraction or
conversation is reported as `404`, never `403`. The `/api/v1/ap/...` endpoints are the exception —
they are back-office actions and take no vendor header at all.

> Replace this with a real token before the API is reachable by vendors. Until then, treat
> `X-Vendor-Id` as trusted input from your own frontend only.

### Errors

Every error carries `code` and `status`. There are two shapes, and a client should handle both.

**Domain errors** — the business rules — always carry a human-readable `message`:

```json
{
  "code": 422,
  "status": "Unprocessable Entity",
  "message": "The invoice is incomplete.",
  "errors": { "invoiceNo": "Invoice no. is required." }
}
```

`errors` appears only when there is per-field detail, and is flat `{field: message}`. A submit
failure is keyed by invoice form field, so a UI can map it straight onto the form:

```json
{
  "code": 422,
  "status": "Unprocessable Entity",
  "message": "The invoice is incomplete.",
  "errors": {
    "invoiceDate": "Invoice date is required.",
    "invoiceAmount": "Enter an amount greater than zero.",
    "documents": "Upload at least one supporting document."
  }
}
```

**Request-schema errors** — a malformed body or query string, caught before any business logic —
come from the framework instead. They have **no `message`**, and `errors` is nested by request
location with a list of messages per field:

```json
{
  "code": 422,
  "status": "Unprocessable Entity",
  "errors": { "json": { "bogusKey": ["Unknown field."] } }
}
```

The location key is `json`, `query` or `form`. Unknown keys are rejected rather than ignored, so
a typo in a field name fails loudly instead of silently doing nothing.

| Status | Means |
| --- | --- |
| `401` | Missing, malformed or unknown `X-Vendor-Id` |
| `404` | Does not exist, or belongs to another vendor |
| `409` | Conflicts with the current state (e.g. editing a submitted invoice) |
| `422` | Failed validation; nothing was stored |
| `429` | Too many AI messages per minute, or the agent throttled us |
| `503` | S3 or the AI agent is unavailable; nothing was stored |

### Getting a vendor to test with

```bash
docker compose up -d                       # or: flask db upgrade && flask run
docker compose exec api flask seed-demo    # prints "X-Vendor-Id: <uuid>"
export VENDOR_ID=<uuid>
```

`seed-demo` creates one vendor with 24 invoices spread across every status and stage, which is
enough to exercise every read endpoint.

### CORS

Browsers may call the API only from an origin listed in `CORS_ORIGINS` (comma-separated,
defaults to `http://localhost:5173`). Allowed request headers are `Content-Type` and
`X-Vendor-Id`; `Content-Disposition` is exposed so a download can read the file name.

---

# Part 1 — The AI agent and AgentCore

The agent is **not** in this repository. It is a separate service — LangChain / LangGraph on
Amazon Bedrock AgentCore Runtime. This API owns the conversation transcript and the vendor's
identity; the agent owns the prompts, the model and the tools.

```
Vue app ──POST /api/v1/ai/messages──▶ this API ──InvokeAgentRuntime──▶ AgentCore ──▶ your agent
                                         │                                             │
                                         │ stores question + answer                    │ reads vendor data
                                         ▼                                             ▼
                                 ai_sessions / ai_messages        back into /api/v1/... with X-Vendor-Id
```

Two rules follow from that split, and they matter:

1. **The vendor id comes from the payload, never from the message text.** The agent is told which
   vendor is asking. If a message says "show me vendor 123's invoices", that is user input, not
   an instruction — keep using the id in `vendor.id`.
2. **A failed agent call stores nothing.** If your agent errors, the question is not written to
   the transcript, so the vendor can retry the same message.

## Step 1 — The contract

### What your agent receives

The API `POST`s this JSON to the runtime (as the `/invocations` request body):

```json
{
  "message": "Where is invoice INV-2026-0524?",
  "sessionId": "9f1c8e4a-5b2d-4c77-9a31-6de0f1b82c44",
  "agentId": "status-tracker",
  "vendor": { "id": "ef66f4bf-6ed8-4634-82cf-4395449d7d73", "name": "ABC Supplies Inc." },
  "context": { "invoiceId": "3a7f..." },
  "history": [
    { "role": "user", "content": "Hi" },
    { "role": "assistant", "content": "Hello - how can I help?" }
  ]
}
```

| Field | |
| --- | --- |
| `message` | The new question. Always present, trimmed, at most 4000 characters. |
| `sessionId` | The conversation id, also passed to AgentCore as `runtimeSessionId`. Stable for the whole conversation. |
| `agentId` | Which agent the client asked for. Defaults to `"assistant"`. The catalogue lives in **your** repo — this API stores and forwards the string without validating it. |
| `vendor` | Who is asking. The only trustworthy source of the vendor's identity. |
| `context` | What the vendor was looking at, e.g. `{"invoiceId": "..."}`. `{}` when there is none. The invoice is already confirmed to belong to this vendor. |
| `history` | The conversation **before** this message, oldest first, up to `AI_HISTORY_MESSAGES` (20) entries, always starting with a `user` turn. Ignore it if you keep your own memory. |

### What your agent must return

```json
{
  "reply": "INV-2026-0524 cleared AP validation on 21 May and is awaiting business approval.",
  "citations": [{ "type": "invoice", "id": "3a7f...", "label": "INV-2026-0524" }],
  "model": "claude-sonnet-5",
  "usage": { "inputTokens": 1840, "outputTokens": 96 },
  "toolCalls": 3
}
```

Only `reply` is required. The rest is stored for cost tracking and the UI's source links:

- `citations` — up to 10. `type` must be `invoice`, `document` or `policy`; anything else is
  dropped silently. `label` defaults to `id`.
- `usage` / `toolCalls` — integers, stored on the answer.
- `model` — what actually answered. Defaults to the runtime name.

Tolerances, so you are not blocked on exact shape: `message` and `output` are accepted as
aliases for `reply`, and a plain-text (non-JSON) body is taken as the reply text. An empty or
whitespace-only reply is rejected and the vendor gets a `503`.

## Step 2 — Build the entrypoint

AgentCore Runtime expects a container that serves `POST /invocations` and `GET /ping` on port
8080. The `bedrock-agentcore` SDK wires both for you:

```python
# agent.py  (in the agent repository)
from bedrock_agentcore import BedrockAgentCoreApp

from my_agent.graph import build_graph  # your LangGraph

app = BedrockAgentCoreApp()
graph = build_graph()


@app.entrypoint
def invoke(payload: dict) -> dict:
    vendor = payload["vendor"]

    messages = [(m["role"], m["content"]) for m in payload.get("history", [])]
    messages.append(("user", payload["message"]))

    result = graph.invoke(
        {"messages": messages},
        # Vendor identity and context are config, not message content, so no prompt text
        # can change which vendor's data the tools may read.
        config={
            "configurable": {
                "thread_id": payload["sessionId"],
                "vendor_id": vendor["id"],
                "vendor_name": vendor["name"],
                "agent_id": payload.get("agentId", "assistant"),
                "invoice_id": (payload.get("context") or {}).get("invoiceId"),
            }
        },
    )

    return {
        "reply": result["messages"][-1].content,
        "citations": result.get("citations", []),
        "model": result.get("model"),
    }


if __name__ == "__main__":
    app.run()
```

Give your tools a read-only HTTP client onto this API, using the vendor id from the config:

```python
import os
import requests

API = os.environ["VENDOR_PORTAL_API_URL"]  # e.g. https://api.internal/api/v1


def portal_get(path: str, vendor_id: str, **params):
    response = requests.get(
        f"{API}{path}",
        params=params,
        timeout=15,
        headers={"X-Vendor-Id": vendor_id},  # scoping is enforced server-side
    )
    response.raise_for_status()
    return response.json()


def list_invoices(vendor_id: str, search: str = "", status: str | None = None):
    """Tool: find the vendor's invoices."""
    return portal_get("/invoices", vendor_id, search=search, status=status, pageSize=10)
```

Part 2 lists everything reachable this way. `GET /invoices`, `GET /invoices/{id}`,
`GET /invoices/{id}/timeline`, `GET /invoices/summary` and `GET /invoices/{id}/comments` cover
almost every question a vendor asks. Keep the agent to `GET`s: submitting or advancing an
invoice on the vendor's behalf is not something a chat turn should do.

## Step 3 — Test it locally, before AgentCore

Run the agent on your machine and point this API straight at it over HTTP — no AWS involved:

```bash
# terminal 1, in the agent repo
python agent.py                     # serves POST /invocations on :8080

# terminal 2, in this repo
AI_AGENT_URL=http://localhost:8080/invocations flask run
```

```bash
curl -X POST localhost:8000/api/v1/ai/messages \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" \
  -d '{"content": "Where is invoice INV-2026-0524?"}'
```

You should get `201` with `sessionId`, `userMessage` and `assistantMessage`. Send a second
message with the same `sessionId` and check `history` arrives as you expect.

This path (`HttpAgentClient`) is also how you would run the agent behind an internal load
balancer instead of AgentCore. Set `AI_AGENT_TOKEN` to have the API send
`Authorization: Bearer <token>`.

## Step 4 — Containerize

```dockerfile
# Must be arm64 for AgentCore Runtime.
FROM --platform=linux/arm64 python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8080
CMD ["python", "agent.py"]
```

Build for arm64 even on an x86 machine (`docker buildx build --platform linux/arm64`), push to
ECR, and confirm `GET /ping` answers before deploying.

## Step 5 — Deploy to AgentCore Runtime

The starter toolkit is the shortest path:

```bash
pip install bedrock-agentcore-starter-toolkit
agentcore configure --entrypoint agent.py
agentcore launch
```

It builds the image, pushes it to ECR, creates the runtime and prints the **agent runtime ARN** —
the one value this API needs:

```
arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/my_agent-a1b2c3d4
```

The runtime's own **execution role** (not the API's) is what needs Bedrock model access, ECR
pull and CloudWatch Logs. Deploy tooling moves faster than this document — check the current
AgentCore docs if a command or flag differs.

## Step 6 — Point this API at it

```bash
AI_AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/my_agent-a1b2c3d4
AI_AGENT_REGION=ap-southeast-1
AI_AGENT_QUALIFIER=DEFAULT          # or a named endpoint
AI_AGENT_TIMEOUT_SECONDS=75
```

The ARN takes precedence over `AI_AGENT_URL`. With neither set the endpoint answers `503` and
nothing else in the API is affected — so shipping this API before the agent exists is fine.

The API's AWS identity (env credentials, profile, or the ECS task / EC2 instance role — the same
chain as S3) needs one permission:

```json
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:123456789012:runtime/my_agent-a1b2c3d4*"
}
```

The trailing `*` covers the runtime's endpoint qualifiers.

The remaining knobs:

| Setting | Default | |
| --- | --- | --- |
| `AI_AGENT_TIMEOUT_SECONDS` | `75` | Read timeout. A reply that takes longer becomes a `503`. |
| `AI_HISTORY_MESSAGES` | `20` | Stored messages sent as `history`. |
| `AI_MAX_MESSAGES_PER_SESSION` | `100` | Conversation length before `409`. |
| `AI_RATE_LIMIT_PER_MINUTE` | `10` | Messages per vendor per minute before `429`. |

## Step 7 — Verify end to end

```bash
# 1. First message starts a conversation
SESSION=$(curl -s -X POST localhost:8000/api/v1/ai/messages \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" \
  -d '{"content": "Which of my invoices are still unpaid?"}' | jq -r .sessionId)

# 2. Follow-up in the same conversation
curl -X POST localhost:8000/api/v1/ai/messages \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" \
  -d "{\"content\": \"What about the oldest one?\", \"sessionId\": \"$SESSION\"}"
```

Checklist:

- [ ] `201` with a non-empty `assistantMessage.content`
- [ ] The follow-up reuses `sessionId` and your agent sees `history`
- [ ] `runtimeSessionId` is accepted — AgentCore requires **33+ characters**, and a UUID is 36
- [ ] Citations you return survive the round trip in `assistantMessage.citations`
- [ ] Stopping the agent turns replies into `503`, and the failed question is **not** stored
- [ ] Your tools see only the calling vendor's data (try two vendor ids)

## How failures map

| Your agent does | The vendor sees |
| --- | --- |
| Returns `reply` | `201` with the answer |
| Returns empty / whitespace `reply` | `503`, nothing stored |
| Throttles (`ThrottlingException`, or HTTP `429`) | `429`, nothing stored |
| Errors, times out, or is unreachable | `503`, nothing stored |
| Not configured at all | `503` |

Nothing is written until an answer exists, so every one of these is safely retryable.

## If the transcript needs reading back

`ai_sessions` and `ai_messages` are currently **write-only** through the API: the endpoint stores
messages, but nothing serves a conversation back. A UI that shows chat history needs a
`GET /api/v1/ai/sessions` (and one for a single conversation) to be added here first.

---

# Part 2 — API reference

22 operations across 16 paths, in seven groups. All paths are relative to `/api/v1`, and all
need `X-Vendor-Id` except the `ap/` group.

| Group | Endpoints |
| --- | --- |
| [Vendor & reference data](#vendor--reference-data) | `GET /vendors/me`, `GET /reference-data` |
| [Invoices](#invoices) | `GET,POST /invoices`, `GET /invoices/summary`, `GET,PATCH,DELETE /invoices/{id}`, `POST /invoices/{id}/submit`, `GET /invoices/{id}/timeline` |
| [Documents](#documents) | `GET,POST /invoices/{id}/documents`, `DELETE /invoices/{id}/documents/{docId}`, `GET /invoices/{id}/documents/{docId}/file` |
| [Comments](#comments) | `GET,POST /invoices/{id}/comments` |
| [Invoice scanning (OCR)](#invoice-scanning-ocr) | `POST /extractions`, `GET,DELETE /extractions/{id}` |
| [AI assistant](#ai-assistant) | `POST /ai/messages`, `POST /ai/messages/stream` |
| [AP back office](#ap-back-office) | `POST /ap/invoices/{id}/advance`, `POST /ap/invoices/{id}/reject` |

Plus `GET /healthz` (outside `/api/v1`), which returns `{"status": "ok"}`, or `503` with
`{"status": "error", "database": "unreachable"}` when the database cannot be reached. Use it as
the load balancer / ECS health check.

## Vendor & reference data

### `GET /vendors/me`

The vendor behind the `X-Vendor-Id` header. The cheapest way to check a vendor id is valid.

```json
{ "id": "ef66f4bf-...", "name": "ABC Supplies Inc.", "vendorCode": "DEMO-001", "email": "billing@example.com" }
```

### `GET /reference-data`

Every dropdown option and upload rule in one call, so clients never hardcode them. Fetch once
at startup.

```json
{
  "invoiceTypes": ["Standard Invoice", "Progress Billing", "Credit Memo", "Debit Memo", "Proforma Invoice"],
  "creditTerms": ["Cash on Delivery", "15 Days Net", "30 Days Net", "45 Days Net", "60 Days Net"],
  "invoiceStatuses": ["Draft", "Submitted", "Under Review", "Approved", "Paid", "Rejected"],
  "stages": ["Submitted", "AP Validation", "Business Approval", "Ariba Processing",
             "S/4HANA Transfer", "Payment Scheduled", "Paid"],
  "documentTypes": ["Invoice", "Purchase Order", "Delivery Receipt", "Other"],
  "vatRate": 0.12,
  "extractableFields": [{ "key": "invoiceNo", "label": "Invoice No." }],
  "upload": { "acceptedExtensions": ["pdf", "jpg", "jpeg", "png"], "maxFileSize": 10485760, "maxFiles": 10 }
}
```

`stages` is ordered, and an invoice's `currentStage` indexes into it: `-1` is still a draft, `7`
(the length) is fully paid.

## Invoices

### The invoice object

Returned by every invoice endpoint. `GET /invoices/{id}`, `POST`, `PATCH` and `/submit` add
`documents` and `comments`; the list endpoint omits them.

| Field | |
| --- | --- |
| `id` | UUID |
| `referenceNo` | `SUB-YYYY-NNNNNN`, assigned on submit. `null` while a draft. |
| `invoiceNo`, `invoiceType`, `invoiceDate` | From the form |
| `description`, `poPrNo`, `drNo`, `dateReceived`, `creditTerms`, `vendorName` | From the form |
| `invoiceAmount`, `vatableSales`, `vat`, `nonVat` | Numbers, 2 dp |
| `status` | `Draft`, `Submitted`, `Under Review`, `Approved`, `Paid` or `Rejected` |
| `submittedOn` | Timestamp, or `null` |
| `currentStage` | Index into `reference-data.stages`; `-1` for a draft |
| `stageTimes` | Timestamps, one per stage reached so far |
| `commentCount` | Integer |
| `createdAt`, `updatedAt` | Timestamps |
| `documents` | `[{ id, name, docType, uploadedOn, size, extension, contentType, hasFile }]` |
| `comments` | `[{ id, author, message, postedOn }]` |

### `GET /invoices`

The vendor's invoices, newest invoice date first, paginated.

| Query | Default | |
| --- | --- | --- |
| `search` | `""` | Matches invoice no., PO/PR no. and description |
| `status` | – | One of the six statuses |
| `dateFrom`, `dateTo` | – | On invoice date, inclusive |
| `page` | `1` | |
| `pageSize` | `10` | 1–100 |

```bash
curl "localhost:8000/api/v1/invoices?status=Paid&pageSize=5" -H "X-Vendor-Id: $VENDOR_ID"
```

```json
{ "items": [ /* invoice objects without documents/comments */ ],
  "total": 14, "page": 1, "pageSize": 5, "pageCount": 3 }
```

### `POST /invoices`

Creates a **draft**. Every field is optional here — the full rules are enforced at submit — so a
client can save a half-filled form.

```bash
curl -X POST localhost:8000/api/v1/invoices \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" -d '{
    "invoiceNo": "INV-2026-0900",
    "invoiceType": "Standard Invoice",
    "invoiceDate": "2026-09-10",
    "dateReceived": "2026-09-12",
    "creditTerms": "30 Days Net",
    "description": "Office supplies - Sep 2026",
    "poPrNo": "PO-2026-0300",
    "invoiceAmount": 11200,
    "vatableSales": 10000,
    "vat": 1200,
    "nonVat": 0
  }'
```

`201` with the invoice object. `invoiceType` and `creditTerms` must come from `reference-data`;
amounts must be non-negative.

### `GET /invoices/summary`

Counts and totals per status, for a dashboard.

```json
{ "total": 24, "byStatus": [{ "status": "Paid", "count": 14, "totalAmount": 1043700.00 }] }
```

### `GET /invoices/{id}`

One invoice with its documents, comments and stage times. `404` if it is not this vendor's.

### `PATCH /invoices/{id}`

Same body as `POST`, all fields optional — send only what changed. **Drafts only**; a submitted
invoice gives `409`.

### `DELETE /invoices/{id}`

Discards a draft and deletes its uploaded files from S3. `204`. Drafts only (`409` otherwise).

### `POST /invoices/{id}/submit`

No body. Runs the full rule set, assigns `referenceNo`, sets the status to `Submitted` and starts
AP Validation.

```bash
curl -X POST localhost:8000/api/v1/invoices/$INVOICE_ID/submit -H "X-Vendor-Id: $VENDOR_ID"
```

- `200` — the updated invoice
- `422` — incomplete; `errors` is keyed by form field, ready to map onto the form
- `409` — not a draft, or that invoice no. was already submitted

### `GET /invoices/{id}/timeline`

Everything the status page shows: the message, the next step, and per-stage progress.

```json
{
  "status": "Under Review",
  "statusInfo": {
    "message": "Your invoice passed AP validation and is awaiting business approval.",
    "nextStep": "Business Approval",
    "nextStepDetail": "The requesting business unit is reviewing and approving your invoice.",
    "estimatedTime": "2 - 3 Business Days"
  },
  "stages": [
    { "label": "Submitted", "state": "done", "time": "2026-05-20T08:00:00+00:00",
      "note": "Invoice submitted by vendor." },
    { "label": "Business Approval", "state": "current", "time": null,
      "note": "Awaiting approval from business unit." },
    { "label": "Ariba Processing", "state": "pending", "time": null, "note": "" }
  ]
}
```

`state` is `done`, `current`, `rejected` or `pending`.

## Documents

Files live in a private S3 bucket; the database keeps only the key and metadata. Downloads are
streamed through the API so the vendor check still applies.

### `POST /invoices/{id}/documents`

`multipart/form-data` with one or more `files` parts. Optional `docType` parts, **in the same
order**, set each file's type; otherwise it is guessed from the file name (`INV...` → Invoice,
`PO...`/`PR...` → Purchase Order, `DR...` → Delivery Receipt).

```bash
curl -X POST localhost:8000/api/v1/invoices/$INVOICE_ID/documents \
  -H "X-Vendor-Id: $VENDOR_ID" \
  -F 'files=@INV-2026-0900.pdf' -F 'docType=Invoice' \
  -F 'files=@PO-2026-0300.pdf'  -F 'docType=Purchase Order'
```

`201` with the created documents. Limits: PDF, JPG or PNG, 10 MB each, 10 per invoice. The type
is verified from the file's first bytes, not its extension. **Validation is all-or-nothing** — if
one file fails, nothing is stored (`422`). Drafts only. `503` if S3 is unreachable.

### `GET /invoices/{id}/documents`

The invoice's documents, same shape as the `documents` array above.

### `DELETE /invoices/{id}/documents/{documentId}`

Removes the record and its S3 object. `204`. Drafts only.

### `GET /invoices/{id}/documents/{documentId}/file`

Streams the file. Add `?download=true` for `Content-Disposition: attachment`; the default renders
inline (for a PDF viewer or `<img>`). The response carries the stored content type and the
vendor's original file name.

## Comments

The vendor's conversation with the AP team about an invoice.

### `GET /invoices/{id}/comments`

Oldest first. `author` is `Vendor` or `AP Team`.

### `POST /invoices/{id}/comments`

```bash
curl -X POST localhost:8000/api/v1/invoices/$INVOICE_ID/comments \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" \
  -d '{"message": "Attaching the corrected delivery receipt."}'
```

`201`. Max 1000 characters, cannot be blank. The author is always `Vendor` — the AP side posts
through the `ap/` endpoints.

## Invoice scanning (OCR)

Upload a scan to pre-fill the invoice form. This does **not** create an invoice — it returns
candidate values for the client to apply.

### `POST /extractions`

`multipart/form-data` with one `file` part (PDF, JPG or PNG, up to 10 MB).

```bash
curl -X POST localhost:8000/api/v1/extractions \
  -H "X-Vendor-Id: $VENDOR_ID" -F 'file=@scan.pdf'
```

`202` with a `pending` extraction. Storing the file in S3 starts the OCR pipeline.

### `GET /extractions/{id}`

Poll until `status` is `completed` or `failed`.

```json
{
  "id": "…", "status": "completed",
  "fileName": "scan.pdf", "extension": "pdf", "contentType": "application/pdf", "size": 284913,
  "text": "ABC Supplies Inc.\nINVOICE\nInvoice No.: INV-2026-0900\n…",
  "entities": [{ "type": "DATE", "text": "2026-09-10", "score": 0.994 }],
  "candidates": [
    { "id": "c2", "value": "INV-2026-0900", "source": "Invoice No.",
      "entityType": null, "score": null, "suggestedField": "invoiceNo" },
    { "id": "c7", "value": "2026-09-10", "source": "DATE",
      "entityType": "DATE", "score": 0.994, "suggestedField": "invoiceDate" },
    { "id": "c1", "value": "ABC Supplies Inc.", "source": "First line",
      "entityType": null, "score": null, "suggestedField": "vendorName" },
    { "id": "c9", "value": "11,200.00", "source": "Largest amount",
      "entityType": null, "score": null, "suggestedField": "invoiceAmount" }
  ],
  "errorMessage": null,
  "createdAt": "…", "completedAt": "…"
}
```

- `suggestedField` matches a key from `reference-data.extractableFields`, so a client can drop
  each candidate onto the right form field. It is `null` when nothing could be inferred, and each
  field is suggested at most once.
- `candidates` are ordered with suggested fields first (in form order), then the rest. At most 50.
- `source` says where the value came from, for showing the user: the label matched on the page
  (`Invoice No.`), one of `Reference number` / `Largest amount` / `First line`, or the detected
  entity type.
- `entityType` and `score` are set only for values that came from entity detection; label matches
  have `null` for both.

Values are raw text, not parsed — `"11,200.00"` needs converting before it goes in
`invoiceAmount`. Treat every candidate as a suggestion for the user to confirm.

On `failed`, `errorMessage` says why.

### `DELETE /extractions/{id}`

Discards the scan and its extracted text. `204`.

## AI assistant

### `POST /ai/messages`

Relays the message to the agent service and returns the stored question and answer. See
[Part 1](#part-1--the-ai-agent-and-agentcore) for what happens on the other side.

| Body field | |
| --- | --- |
| `content` | **Required.** The vendor's message; max 4000 characters, cannot be blank. |
| `sessionId` | Continue a conversation. Omit to start one. |
| `agentId` | Which agent to route to (defined by the agent service). Ignored when `sessionId` is given. |
| `context` | `{ "invoiceId": "..." }` — what the vendor was looking at. Ignored when `sessionId` is given. |

```bash
curl -X POST localhost:8000/api/v1/ai/messages \
  -H 'Content-Type: application/json' -H "X-Vendor-Id: $VENDOR_ID" -d '{
    "content": "Are my documents complete for this invoice?",
    "agentId": "document-checker",
    "context": { "invoiceId": "3a7f..." }
  }'
```

```json
{
  "sessionId": "9f1c8e4a-5b2d-4c77-9a31-6de0f1b82c44",
  "userMessage": { "id": "…", "role": "user", "content": "Are my documents complete…",
                   "createdAt": "2026-09-24T01:33:36.125211+00:00", "citations": [] },
  "assistantMessage": { "id": "…", "role": "assistant", "content": "You have the invoice and the PO…",
                        "createdAt": "…",
                        "citations": [{ "type": "document", "id": "…", "label": "PO-2026-0300.pdf" }] }
}
```

Keep `sessionId` and send it with the next message. `agentId` and `context` are fixed when the
conversation starts, so changing them mid-conversation means starting a new one.

Beyond the shared codes: `409` when the conversation hits `AI_MAX_MESSAGES_PER_SESSION`, `429` at
more than `AI_RATE_LIMIT_PER_MINUTE` messages a minute, and `503` when the agent is unreachable
or unconfigured. A reply can take up to about a minute.

### `POST /ai/messages/stream`

The same request, with the answer streamed back as it is written — what the portal's chat page
uses, so the vendor sees words appear instead of waiting a minute for a block of text. The
response is `text/event-stream`:

```
event: delta
data: {"text": "Invoice INV-2026-0524 "}

event: delta
data: {"text": "is with AP for validation."}

event: done
data: {"sessionId": "…", "userMessage": {…}, "assistantMessage": {…}}
```

```bash
curl -N -X POST localhost:8000/api/v1/ai/messages/stream \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -H "X-Vendor-Id: $VENDOR_ID" -d '{"content": "Where is INV-2026-0524?"}'
```

- `done` carries exactly the body `POST /ai/messages` returns; store its `sessionId` for the
  next message.
- `event: error` with `{"code", "message"}` is sent if the answer breaks off part-way. Failures
  *before* the first chunk (unknown conversation, rate limit, agent down) are normal HTTP errors
  with the usual status codes, so a client should handle both.
- The transcript is written only once the answer is complete: a stream the vendor abandons
  stores nothing.
- Whether the words really arrive one by one depends on the agent. If it answers
  `text/event-stream`, its chunks are passed through (`delta`, `chunk`, `token`, `text` or
  `content` keys, or a bare string, plus an optional final object with `citations` and `usage`).
  An agent that replies with a single JSON body still works: the answer arrives as one `delta`.

## AP back office

Not vendor-facing, and **not protected yet** — these take no `X-Vendor-Id` and will accept any
caller. Restrict them when authentication lands.

### `POST /ap/invoices/{id}/advance`

Moves the invoice one stage on: AP Validation → Business Approval → Ariba Processing →
S/4HANA Transfer → Payment Scheduled → Paid. The status follows the stage.

```bash
curl -X POST localhost:8000/api/v1/ap/invoices/$INVOICE_ID/advance \
  -H 'Content-Type: application/json' -d '{"comment": "Validated against PO-2026-0300."}'
```

`comment` is optional; when given it is posted to the invoice as an `AP Team` comment.

### `POST /ap/invoices/{id}/reject`

```bash
curl -X POST localhost:8000/api/v1/ap/invoices/$INVOICE_ID/reject \
  -H 'Content-Type: application/json' -d '{"reason": "Amounts do not match the PO."}'
```

`reason` is **required** (max 1000 characters) and is posted as an `AP Team` comment. Only valid
during AP validation or business approval.

---

## Typical flows

**Submit an invoice** — the order the Vue app uses:

```
POST   /invoices                      → draft, keep the id
POST   /invoices/{id}/documents       → attach the invoice PDF and supporting files
POST   /invoices/{id}/submit          → 200, or 422 with per-field errors
GET    /invoices/{id}/timeline        → show progress from here on
```

**Scan to pre-fill:**

```
POST   /extractions                   → 202, pending
GET    /extractions/{id}              → poll until completed
                                      → map candidates[].suggestedField onto the form
POST   /invoices                      → create the draft from the corrected values
```

**Chat:**

```
POST   /ai/messages {content}                       → 201, keep sessionId
POST   /ai/messages {content, sessionId}            → each follow-up
```

## Related docs

- [README](../README.md) — setup, storage, configuration, the AI assistant section
- [Database schema](database-schema.md) — ER diagram and table notes
- `/swagger-ui` on the running API — generated, always matches the code

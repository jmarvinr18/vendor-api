import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

load_dotenv(APP_DIR / ".env")


class Config:
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB = int(os.getenv("REDIS_DB", 0))

    REDIS_URL = os.getenv("REDIS_URL")

    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    PROPAGATE_EXCEPTIONS = True
    API_TITLE = "Vendor Portal API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = ""
    OPENAPI_SWAGGER_UI_PATH = "/swagger-ui"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

    # Where invoice and supporting documents are stored: "s3" or "local" (development only).
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3")

    # S3 settings. Credentials are not configured here: boto3 uses the standard AWS chain
    # (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, AWS_PROFILE, or the instance/task role).
    S3_BUCKET = os.getenv("S3_BUCKET")
    S3_REGION = os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION")
    # Only for S3-compatible services such as LocalStack or MinIO.
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
    S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "vendor-portal")
    # "AES256" (S3-managed keys), "aws:kms", or empty to use the bucket's default.
    S3_SERVER_SIDE_ENCRYPTION = os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256")
    S3_KMS_KEY_ID = os.getenv("S3_KMS_KEY_ID")
    # Where the OCR pipeline writes its result for a scan, in the same bucket. Placeholders:
    # {stem} scan file name without extension, {name}, {dir}, {key} (full key without extension).
    EXTRACTION_RESULT_KEY_TEMPLATE = os.getenv("EXTRACTION_RESULT_KEY_TEMPLATE", "processed/{stem}.jsonl")

    # ---- AI assistant ----
    # The agent itself is a separate service (LangChain / LangGraph on Amazon Bedrock
    # AgentCore Runtime); this API only relays the vendor's message to it.
    # Set the runtime ARN to call AgentCore. Credentials come from the same AWS chain as S3;
    # the IAM identity needs bedrock-agentcore:InvokeAgentRuntime on it.
    AI_AGENT_RUNTIME_ARN = os.getenv("AI_AGENT_RUNTIME_ARN")
    AI_AGENT_REGION = os.getenv("AI_AGENT_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-southeast-1"
    # The AgentCore endpoint version to invoke: "DEFAULT", or an endpoint name.
    AI_AGENT_QUALIFIER = os.getenv("AI_AGENT_QUALIFIER", "DEFAULT")
    # Used instead when no runtime ARN is set: the agent reachable over plain HTTP
    # (the agent repository running locally, or behind a load balancer).
    AI_AGENT_URL = os.getenv("AI_AGENT_URL")
    # Sent as "Authorization: Bearer ..." with AI_AGENT_URL.
    AI_AGENT_TOKEN = os.getenv("AI_AGENT_TOKEN")
    AI_AGENT_TIMEOUT_SECONDS = float(os.getenv("AI_AGENT_TIMEOUT_SECONDS", 75))
    # Stored messages sent to the agent as history, conversation length, and messages a
    # vendor may send per minute.
    AI_HISTORY_MESSAGES = int(os.getenv("AI_HISTORY_MESSAGES", 20))
    AI_MAX_MESSAGES_PER_SESSION = int(os.getenv("AI_MAX_MESSAGES_PER_SESSION", 100))
    AI_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_RATE_LIMIT_PER_MINUTE", 10))

    # Used when STORAGE_BACKEND=local.
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(PROJECT_DIR / "uploads"))
    # Ten 10MB files plus form overhead.
    MAX_CONTENT_LENGTH = 110 * 1024 * 1024

    # Comma-separated origins allowed to call the API (the Vue dev server by default).
    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    # Used when a request has no X-Vendor-Id header. Development only, until auth is in place.
    DEFAULT_VENDOR_ID = os.getenv("DEFAULT_VENDOR_ID")

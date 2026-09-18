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

    # Uploaded supporting documents are stored on disk under this folder.
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

"""Writes OCR results to the vendor portal's document_extractions table."""

import json
import ssl
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone


class ExtractionRepository(ABC):
    @abstractmethod
    def complete(self, storage_key: str, text: str, entities: list[dict]) -> bool:
        """Stores the result on a pending row. False if no pending row matched."""

    @abstractmethod
    def fail(self, storage_key: str, message: str) -> bool:
        """Marks a pending row failed. False if no pending row matched."""


# Only pending rows change: a retried or duplicated execution can't overwrite a finished
# result, and a scan the vendor discarded (row deleted) is left alone.
_COMPLETE_SQL = (
    "UPDATE document_extractions SET status = 'completed', extracted_text = :text, "
    "entities = CAST(:entities AS json), error_message = NULL, completed_at = :now "
    "WHERE storage_key = :storage_key AND status = 'pending'"
)
_FAIL_SQL = (
    "UPDATE document_extractions SET status = 'failed', extracted_text = NULL, entities = NULL, "
    "error_message = :message, completed_at = :now "
    "WHERE storage_key = :storage_key AND status = 'pending'"
)


class PostgresExtractionRepository(ExtractionRepository):
    def __init__(self, connect: Callable):
        self._connect = connect

    def _execute(self, sql: str, **params) -> bool:
        conn = self._connect()
        try:
            conn.run(sql, now=datetime.now(timezone.utc), **params)
            return conn.row_count > 0
        finally:
            conn.close()

    def complete(self, storage_key: str, text: str, entities: list[dict]) -> bool:
        return self._execute(_COMPLETE_SQL, storage_key=storage_key, text=text, entities=json.dumps(entities))

    def fail(self, storage_key: str, message: str) -> bool:
        return self._execute(_FAIL_SQL, storage_key=storage_key, message=message[:1000])


def ssl_context(mode: str, root_cert: str | None) -> ssl.SSLContext | None:
    """verify (default): encrypted, server certificate verified (set DB_SSL_ROOT_CERT to the
    RDS CA bundle, which isn't in the default trust store); require: encrypted, unverified;
    disable: plain TCP (local databases only)."""
    mode = (mode or "verify").lower()
    if mode == "disable":
        return None
    if mode == "require":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if mode == "verify":
        return ssl.create_default_context(cafile=root_cert or None)
    raise ValueError(f"Unknown DB_SSL_MODE '{mode}'. Use verify, require or disable.")


def pg8000_connector(config: dict, context: ssl.SSLContext | None) -> Callable:
    import pg8000.native

    def connect():
        return pg8000.native.Connection(
            user=config["username"],
            password=config["password"],
            host=config["host"],
            port=int(config.get("port", 5432)),
            database=config.get("dbname") or config.get("database"),
            ssl_context=context,
            timeout=10,
        )

    return connect

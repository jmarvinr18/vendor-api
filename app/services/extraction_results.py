"""Where the OCR pipeline's results come from.

The pipeline (EventBridge → Step Functions → Textract/Comprehend) writes one JSONL file per
scan to processed/<stem>.jsonl in the document bucket, a few seconds to minutes after the
scan is uploaded:
    {"source_key": "vendor-portal/extractions/<id>/<stem>.pdf", "text": "...",
     "entities": [{"type", "text", "score"}], "pii_spans": [...], "char_count": 3025}
"""

import json
import logging
import posixpath
from abc import ABC, abstractmethod
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from app.services.storage import StorageError

log = logging.getLogger(__name__)


class InvalidExtractionOutput(ValueError):
    """The pipeline wrote a result file that can't be read."""


@dataclass(frozen=True)
class ExtractionOutput:
    source_key: str
    text: str
    entities: list[dict]


def parse_output(raw: str) -> list[ExtractionOutput]:
    """Accepts JSON Lines or a single (possibly pretty-printed) JSON document."""
    raw = raw.lstrip("﻿").strip()
    if not raw:
        raise InvalidExtractionOutput("The result file is empty.")
    try:
        records = [json.loads(raw)]
    except json.JSONDecodeError:
        try:
            records = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise InvalidExtractionOutput(f"The result file is not valid JSON: {exc}") from exc

    outputs = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("source_key"), str):
            raise InvalidExtractionOutput("A result record has no source_key.")
        text = record.get("text")
        entities = record.get("entities")
        outputs.append(
            ExtractionOutput(
                source_key=record["source_key"],
                text=text if isinstance(text, str) else "",
                entities=[e for e in entities if isinstance(e, dict)] if isinstance(entities, list) else [],
            )
        )
    return outputs


class ExtractionResultSource(ABC):
    @abstractmethod
    def fetch(self, storage_key: str) -> ExtractionOutput | None:
        """The pipeline's result for a scan, or None while it isn't available yet.

        Raises InvalidExtractionOutput if a result exists but can't be used, and StorageError
        if the result store can't be reached."""


class NoExtractionResults(ExtractionResultSource):
    """For local storage: there is no pipeline, results are recorded with `flask extraction`."""

    def fetch(self, storage_key: str) -> ExtractionOutput | None:
        return None


class S3ExtractionResultSource(ExtractionResultSource):
    def __init__(self, client, bucket: str, *, key_prefix: str = "", key_template: str = "processed/{stem}.jsonl"):
        self._client = client
        self._bucket = bucket
        prefix = key_prefix.strip("/")
        self._prefix = prefix + "/" if prefix else ""
        self._template = key_template

    def result_key(self, storage_key: str) -> str:
        """Placeholders: {stem} scan file name without extension, {name}, {dir}, {key}
        (the scan's full S3 key without extension)."""
        source_key = self._prefix + storage_key
        directory, name = posixpath.split(source_key)
        return self._template.format(
            stem=posixpath.splitext(name)[0], name=name, dir=directory, key=posixpath.splitext(source_key)[0]
        )

    def fetch(self, storage_key: str) -> ExtractionOutput | None:
        key = self.result_key(storage_key)
        try:
            body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                return None  # The pipeline hasn't finished yet.
            log.exception("Could not read extraction result %s", key)
            raise StorageError(f"Could not read {key}.") from exc
        except BotoCoreError as exc:
            log.exception("Could not read extraction result %s", key)
            raise StorageError(f"Could not read {key}.") from exc

        source_key = self._prefix + storage_key
        for output in parse_output(body.decode("utf-8")):
            if output.source_key == source_key:
                return output
        raise InvalidExtractionOutput(f"{key} has no result for {source_key}.")

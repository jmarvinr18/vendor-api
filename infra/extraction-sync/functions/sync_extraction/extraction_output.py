"""Parsing of the OCR pipeline's JSONL output. Pure functions: no AWS or database access.

A processed file holds one record per source document:
    {"source_key": "vendor-portal/extractions/<id>/<random>.pdf",
     "text": "...", "entities": [{"type", "text", "score"}], "pii_spans": [...], "char_count": 3025}

Files may be true JSON Lines (one record per line) or a single pretty-printed JSON document;
both are accepted.
"""

import json
import posixpath
from dataclasses import dataclass


class InvalidOutput(ValueError):
    """The processed file is not valid pipeline output."""


@dataclass(frozen=True)
class ExtractionOutput:
    source_key: str
    text: str
    entities: list[dict]


def parse_output(raw: str) -> list[ExtractionOutput]:
    raw = raw.lstrip("﻿").strip()
    if not raw:
        raise InvalidOutput("The processed file is empty.")
    try:
        records = [json.loads(raw)]
    except json.JSONDecodeError:
        try:
            records = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise InvalidOutput(f"The processed file is not valid JSON or JSON Lines: {exc}") from exc

    outputs = []
    for i, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("source_key"), str):
            raise InvalidOutput(f"Record {i} has no source_key.")
        text = record.get("text")
        outputs.append(
            ExtractionOutput(
                source_key=record["source_key"],
                text=text if isinstance(text, str) else "",
                entities=normalize_entities(record.get("entities")),
            )
        )
    return outputs


def normalize_entities(entities) -> list[dict]:
    """Keeps the {type, text, score} shape the API's extraction mapper reads."""
    if not isinstance(entities, list):
        return []
    normalized = []
    for entity in entities:
        if not isinstance(entity, dict) or not str(entity.get("text") or "").strip():
            continue
        try:
            score = float(entity.get("score"))
        except (TypeError, ValueError):
            score = None
        normalized.append({"type": str(entity.get("type") or "OTHER"), "text": str(entity["text"]), "score": score})
    return normalized


class KeyMapper:
    """Translates between S3 keys and the API's storage keys."""

    EXTRACTIONS_FOLDER = "extractions/"

    def __init__(self, key_prefix: str, processed_key_template: str):
        prefix = key_prefix.strip("/")
        self._prefix = prefix + "/" if prefix else ""
        self._template = processed_key_template

    def storage_key(self, source_key: str) -> str | None:
        """The document_extractions.storage_key for an S3 key, or None if the object isn't a
        scanned-invoice extraction (e.g. an invoice document under <prefix>/invoices/)."""
        if not source_key.startswith(self._prefix):
            return None
        key = source_key[len(self._prefix):]
        return key if key.startswith(self.EXTRACTIONS_FOLDER) else None

    def processed_key(self, source_key: str) -> str:
        """Where the pipeline wrote the JSONL for a source object.

        Template placeholders: {key} source key without extension, {dir} its folder,
        {name} file name with extension, {stem} file name without extension.
        """
        directory, name = posixpath.split(source_key)
        stem = posixpath.splitext(name)[0]
        return self._template.format(
            key=posixpath.splitext(source_key)[0], dir=directory, name=name, stem=stem
        )

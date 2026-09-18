import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage


def _root() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]).resolve()


def file_size(upload: FileStorage) -> int:
    stream = upload.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def save_invoice_file(invoice_id: uuid.UUID, upload: FileStorage, extension: str) -> str:
    """Saves an upload under a generated name and returns its path relative to UPLOAD_FOLDER.
    The original file name is only kept in the database, never used on disk."""
    relative = Path("invoices") / str(invoice_id) / f"{uuid.uuid4().hex}.{extension}"
    target = _root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.save(target)
    return relative.as_posix()


def absolute_path(relative: str) -> Path | None:
    """Resolves a stored path, refusing anything outside UPLOAD_FOLDER."""
    root = _root()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def delete_file(relative: str | None) -> None:
    if not relative:
        return
    path = absolute_path(relative)
    if path:
        path.unlink(missing_ok=True)

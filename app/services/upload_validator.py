import os
from dataclasses import dataclass
from typing import BinaryIO

from app.constants import DOCUMENT_TYPES, UPLOAD_MAX_FILE_SIZE, UPLOAD_MAX_FILES
from app.services.errors import ValidationFailed


@dataclass(frozen=True)
class FileType:
    content_type: str
    # Leading bytes a genuine file of this type starts with.
    signatures: tuple[bytes, ...]


# Accepted formats. The content type we store comes from here, never from the client.
ACCEPTED_FILE_TYPES: dict[str, FileType] = {
    "pdf": FileType("application/pdf", (b"%PDF-",)),
    "png": FileType("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": FileType("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": FileType("image/jpeg", (b"\xff\xd8\xff",)),
}


@dataclass(frozen=True)
class IncomingFile:
    """An uploaded file, independent of the web framework that received it."""

    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class ValidatedUpload:
    name: str
    extension: str
    size: int
    doc_type: str
    content_type: str
    stream: BinaryIO


def guess_document_type(filename: str) -> str:
    """Document type from the file name, same convention as the Vue app."""
    upper = filename.upper()
    if upper.startswith("INV"):
        return "Invoice"
    if upper.startswith(("PO", "PR")):
        return "Purchase Order"
    if upper.startswith("DR"):
        return "Delivery Receipt"
    return "Other"


def _stream_size(stream: BinaryIO) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


def _starts_with(stream: BinaryIO, signatures: tuple[bytes, ...]) -> bool:
    head = stream.read(max(len(s) for s in signatures))
    stream.seek(0)
    return any(head.startswith(s) for s in signatures)


class UploadValidator:
    """Checks a batch of uploads against the portal's rules. It only validates;
    it doesn't store anything."""

    def __init__(
        self,
        file_types: dict[str, FileType] = ACCEPTED_FILE_TYPES,
        max_file_size: int = UPLOAD_MAX_FILE_SIZE,
        max_files: int = UPLOAD_MAX_FILES,
        document_types: list[str] = DOCUMENT_TYPES,
    ):
        self._file_types = file_types
        self._max_file_size = max_file_size
        self._max_files = max_files
        self._document_types = document_types

    def validate(
        self, files: list[IncomingFile], doc_types: list[str], existing_count: int
    ) -> list[ValidatedUpload]:
        """Returns the uploads ready to store, or raises ValidationFailed listing every problem."""
        if not files:
            raise ValidationFailed("Attach at least one file in the 'files' field.")

        problems: list[str] = []
        accepted: list[ValidatedUpload] = []
        max_mb = self._max_file_size // (1024 * 1024)

        for i, incoming in enumerate(files):
            # Browsers may send a full path; keep only the base name.
            name = incoming.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            file_type = self._file_types.get(extension)
            doc_type = (doc_types[i] if i < len(doc_types) else "") or guess_document_type(name)
            size = _stream_size(incoming.stream)

            if file_type is None:
                problems.append(f"{name}: unsupported format. Use PDF, JPG or PNG.")
            elif size == 0:
                problems.append(f"{name}: file is empty.")
            elif size > self._max_file_size:
                problems.append(f"{name}: exceeds the {max_mb}MB limit.")
            elif not _starts_with(incoming.stream, file_type.signatures):
                problems.append(f"{name}: the file content is not a valid {extension.upper()}.")
            elif doc_type not in self._document_types:
                problems.append(f"{name}: unknown document type '{doc_type}'.")
            else:
                accepted.append(
                    ValidatedUpload(name, extension, size, doc_type, file_type.content_type, incoming.stream)
                )

        if existing_count + len(files) > self._max_files:
            problems.append(f"You can upload up to {self._max_files} files.")
        if problems:
            raise ValidationFailed("Some files could not be uploaded.", errors={"files": problems})
        return accepted

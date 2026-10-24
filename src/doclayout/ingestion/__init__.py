"""Document ingestion and structural layout extraction package."""

from __future__ import annotations

from pathlib import Path

from doclayout.core.models import DocumentLayout
from doclayout.ingestion.docx import DocxLayoutExtractor
from doclayout.ingestion.exceptions import (
    CorruptedDocumentError,
    DocumentIngestionError,
    DocumentLimitExceededError,
    EmptyDocumentError,
    PasswordProtectedDocumentError,
)
from doclayout.ingestion.pdf import PDFLayoutExtractor


def extract_document_layout(
    data: bytes,
    filename: str,
    max_file_size_mb: int | None = None,
    max_pages: int | None = None,
) -> DocumentLayout:
    """Dispatch document layout extraction based on file extension (.pdf or .docx)."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return PDFLayoutExtractor(
            max_file_size_mb=max_file_size_mb,
            max_pages=max_pages,
        ).extract_from_bytes(data, filename=filename)
    if ext == ".docx":
        return DocxLayoutExtractor(
            max_file_size_mb=max_file_size_mb,
            max_pages=max_pages,
        ).extract_from_bytes(data, filename=filename)
    raise CorruptedDocumentError(
        f"Formato de archivo no soportado ('{ext}'). Use únicamente archivos .pdf o .docx."
    )


__all__ = [
    "CorruptedDocumentError",
    "DocumentIngestionError",
    "DocumentLimitExceededError",
    "DocxLayoutExtractor",
    "EmptyDocumentError",
    "PDFLayoutExtractor",
    "PasswordProtectedDocumentError",
    "extract_document_layout",
]

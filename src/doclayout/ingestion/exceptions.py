"""Custom exceptions for document ingestion and layout parsing."""

from __future__ import annotations


class DocumentIngestionError(Exception):
    """Base exception for errors during document ingestion and layout extraction."""


class PasswordProtectedDocumentError(DocumentIngestionError):
    """Raised when a document is encrypted or password-protected."""


class CorruptedDocumentError(DocumentIngestionError):
    """Raised when a document file is unreadable, truncated, or invalid."""


class EmptyDocumentError(DocumentIngestionError):
    """Raised when a document contains no pages or no extractable layout elements."""


class DocumentLimitExceededError(DocumentIngestionError):
    """Raised when a document exceeds configured size or page limits."""

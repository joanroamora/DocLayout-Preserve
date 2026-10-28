"""Session state, SHA-256 extraction caching, and immutable job history management."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from doclayout.core.models import DocumentLayout, TransformationResult
from doclayout.ingestion import extract_document_layout
from doclayout.llm.schemas import TaskType
from doclayout.ui.preview import build_layout_wireframe_pdf
from doclayout.verification.reporter import DetailedQualityReport


class JobHistoryEntry(BaseModel):
    """Snapshot of a processed or failed document transformation job in session history."""

    job_id: str = Field(default_factory=lambda: f"job_{uuid4().hex[:10]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    filename: str
    file_sha256: str
    file_bytes: bytes = Field(default=b"", repr=False)
    task_type: TaskType = Field(default=TaskType.TRANSLATE)
    target_language: str = Field(default="Español")
    style_instructions: str = Field(default="")
    status: str = Field(default="completed", pattern=r"^(completed|failed)$")
    error_message: str | None = Field(default=None)
    model_used: str = Field(default="")
    original_layout: DocumentLayout | None = Field(default=None)
    reconstructed_layout: DocumentLayout | None = Field(default=None)
    fitted_results_by_block_id: dict[str, TransformationResult] = Field(default_factory=dict)
    original_preview_pdf_bytes: bytes | None = Field(default=None, repr=False)
    pdf_output_bytes: bytes | None = Field(default=None, repr=False)
    docx_output_bytes: bytes | None = Field(default=None, repr=False)
    export_json_bytes: bytes | None = Field(default=None, repr=False)
    quality_report: DetailedQualityReport | None = Field(default=None)


class SessionStore:
    """Isolated per-session container holding extraction cache, active job, and history."""

    def __init__(self) -> None:
        self.extraction_cache: dict[str, tuple[DocumentLayout, bytes]] = {}
        self.active_job: JobHistoryEntry | None = None
        self.history: list[JobHistoryEntry] = []
        self.is_processing: bool = False
        self.current_stage_label: str = ""
        self.editor_reset_counter: int = 0

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        """Compute deterministic SHA-256 fingerprint for uploaded document bytes."""
        return hashlib.sha256(data).hexdigest()

    def get_or_extract_layout(
        self,
        file_bytes: bytes,
        filename: str,
    ) -> tuple[str, DocumentLayout, bytes]:
        """Return cached (sha256, layout, preview_pdf_bytes) or extract once and cache."""
        digest = self.compute_sha256(file_bytes)
        cached = self.extraction_cache.get(digest)
        if cached is not None:
            layout, preview_pdf = cached
            return digest, layout.model_copy(deep=True), preview_pdf

        layout = extract_document_layout(file_bytes, filename=filename)
        preview_pdf = (
            file_bytes if layout.source_format == "pdf" else build_layout_wireframe_pdf(layout)
        )
        self.extraction_cache[digest] = (layout.model_copy(deep=True), preview_pdf)
        return digest, layout, preview_pdf

    def upsert_history(self, entry: JobHistoryEntry) -> None:
        """Insert or update a job snapshot in history without creating retry duplicates."""
        snapshot = entry.model_copy(deep=True)
        for idx, existing in enumerate(self.history):
            if existing.job_id == snapshot.job_id:
                self.history[idx] = snapshot
                # Move updated retry to newest position
                updated = self.history.pop(idx)
                self.history.insert(0, updated)
                return
        self.history.insert(0, snapshot)

    def start_new_document(self) -> None:
        """Archive active job if needed and reset active editor while preserving history & cache."""
        if self.active_job is not None:
            self.upsert_history(self.active_job)
        self.active_job = None
        self.current_stage_label = ""
        self.is_processing = False
        self.editor_reset_counter += 1

    def clear_history_only(self) -> None:
        """Empty session history without altering the active editor or extraction cache."""
        self.history.clear()

    def clear_entire_session(self, tmp_dir: Path | None = None) -> None:
        """Purge history, extraction cache, active editor, and temporary files on disk."""
        self.history.clear()
        self.extraction_cache.clear()
        self.active_job = None
        self.current_stage_label = ""
        self.is_processing = False
        self.editor_reset_counter += 1

        if tmp_dir is not None and tmp_dir.exists() and tmp_dir.is_dir():
            for child in tmp_dir.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except OSError:
                    pass

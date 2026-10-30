"""Pydantic schemas and state enums for the asynchronous Job API Gateway and Workers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from doclayout.core.models import QualityReport
from doclayout.llm.schemas import TaskType
from doclayout.verification.reporter import TrafficLightStatus


class JobState(StrEnum):
    """Lifecycle status of an asynchronous document processing job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStage(StrEnum):
    """Deterministic processing checkpoints enabling idempotent worker recovery."""

    PENDING = "pending"
    INGESTED = "ingested"
    TRANSFORMED = "transformed"
    RECONSTRUCTED = "reconstructed"
    VERIFIED = "verified"


class ArtifactType(StrEnum):
    """Downloadable output artifacts persisted with authenticated encryption and TTL."""

    SOURCE = "source"
    PDF = "pdf"
    DOCX = "docx"
    REPORT = "report"


class JobRecord(BaseModel):
    """Internal persisted metadata record scoped strictly to an isolated session/tenant."""

    job_id: str = Field(..., min_length=6, max_length=64)
    session_id: str = Field(..., min_length=3, max_length=64)
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    filename: str = Field(..., min_length=1, max_length=255)
    file_sha256: str = Field(..., min_length=64, max_length=64)
    task_type: TaskType = Field(default=TaskType.TRANSLATE)
    target_language: str = Field(default="Spanish", min_length=2, max_length=64)
    style_instructions: str = Field(default="", max_length=500)
    state: JobState = Field(default=JobState.QUEUED)
    current_stage: JobStage = Field(default=JobStage.PENDING)
    attempts: int = Field(default=0, ge=0)
    model_used: str = Field(default="")
    traffic_light: TrafficLightStatus | None = Field(default=None)
    quality_summary: QualityReport | None = Field(default=None)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime


class JobStatusResponse(BaseModel):
    """Public API response representing a tenant-scoped asynchronous job."""

    job_id: str
    filename: str
    file_sha256: str
    task_type: TaskType
    target_language: str
    state: JobState
    current_stage: JobStage
    attempts: int
    idempotent_replay: bool = False
    model_used: str = ""
    traffic_light: TrafficLightStatus | None = None
    quality_summary: QualityReport | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    download_urls: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(
        cls,
        record: JobRecord,
        idempotent_replay: bool = False,
    ) -> JobStatusResponse:
        """Build a sanitized API response with artifact download URLs when completed."""
        urls: dict[str, str] = {}
        if record.state == JobState.COMPLETED:
            base = f"/api/v1/jobs/{record.job_id}/artifacts"
            urls = {
                "pdf": f"{base}/pdf",
                "docx": f"{base}/docx",
                "report": f"{base}/report",
            }
        return cls(
            job_id=record.job_id,
            filename=record.filename,
            file_sha256=record.file_sha256,
            task_type=record.task_type,
            target_language=record.target_language,
            state=record.state,
            current_stage=record.current_stage,
            attempts=record.attempts,
            idempotent_replay=idempotent_replay,
            model_used=record.model_used,
            traffic_light=record.traffic_light,
            quality_summary=record.quality_summary,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
            download_urls=urls,
        )


class HealthResponse(BaseModel):
    """Lightweight liveness check response (consumes zero Gemini API quota)."""

    status: str = "ok"
    service: str = "doclayout-api"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReadinessResponse(BaseModel):
    """Readiness & HPA custom-metrics probe response (consumes zero Gemini API quota)."""

    status: str = "ready"
    storage_ready: bool = True
    queue_ready: bool = True
    queue_depth: int = Field(default=0, ge=0)
    queue_oldest_age_seconds: float = Field(default=0.0, ge=0.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

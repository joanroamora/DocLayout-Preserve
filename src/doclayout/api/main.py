"""FastAPI REST Job API Gateway for asynchronous document layout processing."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse

from doclayout.api.queue import JobQueueProtocol, build_job_queue
from doclayout.api.schemas import (
    ArtifactType,
    HealthResponse,
    JobRecord,
    JobStage,
    JobState,
    JobStatusResponse,
    ReadinessResponse,
)
from doclayout.api.storage import EncryptedArtifactStore, sanitize_session_id
from doclayout.core.config import Settings, get_settings
from doclayout.llm.schemas import TaskType

_SAFE_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_SAFE_FILENAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_MIME_BY_ARTIFACT: dict[ArtifactType, tuple[str, str]] = {
    ArtifactType.PDF: ("application/pdf", ".pdf"),
    ArtifactType.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    ArtifactType.REPORT: ("application/json", ".quality.json"),
}


def validate_document_magic_bytes(filename: str, payload: bytes) -> str:
    """Validate `.pdf` / `.docx` extension and binary magic header signature."""
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El archivo recibido está vacío.",
        )

    clean_name = Path(filename or "").name.strip()
    ext = Path(clean_name).suffix.lower()
    if ext not in {".pdf", ".docx"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Formato no soportado. Solo se aceptan documentos .pdf y .docx.",
        )

    if ext == ".pdf" and b"%PDF-" not in payload[:1024]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cabecera binaria inválida: el archivo no corresponde a un PDF auténtico.",
        )

    if ext == ".docx" and not payload.startswith(b"PK\x03\x04"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cabecera binaria inválida: el archivo no corresponde a un contenedor DOCX.",
        )

    return clean_name


def _require_session_id(x_session_id: str | None) -> str:
    if not x_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El encabezado X-Session-ID es obligatorio para aislar la sesión.",
        )
    try:
        return sanitize_session_id(x_session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _resolve_idempotency_key(
    raw_header: str | None,
    file_sha256: str,
    task_type: TaskType,
    target_language: str,
    style_instructions: str,
) -> str:
    if raw_header is not None and raw_header.strip():
        cleaned = raw_header.strip()
        if not _SAFE_IDEMPOTENCY_RE.fullmatch(cleaned):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key inválida (use 8-128 caracteres alfanuméricos, '-', '_' o ':').",
            )
        return cleaned

    composite = (
        f"{file_sha256}|{task_type.value}|{target_language.strip()}|{style_instructions.strip()}"
    )
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:40]
    return f"auto_{digest}"


def create_app(
    settings: Settings | None = None,
    store: EncryptedArtifactStore | None = None,
    queue: JobQueueProtocol | None = None,
) -> FastAPI:
    """Create configured FastAPI Job Gateway instance with dependency injection support."""
    cfg = settings or get_settings()
    artifact_store = store or EncryptedArtifactStore(
        root_dir=cfg.ARTIFACT_STORE_DIR,
        ttl_seconds=cfg.ARTIFACT_TTL_SECONDS,
        encryption_secret=cfg.ARTIFACT_ENCRYPTION_SECRET.get_secret_value(),
    )
    job_queue = queue or build_job_queue(cfg.REDIS_URL)

    api = FastAPI(
        title="DocLayout-Preserve Job API Gateway",
        version="0.1.0",
        description="API asíncrona para preservación de diseño documental con aislamiento por sesión.",
    )
    api.state.store = artifact_store
    api.state.queue = job_queue
    api.state.settings = cfg

    @api.get("/healthz", response_model=HealthResponse, tags=["probes"])
    def healthz() -> HealthResponse:
        """Kubernetes liveness probe — consumes strictly zero Gemini API quota."""
        return HealthResponse()

    @api.get("/readyz", response_model=ReadinessResponse, tags=["probes"])
    def readyz() -> JSONResponse | ReadinessResponse:
        """Kubernetes startup/readiness probe & HPA queue-depth metrics endpoint (zero quota)."""
        storage_ok = artifact_store.check_health()
        queue_ok = job_queue.check_health()
        ready = storage_ok and queue_ok
        payload = ReadinessResponse(
            status="ready" if ready else "degraded",
            storage_ready=storage_ok,
            queue_ready=queue_ok,
            queue_depth=job_queue.queue_depth() if queue_ok else 0,
            queue_oldest_age_seconds=job_queue.oldest_message_age_seconds() if queue_ok else 0.0,
        )
        if not ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload.model_dump(mode="json"),
            )
        return payload

    @api.post(
        "/api/v1/jobs",
        response_model=JobStatusResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["jobs"],
    )
    async def submit_job(
        response: Response,
        file: Annotated[UploadFile, File(...)],
        task_type: Annotated[TaskType, Form()] = TaskType.TRANSLATE,
        target_language: Annotated[str, Form()] = "Spanish",
        style_instructions: Annotated[str, Form()] = "",
        x_session_id: Annotated[str | None, Header()] = None,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> JobStatusResponse:
        """Validate uploaded document, store encrypted source with TTL, and enqueue async job."""
        session_id = _require_session_id(x_session_id)
        max_bytes = cfg.MAX_FILE_SIZE_MB * 1024 * 1024
        raw_bytes = await file.read(max_bytes + 1)
        if len(raw_bytes) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"El archivo excede el tamaño máximo permitido ({cfg.MAX_FILE_SIZE_MB} MB).",
            )

        clean_filename = validate_document_magic_bytes(file.filename or "", raw_bytes)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        resolved_idem_key = _resolve_idempotency_key(
            raw_header=idempotency_key,
            file_sha256=file_sha256,
            task_type=task_type,
            target_language=target_language,
            style_instructions=style_instructions,
        )

        # Fast-path concurrency guard (only blocks genuinely new concurrent jobs)
        existing_jobs = artifact_store.list_jobs(session_id)
        has_idem_match = any(j.idempotency_key == resolved_idem_key for j in existing_jobs)
        if (
            not has_idem_match
            and artifact_store.count_active_jobs(session_id) >= cfg.MAX_CONCURRENT_JOBS_PER_SESSION
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Límite de trabajos simultáneos alcanzado para esta sesión.",
            )

        candidate = JobRecord(
            job_id=f"job_{uuid4().hex[:16]}",
            session_id=session_id,
            idempotency_key=resolved_idem_key,
            filename=clean_filename,
            file_sha256=file_sha256,
            task_type=task_type,
            target_language=target_language.strip() or "Spanish",
            style_instructions=style_instructions.strip()[:500],
            state=JobState.QUEUED,
            current_stage=JobStage.PENDING,
            expires_at=artifact_store.compute_default_expiry(),
        )
        persisted, is_replay = artifact_store.create_or_get_idempotent_job(
            record=candidate,
            source_bytes=raw_bytes,
        )
        if not is_replay:
            job_queue.enqueue(job_id=persisted.job_id, session_id=session_id)
        elif persisted.state == JobState.COMPLETED:
            response.status_code = status.HTTP_200_OK

        return JobStatusResponse.from_record(persisted, idempotent_replay=is_replay)

    @api.get("/api/v1/jobs", response_model=list[JobStatusResponse], tags=["jobs"])
    def list_session_jobs(
        x_session_id: Annotated[str | None, Header()] = None,
    ) -> list[JobStatusResponse]:
        """Return unexpired jobs strictly belonging to the caller's session."""
        session_id = _require_session_id(x_session_id)
        records = artifact_store.list_jobs(session_id)
        return [JobStatusResponse.from_record(rec) for rec in records]

    @api.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse, tags=["jobs"])
    def get_job_status(
        job_id: str,
        x_session_id: Annotated[str | None, Header()] = None,
    ) -> JobStatusResponse:
        """Query job status, current stage, and quality summary (404 on cross-session access)."""
        session_id = _require_session_id(x_session_id)
        record = artifact_store.get_job(session_id=session_id, job_id=job_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trabajo no encontrado o expirado para esta sesión.",
            )
        return JobStatusResponse.from_record(record)

    @api.get("/api/v1/jobs/{job_id}/artifacts/{artifact_kind}", tags=["jobs"])
    def download_artifact(
        job_id: str,
        artifact_kind: str,
        x_session_id: Annotated[str | None, Header()] = None,
    ) -> Response:
        """Decrypt and stream reconstructed `.pdf`, `.docx`, or JSON quality report."""
        session_id = _require_session_id(x_session_id)
        if artifact_kind not in {"pdf", "docx", "report"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tipo de artefacto inválido. Use 'pdf', 'docx' o 'report'.",
            )

        record = artifact_store.get_job(session_id=session_id, job_id=job_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trabajo no encontrado o expirado para esta sesión.",
            )
        if record.state != JobState.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"El trabajo aún no ha finalizado (estado actual: {record.state.value}).",
            )

        art_enum = ArtifactType(artifact_kind)
        decrypted = artifact_store.load_artifact(
            session_id=session_id,
            job_id=job_id,
            artifact_type=art_enum,
        )
        if decrypted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artefacto expirado o no disponible.",
            )

        media_type, suffix = _MIME_BY_ARTIFACT[art_enum]
        stem = _SAFE_FILENAME_CHARS_RE.sub("_", Path(record.filename).stem).strip("_") or "doc"
        out_name = f"{stem}_reconstruido{suffix}"
        return Response(
            content=decrypted,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )

    @api.delete(
        "/api/v1/jobs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["jobs"],
    )
    def delete_job(
        job_id: str,
        x_session_id: Annotated[str | None, Header()] = None,
    ) -> Response:
        """Immediately purge job metadata and shred encrypted artifacts on disk."""
        session_id = _require_session_id(x_session_id)
        deleted = artifact_store.delete_job(session_id=session_id, job_id=job_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trabajo no encontrado o expirado para esta sesión.",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return api


app = create_app()

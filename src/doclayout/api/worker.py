"""Asynchronous document processing worker with checkpoint recovery and K8s health probes."""

from __future__ import annotations

import json
import re
import signal
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import FrameType

from doclayout.api.queue import JobQueueProtocol, QueueMessage, SlidingWindowRateLimiter
from doclayout.api.schemas import ArtifactType, JobRecord, JobStage, JobState
from doclayout.api.storage import EncryptedArtifactStore
from doclayout.core.config import get_settings
from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    TableCell,
    TransformationResult,
    TransformationStatus,
)
from doclayout.ingestion import extract_document_layout
from doclayout.ingestion.exceptions import DocumentIngestionError
from doclayout.llm import (
    DocumentTransformationOutput,
    GeminiClient,
    LLMClientProtocol,
    TaskType,
    TransformationRequest,
)
from doclayout.llm.exceptions import LLMTransformationError
from doclayout.reconstruction import reconstruct_document
from doclayout.ui.preview import build_layout_wireframe_pdf
from doclayout.verification import verify_document_quality

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ZeroQuotaSimulationClient:
    """Deterministic zero-token LLM simulator for smoke tests, validation, and offline workers."""

    model_name: str = "zero-quota-simulation-worker"

    def transform_document(
        self,
        layout: DocumentLayout,
        request: TransformationRequest,
    ) -> DocumentTransformationOutput:
        """Transform layout blocks locally with zero network traffic and zero Gemini tokens."""
        results_by_id: dict[str, TransformationResult] = {}
        cells_by_id: dict[str, list[TableCell]] = {}

        for blk in layout.all_blocks:
            if blk.block_type == BlockType.IMAGE or (not blk.text.strip() and not blk.cells):
                results_by_id[blk.block_id] = TransformationResult(
                    block_id=blk.block_id,
                    original_text=blk.text,
                    transformed_text=blk.text,
                    status=TransformationStatus.COMPLETED,
                    applied_font_size=blk.font_size,
                    applied_line_height=blk.line_height,
                )
                continue

            new_text = self._transform_text(blk.text, request)
            if blk.block_type == BlockType.TABLE and blk.cells:
                cells_by_id[blk.block_id] = [
                    TableCell(
                        row_index=c.row_index,
                        col_index=c.col_index,
                        row_span=c.row_span,
                        col_span=c.col_span,
                        text=self._transform_text(c.text, request) if c.text else "",
                        bbox=c.bbox,
                    )
                    for c in blk.cells
                ]

            results_by_id[blk.block_id] = TransformationResult(
                block_id=blk.block_id,
                original_text=blk.text,
                transformed_text=new_text,
                status=TransformationStatus.COMPLETED,
                applied_font_size=blk.font_size,
                applied_line_height=blk.line_height,
            )

        return DocumentTransformationOutput(
            document_id=layout.document_id,
            model_used=self.model_name,
            results_by_block_id=results_by_id,
            transformed_cells_by_block_id=cells_by_id,
        )

    @staticmethod
    def _transform_text(text: str, request: TransformationRequest) -> str:
        clean = text.strip()
        if not clean:
            return text
        if request.task_type == TaskType.ANONYMIZE:
            return _EMAIL_RE.sub("[CORREO_ANONIMIZADO]", clean)
        if request.task_type == TaskType.SUMMARIZE:
            words = clean.split()
            return " ".join(words[: max(3, int(len(words) * 0.7))])
        if request.task_type == TaskType.REWRITE:
            return f"[Redacción optimizada] {clean}"
        lang_tag = request.target_language[:2].upper()
        return f"[{lang_tag}] {clean}"


class AsyncDocumentWorker:
    """
    Processes queued document jobs through the 4-stage pipeline with encrypted checkpoint
    recovery, Gemini quota rate-limiting, max-delivery guardrails, and graceful shutdown.
    """

    def __init__(
        self,
        store: EncryptedArtifactStore,
        queue: JobQueueProtocol,
        llm_client: LLMClientProtocol | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.store = store
        self.queue = queue
        self.llm_client: LLMClientProtocol = llm_client or self._default_llm_client()
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            max_requests_per_minute=get_settings().GEMINI_MAX_RPM
        )
        self.max_attempts = max(1, max_attempts)
        self._stop_event = threading.Event()
        self._is_ready = True

    @staticmethod
    def _default_llm_client() -> LLMClientProtocol:
        settings = get_settings()
        if settings.has_gemini_api_key:
            return GeminiClient(settings=settings)
        return ZeroQuotaSimulationClient()

    @property
    def is_stopping(self) -> bool:
        """Return True when graceful shutdown has been initiated."""
        return self._stop_event.is_set()

    def request_stop(self) -> None:
        """Signal worker loop to finish current in-flight job and exit cleanly."""
        self._stop_event.set()

    def check_readiness(self) -> bool:
        """Check worker readiness without invoking Gemini API."""
        return self._is_ready and self.store.check_health() and self.queue.check_health()

    def process_next(self, timeout_seconds: float = 0.1) -> JobRecord | None:
        """Claim and process a single message from the queue if available."""
        msg = self.queue.dequeue(timeout_seconds=timeout_seconds)
        if msg is None:
            return None
        return self._execute_message(msg)

    def run_forever(self, poll_timeout_seconds: float = 0.5) -> None:
        """Continuously process messages until `request_stop()` or SIGTERM/SIGINT arrives."""
        while not self._stop_event.is_set():
            self.process_next(timeout_seconds=poll_timeout_seconds)

    def _execute_message(self, msg: QueueMessage) -> JobRecord | None:
        record = self.store.get_job_internal(msg.job_id)
        if record is None:
            self.queue.ack(msg.receipt_id)
            return None

        if record.state == JobState.COMPLETED:
            self.queue.ack(msg.receipt_id)
            return record

        record.state = JobState.PROCESSING
        record.attempts += 1
        record = self.store.update_job(record)

        try:
            source_bytes = self.store.load_artifact(
                session_id=record.session_id,
                job_id=record.job_id,
                artifact_type=ArtifactType.SOURCE,
            )
            if source_bytes is None:
                raise DocumentIngestionError(
                    "No se encontró el artefacto fuente cifrado para procesar el trabajo."
                )

            # Stage 1: Ingestion & Layout Decomposition (with checkpoint recovery)
            ckpt_layout_bytes = self.store.load_checkpoint(
                record.session_id, record.job_id, "layout"
            )
            ckpt_preview_bytes = self.store.load_checkpoint(
                record.session_id, record.job_id, "orig_preview_pdf"
            )
            if ckpt_layout_bytes is not None and ckpt_preview_bytes is not None:
                original_layout = DocumentLayout.model_validate_json(
                    ckpt_layout_bytes.decode("utf-8")
                )
                orig_preview_pdf = ckpt_preview_bytes
            else:
                original_layout = extract_document_layout(
                    data=source_bytes,
                    filename=record.filename,
                )
                orig_preview_pdf = (
                    source_bytes
                    if original_layout.source_format == "pdf"
                    else build_layout_wireframe_pdf(original_layout)
                )
                self.store.save_checkpoint(
                    record.session_id,
                    record.job_id,
                    "layout",
                    original_layout.model_dump_json().encode("utf-8"),
                )
                self.store.save_checkpoint(
                    record.session_id,
                    record.job_id,
                    "orig_preview_pdf",
                    orig_preview_pdf,
                )
            record.current_stage = JobStage.INGESTED
            record = self.store.update_job(record)

            # Stage 2: Semantic Transformation (idempotent checkpoint avoids duplicate token usage)
            ckpt_tx_bytes = self.store.load_checkpoint(
                record.session_id, record.job_id, "transformation"
            )
            if ckpt_tx_bytes is not None:
                tx_output = DocumentTransformationOutput.model_validate_json(
                    ckpt_tx_bytes.decode("utf-8")
                )
            else:
                if not self.rate_limiter.acquire_blocking(max_wait_seconds=15.0):
                    raise LLMTransformationError(
                        "Límite de tasa distribuido alcanzado protegiendo cuota de Gemini API."
                    )
                tx_request = TransformationRequest(
                    task_type=record.task_type,
                    target_language=record.target_language,
                    style_instructions=record.style_instructions,
                )
                tx_output = self.llm_client.transform_document(
                    layout=original_layout,
                    request=tx_request,
                )
                self.store.save_checkpoint(
                    record.session_id,
                    record.job_id,
                    "transformation",
                    tx_output.model_dump_json().encode("utf-8"),
                )
            record.current_stage = JobStage.TRANSFORMED
            record.model_used = tx_output.model_used
            record = self.store.update_job(record)

            # Stage 3: Adaptive Text-Fitting & Dual Reconstruction (.pdf + .docx)
            orig_pdf_overlay = source_bytes if original_layout.source_format == "pdf" else None
            pdf_artifact = reconstruct_document(
                layout=original_layout,
                transformation=tx_output,
                output_format="pdf",
                original_pdf_bytes=orig_pdf_overlay,
            )
            docx_artifact = reconstruct_document(
                layout=original_layout,
                transformation=tx_output,
                output_format="docx",
            )
            self.store.save_artifact(
                session_id=record.session_id,
                job_id=record.job_id,
                artifact_type=ArtifactType.PDF,
                payload=pdf_artifact.output_bytes,
            )
            self.store.save_artifact(
                session_id=record.session_id,
                job_id=record.job_id,
                artifact_type=ArtifactType.DOCX,
                payload=docx_artifact.output_bytes,
            )
            record.current_stage = JobStage.RECONSTRUCTED
            record = self.store.update_job(record)

            # Stage 4: Visual & Geometric Fidelity Verification
            quality_report = verify_document_quality(
                original_layout=original_layout,
                reconstructed_layout=pdf_artifact.reconstructed_layout,
                fitted_results=pdf_artifact.fitted_results_by_block_id,
                original_pdf_bytes=orig_preview_pdf,
                reconstructed_pdf_bytes=pdf_artifact.output_bytes,
            )
            report_bundle = {
                "job_id": record.job_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "filename": record.filename,
                "model_used": tx_output.model_used,
                "quality_report": json.loads(quality_report.to_json_report()),
                "reconstructed_layout": json.loads(
                    pdf_artifact.reconstructed_layout.model_dump_json()
                ),
            }
            self.store.save_artifact(
                session_id=record.session_id,
                job_id=record.job_id,
                artifact_type=ArtifactType.REPORT,
                payload=json.dumps(report_bundle, ensure_ascii=False, indent=2).encode("utf-8"),
            )

            record.state = JobState.COMPLETED
            record.current_stage = JobStage.VERIFIED
            record.traffic_light = quality_report.traffic_light
            record.quality_summary = quality_report.summary
            record.error_message = None
            record = self.store.update_job(record)
            self.queue.ack(msg.receipt_id)
            return record

        except DocumentIngestionError as exc:
            # Deterministic document validation error -> terminal failure without retry storm
            record.state = JobState.FAILED
            record.error_message = str(exc)
            record = self.store.update_job(record)
            self.queue.nack(msg.receipt_id, requeue=False)
            return record

        except LLMTransformationError as exc:
            should_retry = record.attempts < self.max_attempts
            record.state = JobState.QUEUED if should_retry else JobState.FAILED
            record.error_message = str(exc)
            record = self.store.update_job(record)
            self.queue.nack(msg.receipt_id, requeue=should_retry)
            return record

        except Exception:
            should_retry = record.attempts < self.max_attempts
            record.state = JobState.QUEUED if should_retry else JobState.FAILED
            record.error_message = (
                "Error interno controlado en el worker durante el procesamiento documental."
            )
            record = self.store.update_job(record)
            self.queue.nack(msg.receipt_id, requeue=should_retry)
            return record


class WorkerProbeServer:
    """Lightweight HTTP probe server exposing `/healthz` and `/readyz` for K8s worker pods."""

    def __init__(
        self, worker: AsyncDocumentWorker, host: str = "127.0.0.1", port: int = 8081
    ) -> None:
        self.worker = worker
        self.host = host
        self.port = port
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start background probe HTTP server."""
        worker_ref = self.worker

        class _ProbeHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self._respond(200, {"status": "ok", "service": "doclayout-worker"})
                    return
                if self.path == "/readyz":
                    ready = worker_ref.check_readiness() and not worker_ref.is_stopping
                    code = 200 if ready else 503
                    self._respond(code, {"status": "ready" if ready else "draining"})
                    return
                self._respond(404, {"detail": "Not Found"})

            def _respond(self, status_code: int, payload: dict[str, str]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = HTTPServer((self.host, self.port), _ProbeHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down background probe HTTP server."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def main() -> int:
    """CLI entry point for Kubernetes `doclayout-worker` Deployment pods."""
    from doclayout.api.queue import build_job_queue

    settings = get_settings()
    store = EncryptedArtifactStore(
        root_dir=settings.ARTIFACT_STORE_DIR,
        ttl_seconds=settings.ARTIFACT_TTL_SECONDS,
        encryption_secret=settings.ARTIFACT_ENCRYPTION_SECRET.get_secret_value(),
    )
    queue = build_job_queue(settings.REDIS_URL)
    worker = AsyncDocumentWorker(store=store, queue=queue)
    probes = WorkerProbeServer(
        worker=worker,
        host="0.0.0.0",  # nosec B104 - Kubernetes container probe listener inside isolated pod
        port=settings.WORKER_HEALTH_PORT,
    )

    def _handle_shutdown(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        worker.request_stop()

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    probes.start()
    try:
        worker.run_forever()
    finally:
        probes.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

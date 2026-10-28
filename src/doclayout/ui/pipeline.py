"""Four-stage end-to-end document processing orchestrator for UI and local workflows."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from doclayout.ingestion.exceptions import DocumentIngestionError
from doclayout.llm.client import LLMClientProtocol
from doclayout.llm.exceptions import LLMTransformationError
from doclayout.llm.schemas import TransformationRequest
from doclayout.reconstruction import reconstruct_document
from doclayout.ui.session_state import JobHistoryEntry, SessionStore
from doclayout.verification import verify_document_quality

StageCallback = Callable[[int, str], None]


def run_four_stage_pipeline(
    store: SessionStore,
    file_bytes: bytes,
    filename: str,
    request: TransformationRequest,
    llm_client: LLMClientProtocol,
    stage_callback: StageCallback | None = None,
    retry_job_id: str | None = None,
) -> JobHistoryEntry:
    """Execute Ingestion -> Gemini Transformation -> Reconstruction -> Quality Verification."""
    if store.is_processing:
        raise RuntimeError("Ya hay un procesamiento activo en esta sesión. Espere a que finalice.")

    store.is_processing = True
    digest = store.compute_sha256(file_bytes) if file_bytes else ""
    resolved_job_id = retry_job_id or f"job_{uuid4().hex[:10]}"

    def _notify(stage_num: int, label: str) -> None:
        store.current_stage_label = f"Etapa {stage_num}/4: {label}"
        if stage_callback is not None:
            stage_callback(stage_num, store.current_stage_label)

    try:
        # Stage 1: Ingestion & Layout Decomposition
        _notify(1, "Extrayendo geometría, tipografía, tablas y Bounding Boxes...")
        digest, original_layout, orig_preview_pdf = store.get_or_extract_layout(
            file_bytes=file_bytes,
            filename=filename,
        )

        # Stage 2: Semantic Transformation via Gemini (or Mock)
        _notify(2, "Transformando contenido semántico respetando presupuesto espacial...")
        tx_output = llm_client.transform_document(layout=original_layout, request=request)

        # Stage 3: Adaptive Text-Fitting & Dual Format Reconstruction (.pdf + .docx)
        _notify(3, "Aplicando Adaptive Text-Fitting y ensamblando documentos (.pdf y .docx)...")
        orig_pdf_for_overlay = file_bytes if original_layout.source_format == "pdf" else None
        pdf_artifact = reconstruct_document(
            layout=original_layout,
            transformation=tx_output,
            output_format="pdf",
            original_pdf_bytes=orig_pdf_for_overlay,
        )
        docx_artifact = reconstruct_document(
            layout=original_layout,
            transformation=tx_output,
            output_format="docx",
        )

        # Stage 4: Visual & Geometric Fidelity Verification
        _notify(4, "Calculando métricas de fidelidad visual (IoU, colisiones y SSIM estático)...")
        quality_report = verify_document_quality(
            original_layout=original_layout,
            reconstructed_layout=pdf_artifact.reconstructed_layout,
            fitted_results=pdf_artifact.fitted_results_by_block_id,
            original_pdf_bytes=orig_preview_pdf,
            reconstructed_pdf_bytes=pdf_artifact.output_bytes,
        )

        bundle_payload = {
            "job_timestamp": datetime.now(UTC).isoformat(),
            "filename": filename,
            "model_used": tx_output.model_used,
            "quality_report": json.loads(quality_report.to_json_report()),
            "reconstructed_layout": json.loads(pdf_artifact.reconstructed_layout.model_dump_json()),
        }
        export_json_bytes = json.dumps(bundle_payload, ensure_ascii=False, indent=2).encode("utf-8")

        completed_entry = JobHistoryEntry(
            job_id=resolved_job_id,
            filename=filename,
            file_sha256=digest,
            file_bytes=file_bytes,
            task_type=request.task_type,
            target_language=request.target_language,
            style_instructions=request.style_instructions,
            status="completed",
            model_used=tx_output.model_used,
            original_layout=original_layout,
            reconstructed_layout=pdf_artifact.reconstructed_layout,
            fitted_results_by_block_id=pdf_artifact.fitted_results_by_block_id,
            original_preview_pdf_bytes=orig_preview_pdf,
            pdf_output_bytes=pdf_artifact.output_bytes,
            docx_output_bytes=docx_artifact.output_bytes,
            export_json_bytes=export_json_bytes,
            quality_report=quality_report,
        )
        store.active_job = completed_entry
        store.upsert_history(completed_entry)
        return completed_entry

    except (DocumentIngestionError, LLMTransformationError, ValueError) as exc:
        failed_entry = JobHistoryEntry(
            job_id=resolved_job_id,
            filename=filename or "documento_sin_nombre",
            file_sha256=digest,
            file_bytes=file_bytes,
            task_type=request.task_type,
            target_language=request.target_language,
            style_instructions=request.style_instructions,
            status="failed",
            error_message=str(exc),
        )
        store.active_job = failed_entry
        store.upsert_history(failed_entry)
        return failed_entry

    except Exception:
        failed_entry = JobHistoryEntry(
            job_id=resolved_job_id,
            filename=filename or "documento_sin_nombre",
            file_sha256=digest,
            file_bytes=file_bytes,
            task_type=request.task_type,
            target_language=request.target_language,
            style_instructions=request.style_instructions,
            status="failed",
            error_message=(
                "Ocurrió un error interno no esperado durante el procesamiento del documento."
            ),
        )
        store.active_job = failed_entry
        store.upsert_history(failed_entry)
        return failed_entry

    finally:
        store.is_processing = False
        store.current_stage_label = ""

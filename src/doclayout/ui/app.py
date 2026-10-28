"""Streamlit interactive Side-by-Side application for DocLayout-Preserve."""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from doclayout.core.config import get_settings
from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    TableCell,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm import (
    DocumentTransformationOutput,
    GeminiClient,
    LLMClientProtocol,
    TaskType,
    TransformationRequest,
)
from doclayout.ui.pipeline import run_four_stage_pipeline
from doclayout.ui.preview import BLOCK_TYPE_COLORS, render_pdf_page_png
from doclayout.ui.session_state import JobHistoryEntry, SessionStore
from doclayout.verification import TrafficLightStatus

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_TASK_LABELS: dict[str, TaskType] = {
    "Traducir preservando diseño": TaskType.TRANSLATE,
    "Reescribir y mejorar redacción": TaskType.REWRITE,
    "Resumir bloques estructurales": TaskType.SUMMARIZE,
    "Anonimizar datos sensibles (PII)": TaskType.ANONYMIZE,
}

_SEMAPHORE_LABELS: dict[TrafficLightStatus, str] = {
    TrafficLightStatus.GREEN: "🟢 VERDE — Fidelidad Óptima",
    TrafficLightStatus.YELLOW: "🟡 AMARILLO — Advertencias Tipográficas",
    TrafficLightStatus.RED: "🔴 ROJO — Incidentes de Maquetación",
}


class LocalSimulationLLMClient:
    """Zero-quota local simulation client for offline UI inspection and smoke testing."""

    model_name: str = "local-simulation-zero-tokens"

    def transform_document(
        self,
        layout: DocumentLayout,
        request: TransformationRequest,
    ) -> DocumentTransformationOutput:
        """Transform blocks locally without network calls or token consumption."""
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

            new_text = self._simulate_text(blk.text, request)
            if blk.block_type == BlockType.TABLE and blk.cells:
                cells_by_id[blk.block_id] = [
                    TableCell(
                        row_index=c.row_index,
                        col_index=c.col_index,
                        row_span=c.row_span,
                        col_span=c.col_span,
                        text=self._simulate_text(c.text, request) if c.text else "",
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
    def _simulate_text(text: str, request: TransformationRequest) -> str:
        if not text.strip():
            return text
        if request.task_type == TaskType.ANONYMIZE:
            return _EMAIL_RE.sub("[REDACTED]", text)
        if request.task_type == TaskType.SUMMARIZE:
            words = text.split()
            return " ".join(words[: max(1, int(len(words) * 0.75))])
        prefix = f"[{request.target_language[:2].upper()}]"
        return f"{prefix} {text}"


def _get_session_store() -> SessionStore:
    """Retrieve or initialize the isolated SessionStore inside st.session_state."""
    if "doclayout_store" not in st.session_state:
        st.session_state["doclayout_store"] = SessionStore()
    store: SessionStore = st.session_state["doclayout_store"]
    return store


def _render_quality_panel(job: JobHistoryEntry) -> None:
    """Render visual quality metrics semaphore, KPIs, and expandable incident list."""
    report = job.quality_report
    if report is None:
        return

    st.subheader("Control de Calidad Visual y Fidelidad Espacial")
    sem_text = _SEMAPHORE_LABELS.get(report.traffic_light, str(report.traffic_light))
    if report.traffic_light == TrafficLightStatus.GREEN:
        st.success(f"**Estado del Semáforo:** {sem_text}")
    elif report.traffic_light == TrafficLightStatus.YELLOW:
        st.warning(f"**Estado del Semáforo:** {sem_text}")
    else:
        st.error(f"**Estado del Semáforo:** {sem_text}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("IoU Promedio", f"{report.summary.mean_iou:.3f}")
    col2.metric("SSIM Estático", f"{report.summary.ssim_score:.3f}")
    col3.metric("Desbordamientos", str(report.summary.overflow_count))
    col4.metric("Colisiones", str(report.summary.collision_count))

    if report.incidents:
        with st.expander(f"Ver detalle de incidentes visuales ({len(report.incidents)})"):
            for inc in report.incidents:
                st.markdown(
                    f"- **[{inc.severity.value.upper()} | {inc.incident_type.value}]** "
                    f"(Pág. {inc.page_index + 1}): {inc.message}"
                )


def _render_side_by_side_viewer(job: JobHistoryEntry) -> None:
    """Render interactive Side-by-Side visual comparison with Bounding Box overlay toggles."""
    if (
        job.original_layout is None
        or job.reconstructed_layout is None
        or job.original_preview_pdf_bytes is None
        or job.pdf_output_bytes is None
    ):
        return

    st.subheader("Visualizador Comparativo Lado a Lado (Side-by-Side)")

    total_pages = max(1, len(job.reconstructed_layout.pages))
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1, 1, 2])

    with ctrl_col1:
        selected_page = (
            st.number_input(
                "Página a inspeccionar",
                min_value=1,
                max_value=total_pages,
                value=1,
                step=1,
            )
            - 1
        )
    with ctrl_col2:
        show_boxes = st.toggle("Mostrar Bounding Boxes", value=True)
    with ctrl_col3:
        all_types = [bt.value for bt in BlockType]
        default_types = [
            BlockType.HEADING.value,
            BlockType.PARAGRAPH.value,
            BlockType.TABLE.value,
            BlockType.LIST.value,
            BlockType.IMAGE.value,
        ]
        chosen_type_names = st.multiselect(
            "Capas estructurales visibles",
            options=all_types,
            default=default_types,
            disabled=not show_boxes,
        )

    visible_types = {BlockType(t) for t in chosen_type_names} if show_boxes else set()
    overflow_ids = {
        bid for bid, res in job.fitted_results_by_block_id.items() if res.overflow_detected
    }

    if show_boxes:
        legend_items = [
            f"`{bt.value}` RGB{rgb}" for bt, rgb in BLOCK_TYPE_COLORS.items() if bt in visible_types
        ]
        if legend_items:
            st.caption(
                "Leyenda de capas: " + " · ".join(legend_items) + " · `OVERFLOW` RGB(220,53,69)"
            )

    orig_png = render_pdf_page_png(
        pdf_bytes=job.original_preview_pdf_bytes,
        page_index=int(selected_page),
        blocks=job.original_layout.all_blocks,
        visible_types=visible_types,
    )
    rec_png = render_pdf_page_png(
        pdf_bytes=job.pdf_output_bytes,
        page_index=int(selected_page),
        blocks=job.reconstructed_layout.all_blocks,
        visible_types=visible_types,
        overflow_block_ids=overflow_ids,
    )

    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown(f"**Documento Original** — Pág. {int(selected_page) + 1}/{total_pages}")
        st.image(orig_png, use_container_width=True)
    with right_col:
        st.markdown(f"**Documento Reconstruido** — Pág. {int(selected_page) + 1}/{total_pages}")
        st.image(rec_png, use_container_width=True)


def _render_downloads(job: JobHistoryEntry, key_prefix: str = "active") -> None:
    """Render direct download buttons for .pdf, .docx, and .json artifacts."""
    stem = Path(job.filename).stem or "documento"
    dl1, dl2, dl3 = st.columns(3)

    if job.pdf_output_bytes:
        dl1.download_button(
            label="Descargar PDF Reconstruido (.pdf)",
            data=job.pdf_output_bytes,
            file_name=f"{stem}_preservado.pdf",
            mime="application/pdf",
            key=f"{key_prefix}_dl_pdf_{job.job_id}",
            use_container_width=True,
        )
    if job.docx_output_bytes:
        dl2.download_button(
            label="Descargar Word Reconstruido (.docx)",
            data=job.docx_output_bytes,
            file_name=f"{stem}_preservado.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"{key_prefix}_dl_docx_{job.job_id}",
            use_container_width=True,
        )
    if job.export_json_bytes:
        dl3.download_button(
            label="Descargar Layout y Calidad (.json)",
            data=job.export_json_bytes,
            file_name=f"{stem}_layout_report.json",
            mime="application/json",
            key=f"{key_prefix}_dl_json_{job.job_id}",
            use_container_width=True,
        )


def _render_history_section(store: SessionStore, use_simulation: bool) -> None:
    """Render session job history ordered newest-first with view, retry, and download actions."""
    st.divider()
    st.subheader("Historial de Trabajos de la Sesión")
    st.caption(
        "Aviso de retención: Este historial permanece únicamente en la memoria de su sesión "
        "local activa. Recargar el navegador, reiniciar el servidor o pulsar «Limpiar toda la "
        "sesión» eliminará estos datos. Las consultas y descargas desde el historial no realizan "
        "nuevas llamadas a Gemini."
    )

    if not store.history:
        st.info("Aún no hay trabajos registrados en esta sesión.")
        return

    for entry in store.history:
        ts_str = entry.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        status_badge = "✅ Completado" if entry.status == "completed" else "❌ Fallido"
        sem_short = (
            _SEMAPHORE_LABELS[entry.quality_report.traffic_light].split("—")[0].strip()
            if entry.quality_report is not None
            else "N/A"
        )
        header = (
            f"{ts_str} | {entry.filename} | {entry.task_type.value} → {entry.target_language} "
            f"| {status_badge} | {sem_short}"
        )

        with st.expander(header):
            st.markdown(
                f"- **ID de Trabajo:** `{entry.job_id}`  \n"
                f"- **Modelo utilizado:** `{entry.model_used or 'N/A'}`  \n"
                f"- **Instrucciones de estilo:** {entry.style_instructions or '*(ninguna)*'}"
            )
            if entry.status == "failed":
                st.error(f"Motivo del fallo: {entry.error_message}")
                if entry.file_bytes and st.button(
                    "Reintentar este trabajo",
                    key=f"retry_{entry.job_id}",
                    disabled=store.is_processing,
                ):
                    llm_client: LLMClientProtocol = (
                        LocalSimulationLLMClient() if use_simulation else GeminiClient()
                    )
                    req = TransformationRequest(
                        task_type=entry.task_type,
                        target_language=entry.target_language,
                        style_instructions=entry.style_instructions,
                    )
                    run_four_stage_pipeline(
                        store=store,
                        file_bytes=entry.file_bytes,
                        filename=entry.filename,
                        request=req,
                        llm_client=llm_client,
                        retry_job_id=entry.job_id,
                    )
                    st.rerun()
            else:
                if st.button(
                    "Cargar en el visualizador superior",
                    key=f"view_{entry.job_id}",
                    disabled=store.is_processing,
                ):
                    store.active_job = entry.model_copy(deep=True)
                    st.rerun()
                _render_downloads(entry, key_prefix="hist")


def render_app() -> None:
    """Main entry point rendering the full Spanish Streamlit interface."""
    st.set_page_config(
        page_title="DocLayout-Preserve | Preservación de Maquetación",
        layout="wide",
    )

    settings = get_settings()
    store = _get_session_store()
    reset_idx = store.editor_reset_counter

    st.title("DocLayout-Preserve")
    st.markdown(
        "**Motor de Preservación Estricta de Estructura y Maquetación Documental con Gemini API**"
    )

    st.info(
        "**Aviso de Privacidad:** Al procesar un documento con Gemini API, los bloques de texto "
        "extraídos se transmiten de forma cifrada a Google Gemini Developer API para su "
        "transformación semántica. Si activa el *Modo simulación local*, el flujo completo se "
        "ejecuta 100% en su equipo sin enviar datos externos ni consumir cuota."
    )

    if not settings.has_gemini_api_key:
        st.warning(
            "No se detectó una `GEMINI_API_KEY` configurada en `.env` o en el entorno. "
            "Puede inspeccionar y probar las 4 etapas activando el **Modo simulación local**, "
            "o configurar su clave para habilitar llamadas reales a Gemini."
        )

    # Sidebar session management actions
    with st.sidebar:
        st.header("Gestión de Sesión")
        st.caption(
            f"Límites configurados: máx. **{settings.MAX_FILE_SIZE_MB} MB** y "
            f"**{settings.MAX_PAGES} páginas** por documento."
        )
        if st.button("Nuevo Documento", use_container_width=True, disabled=store.is_processing):
            store.start_new_document()
            st.rerun()

        if st.button("Vaciar historial", use_container_width=True, disabled=store.is_processing):
            store.clear_history_only()
            st.rerun()

        if st.button(
            "Limpiar toda la sesión",
            use_container_width=True,
            disabled=store.is_processing,
        ):
            project_tmp = Path(".tmp")
            store.clear_entire_session(tmp_dir=project_tmp)
            st.rerun()

    # Main Input Form / Controls
    st.subheader("1. Carga de Documento y Parámetros de Transformación")
    uploaded_file = st.file_uploader(
        f"Seleccione un documento PDF o Word (.docx) — Máx. {settings.MAX_FILE_SIZE_MB} MB / {settings.MAX_PAGES} páginas",
        type=["pdf", "docx"],
        key=f"uploader_{reset_idx}",
    )

    cfg_col1, cfg_col2, cfg_col3 = st.columns([2, 1, 1])
    with cfg_col1:
        selected_task_label = st.selectbox(
            "Tarea de transformación",
            options=list(_TASK_LABELS.keys()),
            key=f"task_{reset_idx}",
        )
    with cfg_col2:
        target_language = st.selectbox(
            "Idioma destino",
            options=["Español", "Inglés", "Francés", "Portugués", "Alemán", "Italiano"],
            key=f"lang_{reset_idx}",
        )
    with cfg_col3:
        use_simulation = st.toggle(
            "Modo simulación local (0 tokens)",
            value=not settings.has_gemini_api_key,
            key=f"sim_{reset_idx}",
        )

    style_instructions = st.text_area(
        "Instrucciones adicionales de diseño o tono (opcional)",
        placeholder="Ej.: Conservar títulos concisos, registro técnico formal y siglas originales.",
        max_chars=2000,
        key=f"style_{reset_idx}",
    )

    can_process = (
        uploaded_file is not None
        and not store.is_processing
        and (use_simulation or settings.has_gemini_api_key)
    )

    if st.button(
        "Procesar las 4 etapas",
        type="primary",
        disabled=not can_process,
        use_container_width=True,
    ):
        if uploaded_file is not None:
            raw_bytes = uploaded_file.getvalue()
            req = TransformationRequest(
                task_type=_TASK_LABELS[selected_task_label],
                target_language=target_language,
                style_instructions=style_instructions,
            )
            llm_client: LLMClientProtocol = (
                LocalSimulationLLMClient() if use_simulation else GeminiClient(settings=settings)
            )

            progress_bar = st.progress(0, text="Iniciando pipeline de 4 etapas...")

            def _on_stage(stage_num: int, label: str) -> None:
                progress_bar.progress(int(stage_num * 25), text=label)

            run_four_stage_pipeline(
                store=store,
                file_bytes=raw_bytes,
                filename=uploaded_file.name,
                request=req,
                llm_client=llm_client,
                stage_callback=_on_stage,
            )
            progress_bar.progress(100, text="Flujo finalizado.")

    # Active Job Results
    if store.active_job is not None:
        st.divider()
        active = store.active_job
        if active.status == "failed":
            st.error(f"**No se pudo completar el procesamiento:** {active.error_message}")
        else:
            _render_quality_panel(active)
            st.subheader("Descarga de Artefactos Reconstruidos")
            _render_downloads(active, key_prefix="active")
            _render_side_by_side_viewer(active)

    # Session History at bottom
    _render_history_section(store=store, use_simulation=use_simulation)


if __name__ == "__main__":
    render_app()

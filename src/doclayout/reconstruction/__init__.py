"""Typographic reconstruction and adaptive text-fitting package."""

from __future__ import annotations

from pydantic import BaseModel, Field

from doclayout.core.models import DocumentLayout, TransformationResult
from doclayout.llm.schemas import DocumentTransformationOutput
from doclayout.reconstruction.docx_builder import DocxDocumentBuilder
from doclayout.reconstruction.fitting import (
    AdaptiveTextFitter,
    FitMetrics,
    map_pdf_font_name,
    measure_wrapped_height,
)
from doclayout.reconstruction.pdf_builder import PDFDocumentBuilder


class ReconstructionArtifact(BaseModel):
    """Output bundle containing reconstructed binary document and fitted layout state."""

    document_id: str
    output_format: str = Field(..., pattern=r"^(pdf|docx)$")
    output_bytes: bytes
    reconstructed_layout: DocumentLayout
    fitted_results_by_block_id: dict[str, TransformationResult] = Field(default_factory=dict)


def reconstruct_document(
    layout: DocumentLayout,
    transformation: DocumentTransformationOutput,
    output_format: str | None = None,
    original_pdf_bytes: bytes | None = None,
    fitter: AdaptiveTextFitter | None = None,
) -> ReconstructionArtifact:
    """Reconstruct document into target format ('pdf' or 'docx') with adaptive text-fitting."""
    target_fmt = (output_format or layout.source_format).lower().strip(".")
    if target_fmt == "pdf":
        out_bytes, rec_layout, fitted_map = PDFDocumentBuilder(fitter=fitter).build(
            layout=layout,
            transformation=transformation,
            original_pdf_bytes=original_pdf_bytes,
        )
    elif target_fmt == "docx":
        out_bytes, rec_layout, fitted_map = DocxDocumentBuilder(fitter=fitter).build(
            layout=layout,
            transformation=transformation,
        )
    else:
        raise ValueError(
            f"Formato de reconstrucción no soportado ('{target_fmt}'). Use 'pdf' o 'docx'."
        )

    return ReconstructionArtifact(
        document_id=layout.document_id,
        output_format=target_fmt,
        output_bytes=out_bytes,
        reconstructed_layout=rec_layout,
        fitted_results_by_block_id=fitted_map,
    )


__all__ = [
    "AdaptiveTextFitter",
    "DocxDocumentBuilder",
    "FitMetrics",
    "PDFDocumentBuilder",
    "ReconstructionArtifact",
    "map_pdf_font_name",
    "measure_wrapped_height",
    "reconstruct_document",
]

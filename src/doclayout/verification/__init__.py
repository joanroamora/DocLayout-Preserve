"""Visual and geometric layout fidelity verification package."""

from __future__ import annotations

from doclayout.core.models import DocumentLayout, TransformationResult
from doclayout.verification.metrics import (
    IncidentSeverity,
    IncidentType,
    VisualIncident,
    compute_block_iou_scores,
    compute_grayscale_ssim,
    compute_pdf_static_ssim,
    detect_block_collisions,
    detect_margin_and_truncation_issues,
)
from doclayout.verification.reporter import (
    DetailedQualityReport,
    LayoutQualityVerifier,
    TrafficLightStatus,
)


def verify_document_quality(
    original_layout: DocumentLayout,
    reconstructed_layout: DocumentLayout,
    fitted_results: dict[str, TransformationResult] | None = None,
    original_pdf_bytes: bytes | None = None,
    reconstructed_pdf_bytes: bytes | None = None,
) -> DetailedQualityReport:
    """Convenience entry point to verify layout fidelity and produce a DetailedQualityReport."""
    return LayoutQualityVerifier().verify(
        original_layout=original_layout,
        reconstructed_layout=reconstructed_layout,
        fitted_results=fitted_results,
        original_pdf_bytes=original_pdf_bytes,
        reconstructed_pdf_bytes=reconstructed_pdf_bytes,
    )


__all__ = [
    "DetailedQualityReport",
    "IncidentSeverity",
    "IncidentType",
    "LayoutQualityVerifier",
    "TrafficLightStatus",
    "VisualIncident",
    "compute_block_iou_scores",
    "compute_grayscale_ssim",
    "compute_pdf_static_ssim",
    "detect_block_collisions",
    "detect_margin_and_truncation_issues",
    "verify_document_quality",
]

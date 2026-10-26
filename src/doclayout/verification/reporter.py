"""Quality report builder and JSON serializer for layout fidelity verification."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from doclayout.core.models import (
    DocumentLayout,
    QualityReport,
    TransformationResult,
)
from doclayout.verification.metrics import (
    IncidentSeverity,
    IncidentType,
    VisualIncident,
    compute_block_iou_scores,
    compute_pdf_static_ssim,
    detect_block_collisions,
    detect_margin_and_truncation_issues,
)


class TrafficLightStatus(StrEnum):
    """Visual semaphore status exposed to UI dashboards and CI quality gates."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class DetailedQualityReport(BaseModel):
    """Comprehensive verification report combining summary KPIs and structured incidents."""

    summary: QualityReport
    traffic_light: TrafficLightStatus = Field(default=TrafficLightStatus.GREEN)
    per_block_iou: dict[str, float] = Field(default_factory=dict)
    incidents: list[VisualIncident] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_json_report(self, indent: int = 2) -> str:
        """Export the full quality report and incident catalog as formatted JSON."""
        return self.model_dump_json(indent=indent)


class LayoutQualityVerifier:
    """Evaluates spatial IoU, collisions, overflows, margin bleeds, and static SSIM."""

    def __init__(
        self,
        min_iou_threshold: float = 0.85,
        min_ssim_threshold: float = 0.90,
        collision_tolerance_pt: float = 1.0,
        printable_margin_pt: float = 8.0,
    ) -> None:
        self.min_iou_threshold = min_iou_threshold
        self.min_ssim_threshold = min_ssim_threshold
        self.collision_tolerance_pt = collision_tolerance_pt
        self.printable_margin_pt = printable_margin_pt

    def verify(
        self,
        original_layout: DocumentLayout,
        reconstructed_layout: DocumentLayout,
        fitted_results: dict[str, TransformationResult] | None = None,
        original_pdf_bytes: bytes | None = None,
        reconstructed_pdf_bytes: bytes | None = None,
    ) -> DetailedQualityReport:
        """Run all geometric and visual quality checks and assemble a DetailedQualityReport."""
        all_incidents: list[VisualIncident] = []

        mean_iou, per_block_iou, iou_incidents = compute_block_iou_scores(
            original=original_layout,
            reconstructed=reconstructed_layout,
            min_iou_threshold=self.min_iou_threshold,
        )
        all_incidents.extend(iou_incidents)

        collision_count, collision_incidents = detect_block_collisions(
            reconstructed=reconstructed_layout,
            tolerance=self.collision_tolerance_pt,
        )
        all_incidents.extend(collision_incidents)

        overflow_count, margin_incidents = detect_margin_and_truncation_issues(
            original=original_layout,
            reconstructed=reconstructed_layout,
            fitted_results=fitted_results,
            printable_margin_pt=self.printable_margin_pt,
        )
        all_incidents.extend(margin_incidents)

        ssim_score = 1.0
        if original_pdf_bytes and reconstructed_pdf_bytes:
            ssim_score = compute_pdf_static_ssim(
                original_pdf_bytes=original_pdf_bytes,
                reconstructed_pdf_bytes=reconstructed_pdf_bytes,
                text_blocks=reconstructed_layout.all_blocks,
            )
            if ssim_score < self.min_ssim_threshold:
                all_incidents.append(
                    VisualIncident(
                        incident_type=IncidentType.LOW_SSIM,
                        severity=IncidentSeverity.WARNING,
                        page_index=0,
                        message=(
                            f"Similitud estructural estática (SSIM={ssim_score:.3f}) inferior "
                            f"al umbral esperado ({self.min_ssim_threshold:.2f})."
                        ),
                        metric_value=ssim_score,
                    )
                )

        has_critical = any(inc.severity == IncidentSeverity.CRITICAL for inc in all_incidents)
        passed = (
            not has_critical
            and collision_count == 0
            and mean_iou >= self.min_iou_threshold
            and ssim_score >= self.min_ssim_threshold
        )

        if not passed:
            traffic_light = TrafficLightStatus.RED
        elif overflow_count > 0 or len(all_incidents) > 0:
            traffic_light = TrafficLightStatus.YELLOW
        else:
            traffic_light = TrafficLightStatus.GREEN

        summary = QualityReport(
            document_id=reconstructed_layout.document_id,
            mean_iou=mean_iou,
            ssim_score=ssim_score,
            collision_count=collision_count,
            overflow_count=overflow_count,
            total_blocks_evaluated=len(original_layout.all_blocks),
            passed=passed,
            warnings=[inc.message for inc in all_incidents],
        )

        return DetailedQualityReport(
            summary=summary,
            traffic_light=traffic_light,
            per_block_iou=per_block_iou,
            incidents=all_incidents,
        )

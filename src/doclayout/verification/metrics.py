"""Quantitative geometric and visual fidelity metrics (IoU, collisions, margins, SSIM)."""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pymupdf
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    LayoutBlock,
    TransformationResult,
)


class IncidentType(StrEnum):
    """Categories of layout fidelity anomalies."""

    COLLISION = "collision"
    OVERFLOW = "overflow"
    MARGIN_BLEED = "margin_bleed"
    LOW_IOU = "low_iou"
    LOW_SSIM = "low_ssim"
    MISSING_BLOCK = "missing_block"
    EMPTY_TRUNCATION = "empty_truncation"


class IncidentSeverity(StrEnum):
    """Severity level for visual layout incidents."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class VisualIncident(BaseModel):
    """Structured incident record emitted during layout quality verification."""

    incident_type: IncidentType
    severity: IncidentSeverity
    page_index: int = Field(default=0, ge=0)
    block_id: str | None = Field(default=None)
    secondary_block_id: str | None = Field(default=None)
    message: str
    metric_value: float | None = Field(default=None)


def compute_block_iou_scores(
    original: DocumentLayout,
    reconstructed: DocumentLayout,
    min_iou_threshold: float = 0.85,
) -> tuple[float, dict[str, float], list[VisualIncident]]:
    """Calculate per-block and mean IoU between original and reconstructed layouts."""
    orig_blocks = original.all_blocks
    if not orig_blocks:
        return 1.0, {}, []

    rec_by_id: dict[str, LayoutBlock] = {b.block_id: b for b in reconstructed.all_blocks}
    per_block_iou: dict[str, float] = {}
    incidents: list[VisualIncident] = []

    for orig_blk in orig_blocks:
        rec_blk = rec_by_id.get(orig_blk.block_id)
        if rec_blk is None:
            per_block_iou[orig_blk.block_id] = 0.0
            incidents.append(
                VisualIncident(
                    incident_type=IncidentType.MISSING_BLOCK,
                    severity=IncidentSeverity.CRITICAL,
                    page_index=orig_blk.bbox.page_index,
                    block_id=orig_blk.block_id,
                    message=(
                        f"El bloque '{orig_blk.block_id}' del documento original no existe "
                        "en el documento reconstruido."
                    ),
                    metric_value=0.0,
                )
            )
            continue

        score = round(orig_blk.bbox.iou(rec_blk.bbox), 4)
        per_block_iou[orig_blk.block_id] = score
        if score < min_iou_threshold:
            incidents.append(
                VisualIncident(
                    incident_type=IncidentType.LOW_IOU,
                    severity=IncidentSeverity.WARNING,
                    page_index=orig_blk.bbox.page_index,
                    block_id=orig_blk.block_id,
                    message=(
                        f"Desviación geométrica en bloque '{orig_blk.block_id}': "
                        f"IoU={score:.3f} inferior al umbral ({min_iou_threshold:.2f})."
                    ),
                    metric_value=score,
                )
            )

    mean_iou = round(sum(per_block_iou.values()) / len(per_block_iou), 4)
    return mean_iou, per_block_iou, incidents


def detect_block_collisions(
    reconstructed: DocumentLayout,
    tolerance: float = 1.0,
) -> tuple[int, list[VisualIncident]]:
    """Detect spatial overlaps between blocks on the same page."""
    collision_count = 0
    incidents: list[VisualIncident] = []

    for page in reconstructed.pages:
        blocks = page.blocks
        n_blocks = len(blocks)
        for i in range(n_blocks):
            for j in range(i + 1, n_blocks):
                b1 = blocks[i]
                b2 = blocks[j]
                if b1.bbox.collides_with(b2.bbox, tolerance=tolerance):
                    collision_count += 1
                    involves_image = BlockType.IMAGE in {b1.block_type, b2.block_type}
                    severity = (
                        IncidentSeverity.CRITICAL if involves_image else IncidentSeverity.WARNING
                    )
                    overlap_area = round(b1.bbox.intersection_area(b2.bbox), 2)
                    incidents.append(
                        VisualIncident(
                            incident_type=IncidentType.COLLISION,
                            severity=severity,
                            page_index=page.page_index,
                            block_id=b1.block_id,
                            secondary_block_id=b2.block_id,
                            message=(
                                f"Colisión espacial en página {page.page_index + 1} entre "
                                f"'{b1.block_id}' ({b1.block_type}) y '{b2.block_id}' "
                                f"({b2.block_type}) con área solapada de {overlap_area:.1f} pt²."
                            ),
                            metric_value=overlap_area,
                        )
                    )

    return collision_count, incidents


def detect_margin_and_truncation_issues(
    original: DocumentLayout,
    reconstructed: DocumentLayout,
    fitted_results: dict[str, TransformationResult] | None = None,
    printable_margin_pt: float = 8.0,
) -> tuple[int, list[VisualIncident]]:
    """Detect typographic overflows, empty truncations, and printable margin violations."""
    overflow_count = 0
    incidents: list[VisualIncident] = []
    results_map = fitted_results or {}
    orig_by_id = {b.block_id: b for b in original.all_blocks}

    for page in reconstructed.pages:
        for blk in page.blocks:
            res = results_map.get(blk.block_id)
            if res is not None and res.overflow_detected:
                overflow_count += 1
                warn_detail = res.warnings[-1] if res.warnings else "Desbordamiento tipográfico."
                incidents.append(
                    VisualIncident(
                        incident_type=IncidentType.OVERFLOW,
                        severity=IncidentSeverity.WARNING,
                        page_index=page.page_index,
                        block_id=blk.block_id,
                        message=f"Bloque '{blk.block_id}': {warn_detail}",
                        metric_value=res.applied_font_size,
                    )
                )

            orig_blk = orig_by_id.get(blk.block_id)
            if (
                orig_blk is not None
                and orig_blk.block_type != BlockType.IMAGE
                and orig_blk.text.strip()
                and not blk.text.strip()
            ):
                incidents.append(
                    VisualIncident(
                        incident_type=IncidentType.EMPTY_TRUNCATION,
                        severity=IncidentSeverity.CRITICAL,
                        page_index=page.page_index,
                        block_id=blk.block_id,
                        message=(
                            f"Truncamiento total en bloque '{blk.block_id}': el texto original "
                            "quedó vacío tras la reconstrucción."
                        ),
                    )
                )

            # Check printable margin violations introduced or worsened in reconstruction
            exceeds_printable = (
                blk.bbox.x0 < printable_margin_pt
                or blk.bbox.y0 < printable_margin_pt
                or blk.bbox.x1 > (page.width - printable_margin_pt)
                or blk.bbox.y1 > (page.height - printable_margin_pt)
            )
            orig_exceeded = orig_blk is not None and (
                orig_blk.bbox.x0 < printable_margin_pt
                or orig_blk.bbox.y0 < printable_margin_pt
                or orig_blk.bbox.x1 > (page.width - printable_margin_pt)
                or orig_blk.bbox.y1 > (page.height - printable_margin_pt)
            )
            outside_canvas = blk.bbox.x1 > page.width or blk.bbox.y1 > page.height

            if outside_canvas or (exceeds_printable and not orig_exceeded):
                incidents.append(
                    VisualIncident(
                        incident_type=IncidentType.MARGIN_BLEED,
                        severity=(
                            IncidentSeverity.CRITICAL
                            if outside_canvas
                            else IncidentSeverity.WARNING
                        ),
                        page_index=page.page_index,
                        block_id=blk.block_id,
                        message=(
                            f"El bloque '{blk.block_id}' invade los márgenes imprimibles en "
                            f"página {page.page_index + 1} "
                            f"({blk.bbox.x0:.1f}, {blk.bbox.y0:.1f}, "
                            f"{blk.bbox.x1:.1f}, {blk.bbox.y1:.1f})."
                        ),
                    )
                )

    return overflow_count, incidents


def compute_grayscale_ssim(
    img_a: NDArray[np.float64],
    img_b: NDArray[np.float64],
    patch_size: int = 8,
) -> float:
    """Compute mean Structural Similarity Index (SSIM) between two 2D [0..255] arrays."""
    if img_a.shape != img_b.shape:
        min_h = min(img_a.shape[0], img_b.shape[0])
        min_w = min(img_a.shape[1], img_b.shape[1])
        img_a = img_a[:min_h, :min_w]
        img_b = img_b[:min_h, :min_w]

    h, w = img_a.shape
    if h == 0 or w == 0:
        return 1.0

    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2

    ps = max(2, min(patch_size, h, w))
    trim_h = (h // ps) * ps
    trim_w = (w // ps) * ps
    if trim_h == 0 or trim_w == 0:
        return 1.0

    # Reshape into non-overlapping (N_patches, ps * ps) patches for fast vectorized evaluation
    patches_a = (
        img_a[:trim_h, :trim_w]
        .reshape(trim_h // ps, ps, trim_w // ps, ps)
        .transpose(0, 2, 1, 3)
        .reshape(-1, ps * ps)
    )
    patches_b = (
        img_b[:trim_h, :trim_w]
        .reshape(trim_h // ps, ps, trim_w // ps, ps)
        .transpose(0, 2, 1, 3)
        .reshape(-1, ps * ps)
    )

    mu_a = patches_a.mean(axis=1)
    mu_b = patches_b.mean(axis=1)
    var_a = patches_a.var(axis=1)
    var_b = patches_b.var(axis=1)
    cov_ab = ((patches_a - mu_a[:, None]) * (patches_b - mu_b[:, None])).mean(axis=1)

    numerator = (2.0 * mu_a * mu_b + c1) * (2.0 * cov_ab + c2)
    denominator = (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
    ssim_map = numerator / denominator

    score = float(np.clip(ssim_map.mean(), 0.0, 1.0))
    return round(score, 4)


def compute_pdf_static_ssim(
    original_pdf_bytes: bytes,
    reconstructed_pdf_bytes: bytes,
    text_blocks: list[LayoutBlock] | None = None,
    dpi: int = 72,
) -> float:
    """Render PDFs and compute SSIM across static graphics, images, backgrounds, and borders."""
    orig_doc = pymupdf.open(stream=original_pdf_bytes, filetype="pdf")
    rec_doc = pymupdf.open(stream=reconstructed_pdf_bytes, filetype="pdf")

    try:
        page_count = min(len(orig_doc), len(rec_doc))
        if page_count == 0:
            return 1.0

        scale = dpi / 72.0
        blocks_by_page: dict[int, list[LayoutBlock]] = {}
        for blk in text_blocks or []:
            if blk.block_type not in {BlockType.IMAGE, BlockType.TABLE}:
                blocks_by_page.setdefault(blk.bbox.page_index, []).append(blk)
            elif blk.block_type == BlockType.TABLE and blk.cells:
                # Mask inner cell interiors while keeping outer & inner grid lines visible
                for cell in blk.cells:
                    if cell.bbox is not None and cell.bbox.width > 6.0 and cell.bbox.height > 6.0:
                        blocks_by_page.setdefault(blk.bbox.page_index, []).append(
                            LayoutBlock(
                                block_type=BlockType.PARAGRAPH,
                                bbox=cell.bbox,
                                text=cell.text,
                            )
                        )

        page_scores: list[float] = []
        for p_idx in range(page_count):
            pix_orig = orig_doc[p_idx].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            pix_rec = rec_doc[p_idx].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)

            arr_orig = (
                np.frombuffer(pix_orig.samples, dtype=np.uint8)
                .reshape(pix_orig.height, pix_orig.width)
                .astype(np.float64)
            )
            arr_rec = (
                np.frombuffer(pix_rec.samples, dtype=np.uint8)
                .reshape(pix_rec.height, pix_rec.width)
                .astype(np.float64)
            )

            for mask_blk in blocks_by_page.get(p_idx, []):
                # Inset by 2px on table cells or expand by 1px on free text to mask glyphs cleanly
                px0 = max(0, int(mask_blk.bbox.x0 * scale) + 1)
                py0 = max(0, int(mask_blk.bbox.y0 * scale) + 1)
                px1 = min(arr_orig.shape[1], int(mask_blk.bbox.x1 * scale) - 1)
                py1 = min(arr_orig.shape[0], int(mask_blk.bbox.y1 * scale) - 1)
                if px1 > px0 and py1 > py0:
                    arr_orig[py0:py1, px0:px1] = 255.0
                    arr_rec[py0:py1, px0:px1] = 255.0
                    # Also mask full text bounding box for standalone headings/paragraphs
                    if mask_blk.block_id in {b.block_id for b in (text_blocks or [])}:
                        fx0 = max(0, int(mask_blk.bbox.x0 * scale) - 2)
                        fy0 = max(0, int(mask_blk.bbox.y0 * scale) - 2)
                        fx1 = min(arr_orig.shape[1], int(mask_blk.bbox.x1 * scale) + 2)
                        fy1 = min(arr_orig.shape[0], int((mask_blk.bbox.y1 + 6.0) * scale))
                        arr_orig[fy0:fy1, fx0:fx1] = 255.0
                        arr_rec[fy0:fy1, fx0:fx1] = 255.0

            page_scores.append(compute_grayscale_ssim(arr_orig, arr_rec))

        return round(sum(page_scores) / len(page_scores), 4)
    finally:
        orig_doc.close()
        rec_doc.close()

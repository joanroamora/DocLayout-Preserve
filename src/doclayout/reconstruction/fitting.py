"""Adaptive Text-Fitting engine for typographic layout preservation."""

from __future__ import annotations

import pymupdf
from pydantic import BaseModel, Field

from doclayout.core.config import get_settings
from doclayout.core.models import (
    BlockType,
    BoundingBox,
    LayoutBlock,
    TransformationResult,
    TransformationStatus,
)


def map_pdf_font_name(font_family: str) -> str:
    """Map an arbitrary font family string to a PyMuPDF built-in base-14 font code."""
    lower = font_family.lower()
    is_bold = "bold" in lower or "black" in lower or "heavy" in lower
    if "times" in lower or "serif" in lower or "georgia" in lower:
        return "tibo" if is_bold else "tiro"
    if "cour" in lower or "mono" in lower or "consolas" in lower:
        return "cobo" if is_bold else "cour"
    return "hebo" if is_bold else "helv"


class FitMetrics(BaseModel):
    """Calculated typographic parameters after fitting text into a BoundingBox."""

    fitted_font_size: float = Field(..., gt=0.0)
    applied_tracking: float = Field(
        default=0.0,
        description="Character spacing adjustment in points (negative = tighter tracking)",
    )
    applied_line_height: float = Field(default=1.2, gt=0.0)
    estimated_lines: int = Field(default=1, ge=1)
    required_height: float = Field(default=0.0, ge=0.0)
    overflow_detected: bool = Field(default=False)
    warnings: list[str] = Field(default_factory=list)


def measure_wrapped_height(
    text: str,
    box_width: float,
    font_name: str,
    font_size: float,
    line_height: float,
    tracking_em: float = 0.0,
) -> tuple[int, float]:
    """Calculate wrapped line count and vertical height in points inside box_width."""
    usable_width = max(4.0, box_width)
    cleaned = text.strip()
    if not cleaned:
        return 1, round(font_size * line_height, 2)

    tracking_pts = tracking_em * font_size

    def _str_width(segment: str) -> float:
        raw_w = float(pymupdf.get_text_length(segment, fontname=font_name, fontsize=font_size))
        adjusted = raw_w + tracking_pts * max(0, len(segment) - 1)
        return max(1.0, adjusted)

    space_w = max(1.5, _str_width(" "))
    total_lines = 0

    for paragraph in cleaned.splitlines():
        words = paragraph.split()
        if not words:
            total_lines += 1
            continue

        current_line_w = 0.0
        for word in words:
            word_w = _str_width(word)
            if word_w > usable_width:
                # Word itself is wider than the container and must wrap across multiple lines
                if current_line_w > 0.0:
                    total_lines += 1
                    current_line_w = 0.0
                extra_full_lines = int(word_w // usable_width)
                total_lines += extra_full_lines
                remainder = word_w - (extra_full_lines * usable_width)
                current_line_w = remainder if remainder > 0.5 else 0.0
                continue

            candidate_w = word_w if current_line_w == 0.0 else current_line_w + space_w + word_w
            if candidate_w <= usable_width:
                current_line_w = candidate_w
            else:
                total_lines += 1
                current_line_w = word_w

        if current_line_w > 0.0:
            total_lines += 1

    total_lines = max(1, total_lines)
    required_height = round(total_lines * font_size * line_height, 2)
    return total_lines, required_height


class AdaptiveTextFitter:
    """Sequential typographic fitter: 1) micro-tracking, 2) font scaling (>=75%), 3) line-height."""

    def __init__(
        self,
        min_font_scale_ratio: float | None = None,
        max_negative_tracking_em: float = -0.04,
        min_line_height: float = 1.04,
    ) -> None:
        settings = get_settings()
        self.min_font_scale_ratio = (
            min_font_scale_ratio
            if min_font_scale_ratio is not None
            else settings.MIN_FONT_SCALE_RATIO
        )
        self.max_negative_tracking_em = max_negative_tracking_em
        self.min_line_height = min_line_height

    def fit_text_to_box(
        self,
        text: str,
        bbox: BoundingBox,
        base_font_size: float,
        font_family: str = "Helvetica",
        base_line_height: float = 1.2,
    ) -> FitMetrics:
        """Fit text into bbox applying progressive typographic adjustments before alerting overflow."""
        font_code = map_pdf_font_name(font_family)
        clean_base_size = max(4.0, base_font_size)
        clean_line_height = max(1.0, base_line_height)

        # Step 0: Check natural fit with original typography
        lines, req_h = measure_wrapped_height(
            text=text,
            box_width=bbox.width,
            font_name=font_code,
            font_size=clean_base_size,
            line_height=clean_line_height,
            tracking_em=0.0,
        )
        if req_h <= bbox.height:
            return FitMetrics(
                fitted_font_size=round(clean_base_size, 2),
                applied_tracking=0.0,
                applied_line_height=round(clean_line_height, 2),
                estimated_lines=lines,
                required_height=req_h,
                overflow_detected=False,
            )

        # Adjustment 1: Subtle micro-tracking without shrinking font size
        tracking_steps = [-0.015, -0.03, self.max_negative_tracking_em]
        for trk_em in tracking_steps:
            lines, req_h = measure_wrapped_height(
                text=text,
                box_width=bbox.width,
                font_name=font_code,
                font_size=clean_base_size,
                line_height=clean_line_height,
                tracking_em=trk_em,
            )
            if req_h <= bbox.height:
                return FitMetrics(
                    fitted_font_size=round(clean_base_size, 2),
                    applied_tracking=round(trk_em * clean_base_size, 3),
                    applied_line_height=round(clean_line_height, 2),
                    estimated_lines=lines,
                    required_height=req_h,
                    overflow_detected=False,
                )

        # Adjustment 2: Controlled proportional font scaling down to min_font_scale_ratio (75%)
        scale = 0.975
        while scale >= self.min_font_scale_ratio - 1e-6:
            effective_scale = max(self.min_font_scale_ratio, scale)
            candidate_size = clean_base_size * effective_scale
            lines, req_h = measure_wrapped_height(
                text=text,
                box_width=bbox.width,
                font_name=font_code,
                font_size=candidate_size,
                line_height=clean_line_height,
                tracking_em=self.max_negative_tracking_em,
            )
            if req_h <= bbox.height:
                return FitMetrics(
                    fitted_font_size=round(candidate_size, 2),
                    applied_tracking=round(self.max_negative_tracking_em * candidate_size, 3),
                    applied_line_height=round(clean_line_height, 2),
                    estimated_lines=lines,
                    required_height=req_h,
                    overflow_detected=False,
                )
            if effective_scale <= self.min_font_scale_ratio:
                break
            scale -= 0.025

        # Adjustment 3: Proportional line-height compression at minimum allowed font size
        min_font_size = round(clean_base_size * self.min_font_scale_ratio, 2)
        lh_candidates = [1.14, 1.09, self.min_line_height]
        for lh in lh_candidates:
            if lh >= clean_line_height:
                continue
            lines, req_h = measure_wrapped_height(
                text=text,
                box_width=bbox.width,
                font_name=font_code,
                font_size=min_font_size,
                line_height=lh,
                tracking_em=self.max_negative_tracking_em,
            )
            if req_h <= bbox.height:
                return FitMetrics(
                    fitted_font_size=min_font_size,
                    applied_tracking=round(self.max_negative_tracking_em * min_font_size, 3),
                    applied_line_height=round(lh, 2),
                    estimated_lines=lines,
                    required_height=req_h,
                    overflow_detected=False,
                )

        # Exhausted typographic tolerances: emit overflow alert
        final_lh = min(clean_line_height, self.min_line_height)
        lines, req_h = measure_wrapped_height(
            text=text,
            box_width=bbox.width,
            font_name=font_code,
            font_size=min_font_size,
            line_height=final_lh,
            tracking_em=self.max_negative_tracking_em,
        )
        warning_msg = (
            f"Desbordamiento tipográfico detectado: altura requerida ({req_h:.1f} pt) supera "
            f"altura del contenedor ({bbox.height:.1f} pt) incluso al escalar fuente al "
            f"{int(self.min_font_scale_ratio * 100)}% ({min_font_size:.2f} pt)."
        )
        return FitMetrics(
            fitted_font_size=min_font_size,
            applied_tracking=round(self.max_negative_tracking_em * min_font_size, 3),
            applied_line_height=round(final_lh, 2),
            estimated_lines=lines,
            required_height=req_h,
            overflow_detected=True,
            warnings=[warning_msg],
        )

    def apply_to_result(
        self,
        block: LayoutBlock,
        result: TransformationResult,
    ) -> TransformationResult:
        """Fit transformed block text into block.bbox and return enriched TransformationResult."""
        if (
            block.block_type in {BlockType.IMAGE, BlockType.TABLE}
            or not result.transformed_text.strip()
        ):
            return result

        fit = self.fit_text_to_box(
            text=result.transformed_text,
            bbox=block.bbox,
            base_font_size=block.font_size,
            font_family=block.font_family,
            base_line_height=block.line_height,
        )
        merged_warnings = list(result.warnings) + fit.warnings
        status = (
            TransformationStatus.OVERFLOW_WARNING
            if fit.overflow_detected
            else TransformationStatus.COMPLETED
        )
        return TransformationResult(
            block_id=result.block_id,
            original_text=result.original_text,
            transformed_text=result.transformed_text,
            status=status,
            applied_font_size=fit.fitted_font_size,
            applied_tracking=fit.applied_tracking,
            applied_line_height=fit.applied_line_height,
            overflow_detected=fit.overflow_detected,
            warnings=merged_warnings,
        )

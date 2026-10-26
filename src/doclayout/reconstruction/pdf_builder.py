"""PDF layout reconstruction engine using PyMuPDF (pymupdf)."""

from __future__ import annotations

import base64
from typing import Any

import pymupdf

from doclayout.core.models import (
    BlockType,
    BoundingBox,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    TableCell,
    TextAlignment,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm.schemas import DocumentTransformationOutput
from doclayout.reconstruction.fitting import AdaptiveTextFitter, map_pdf_font_name

_ALIGN_MAP: dict[TextAlignment, int] = {
    TextAlignment.LEFT: 0,
    TextAlignment.CENTER: 1,
    TextAlignment.RIGHT: 2,
    TextAlignment.JUSTIFY: 3,
}


def _hex_to_rgb_floats(hex_color: str) -> tuple[float, float, float]:
    """Convert a #RRGGBB color string into PyMuPDF normalized (r, g, b) floats."""
    cleaned = hex_color.lstrip("#")
    if len(cleaned) != 6:
        return (0.0, 0.0, 0.0)
    r = int(cleaned[0:2], 16) / 255.0
    g = int(cleaned[2:4], 16) / 255.0
    b = int(cleaned[4:6], 16) / 255.0
    return (round(r, 4), round(g, 4), round(b, 4))


class PDFDocumentBuilder:
    """Reconstructs transformed PDF documents preserving coordinates, tables, and visuals."""

    def __init__(self, fitter: AdaptiveTextFitter | None = None) -> None:
        self.fitter = fitter or AdaptiveTextFitter()

    def build(
        self,
        layout: DocumentLayout,
        transformation: DocumentTransformationOutput,
        original_pdf_bytes: bytes | None = None,
    ) -> tuple[bytes, DocumentLayout, dict[str, TransformationResult]]:
        """Render transformed blocks onto original PDF template or standalone PDF canvas."""
        overlay_mode = original_pdf_bytes is not None and len(original_pdf_bytes) > 0
        doc = (
            pymupdf.open(stream=original_pdf_bytes, filetype="pdf")
            if overlay_mode
            else pymupdf.open()
        )

        reconstructed_pages: list[PageLayout] = []
        fitted_results: dict[str, TransformationResult] = {}

        try:
            for page_layout in sorted(layout.pages, key=lambda p: p.page_index):
                if overlay_mode and page_layout.page_index < len(doc):
                    pdf_page = doc[page_layout.page_index]
                    self._redact_original_text_regions(pdf_page, page_layout.blocks)
                else:
                    pdf_page = doc.new_page(width=page_layout.width, height=page_layout.height)

                new_blocks: list[LayoutBlock] = []
                for block in page_layout.blocks:
                    base_res = transformation.results_by_block_id.get(
                        block.block_id,
                        TransformationResult(
                            block_id=block.block_id,
                            original_text=block.text,
                            transformed_text=block.text,
                            applied_font_size=block.font_size,
                            applied_line_height=block.line_height,
                        ),
                    )

                    if block.block_type == BlockType.IMAGE:
                        if not overlay_mode and block.image_bytes_b64:
                            img_raw = base64.b64decode(block.image_bytes_b64)
                            pdf_page.insert_image(
                                (block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1),
                                stream=img_raw,
                            )
                        fitted_results[block.block_id] = base_res
                        new_blocks.append(block.model_copy(deep=True))
                        continue

                    if block.block_type == BlockType.TABLE and block.cells:
                        reconstructed_tbl, tbl_res = self._render_table_block(
                            pdf_page=pdf_page,
                            block=block,
                            base_result=base_res,
                            transformed_cells=transformation.transformed_cells_by_block_id.get(
                                block.block_id,
                                block.cells,
                            ),
                            draw_grid=not overlay_mode,
                            page_height=page_layout.height,
                        )
                        fitted_results[block.block_id] = tbl_res
                        new_blocks.append(reconstructed_tbl)
                        continue

                    enriched_res = self.fitter.apply_to_result(block, base_res)
                    self._insert_fitted_textbox(
                        pdf_page=pdf_page,
                        bbox=block.bbox,
                        text=enriched_res.transformed_text,
                        font_family=block.font_family,
                        font_size=enriched_res.applied_font_size,
                        line_height=enriched_res.applied_line_height,
                        color_hex=block.color,
                        alignment=block.alignment,
                        page_height=page_layout.height,
                    )
                    fitted_results[block.block_id] = enriched_res
                    new_blocks.append(
                        block.model_copy(
                            update={
                                "text": enriched_res.transformed_text,
                                "font_size": enriched_res.applied_font_size,
                                "line_height": enriched_res.applied_line_height,
                            },
                            deep=True,
                        )
                    )

                reconstructed_pages.append(
                    PageLayout(
                        page_index=page_layout.page_index,
                        width=page_layout.width,
                        height=page_layout.height,
                        blocks=new_blocks,
                    )
                )

            pdf_bytes = bytes(doc.tobytes(garbage=3, deflate=True))
        finally:
            doc.close()

        reconstructed_doc = DocumentLayout(
            document_id=layout.document_id,
            filename=layout.filename,
            source_format="pdf",
            pages=reconstructed_pages,
            metadata={**layout.metadata, "reconstructed": True},
        )
        return pdf_bytes, reconstructed_doc, fitted_results

    @staticmethod
    def _redact_original_text_regions(pdf_page: Any, blocks: list[LayoutBlock]) -> None:
        """Strip underlying original text glyphs while preserving images and vector graphics."""
        has_redactions = False
        for blk in blocks:
            if blk.block_type == BlockType.IMAGE:
                continue
            rect = (blk.bbox.x0, blk.bbox.y0, blk.bbox.x1, blk.bbox.y1)
            pdf_page.add_redact_annot(rect, fill=False)
            has_redactions = True

        if has_redactions:
            pdf_page.apply_redactions(
                images=int(getattr(pymupdf, "PDF_REDACT_IMAGE_NONE", 0)),
                graphics=int(getattr(pymupdf, "PDF_REDACT_LINE_ART_NONE", 0)),
            )

    def _render_table_block(
        self,
        pdf_page: Any,
        block: LayoutBlock,
        base_result: TransformationResult,
        transformed_cells: list[TableCell],
        draw_grid: bool,
        page_height: float,
    ) -> tuple[LayoutBlock, TransformationResult]:
        """Fit and render every cell inside a table bounding box."""
        row_count = max(
            int(block.metadata.get("row_count") or 0),
            max((c.row_index + 1 for c in transformed_cells), default=1),
        )
        col_count = max(
            int(block.metadata.get("col_count") or 0),
            max((c.col_index + 1 for c in transformed_cells), default=1),
        )
        raw_rel_widths = block.metadata.get("relative_col_widths")
        if isinstance(raw_rel_widths, list) and len(raw_rel_widths) == col_count:
            rel_widths = [float(w) for w in raw_rel_widths]
        else:
            rel_widths = [1.0 / col_count for _ in range(col_count)]

        col_edges: list[float] = [block.bbox.x0]
        acc_x = block.bbox.x0
        for idx, ratio in enumerate(rel_widths):
            if idx == col_count - 1:
                col_edges.append(block.bbox.x1)
            else:
                acc_x += block.bbox.width * ratio
                col_edges.append(acc_x)

        row_h = block.bbox.height / max(1, row_count)
        row_edges = [
            block.bbox.y0 + r * row_h if r < row_count else block.bbox.y1
            for r in range(row_count + 1)
        ]

        if draw_grid:
            for y in row_edges:
                pdf_page.draw_line(
                    (block.bbox.x0, y),
                    (block.bbox.x1, y),
                    color=(0.35, 0.35, 0.35),
                    width=0.6,
                )
            for x in col_edges:
                pdf_page.draw_line(
                    (x, block.bbox.y0),
                    (x, block.bbox.y1),
                    color=(0.35, 0.35, 0.35),
                    width=0.6,
                )

        overflow_any = False
        cell_warnings: list[str] = list(base_result.warnings)
        rendered_cells: list[TableCell] = []
        min_applied_font = block.font_size

        for cell in transformed_cells:
            r_idx = min(cell.row_index, row_count - 1)
            c_idx = min(cell.col_index, col_count - 1)
            r_end = min(row_count, r_idx + cell.row_span)
            c_end = min(col_count, c_idx + cell.col_span)

            cell_bbox = cell.bbox or BoundingBox(
                x0=round(col_edges[c_idx], 2),
                y0=round(row_edges[r_idx], 2),
                x1=round(max(col_edges[c_idx] + 2.0, col_edges[c_end]), 2),
                y1=round(max(row_edges[r_idx] + 2.0, row_edges[r_end]), 2),
                page_index=block.bbox.page_index,
            )

            padded_bbox = BoundingBox(
                x0=cell_bbox.x0 + 2.0 if cell_bbox.width > 6.0 else cell_bbox.x0,
                y0=cell_bbox.y0 + 1.5 if cell_bbox.height > 5.0 else cell_bbox.y0,
                x1=cell_bbox.x1 - 2.0 if cell_bbox.width > 6.0 else cell_bbox.x1,
                y1=cell_bbox.y1 - 1.5 if cell_bbox.height > 5.0 else cell_bbox.y1,
                page_index=cell_bbox.page_index,
            )

            if cell.text.strip():
                fit = self.fitter.fit_text_to_box(
                    text=cell.text,
                    bbox=padded_bbox,
                    base_font_size=min(block.font_size, 10.0),
                    font_family=block.font_family,
                    base_line_height=1.15,
                )
                min_applied_font = min(min_applied_font, fit.fitted_font_size)
                if fit.overflow_detected:
                    overflow_any = True
                    cell_warnings.extend(fit.warnings)

                self._insert_fitted_textbox(
                    pdf_page=pdf_page,
                    bbox=padded_bbox,
                    text=cell.text,
                    font_family=block.font_family,
                    font_size=fit.fitted_font_size,
                    line_height=fit.applied_line_height,
                    color_hex=block.color,
                    alignment=block.alignment,
                    page_height=page_height,
                )

            rendered_cells.append(
                TableCell(
                    row_index=cell.row_index,
                    col_index=cell.col_index,
                    row_span=cell.row_span,
                    col_span=cell.col_span,
                    text=cell.text,
                    bbox=cell_bbox,
                )
            )

        tbl_result = TransformationResult(
            block_id=block.block_id,
            original_text=block.text,
            transformed_text=base_result.transformed_text,
            status=(
                TransformationStatus.OVERFLOW_WARNING
                if overflow_any
                else TransformationStatus.COMPLETED
            ),
            applied_font_size=round(min_applied_font, 2),
            applied_line_height=block.line_height,
            overflow_detected=overflow_any,
            warnings=cell_warnings,
        )
        reconstructed_block = block.model_copy(
            update={
                "text": base_result.transformed_text,
                "cells": rendered_cells,
                "font_size": round(min_applied_font, 2),
            },
            deep=True,
        )
        return reconstructed_block, tbl_result

    @staticmethod
    def _insert_fitted_textbox(
        pdf_page: Any,
        bbox: BoundingBox,
        text: str,
        font_family: str,
        font_size: float,
        line_height: float,
        color_hex: str,
        alignment: TextAlignment,
        page_height: float,
    ) -> None:
        """Insert text into bbox with descender clearance fallback so glyphs are never dropped."""
        if not text.strip():
            return

        font_code = map_pdf_font_name(font_family)
        rgb = _hex_to_rgb_floats(color_hex)
        align_code = _ALIGN_MAP.get(alignment, 0)

        rect = (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
        rc = float(
            pdf_page.insert_textbox(
                rect,
                text,
                fontname=font_code,
                fontsize=font_size,
                lineheight=line_height,
                color=rgb,
                align=align_code,
            )
        )
        if rc < 0:
            # Tight single-line extracted boxes often clip descenders by 2-4pt in PyMuPDF;
            # retry with slight vertical clearance so transformed text is always rendered.
            expanded_y1 = min(page_height, bbox.y1 + max(8.0, font_size * 1.35))
            rc2 = float(
                pdf_page.insert_textbox(
                    (bbox.x0, bbox.y0, bbox.x1, expanded_y1),
                    text,
                    fontname=font_code,
                    fontsize=font_size,
                    lineheight=line_height,
                    color=rgb,
                    align=align_code,
                )
            )
            if rc2 < 0:
                pdf_page.insert_textbox(
                    (bbox.x0, bbox.y0, bbox.x1, page_height),
                    text,
                    fontname=font_code,
                    fontsize=max(6.0, font_size * 0.85),
                    lineheight=1.05,
                    color=rgb,
                    align=align_code,
                )

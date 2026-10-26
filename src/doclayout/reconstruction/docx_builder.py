"""DOCX layout reconstruction engine using python-docx."""

from __future__ import annotations

import base64
import io
from typing import Any

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    TableCell,
    TextAlignment,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm.schemas import DocumentTransformationOutput
from doclayout.reconstruction.fitting import AdaptiveTextFitter

_DOCX_ALIGN_MAP: dict[TextAlignment, WD_ALIGN_PARAGRAPH] = {
    TextAlignment.LEFT: WD_ALIGN_PARAGRAPH.LEFT,
    TextAlignment.CENTER: WD_ALIGN_PARAGRAPH.CENTER,
    TextAlignment.RIGHT: WD_ALIGN_PARAGRAPH.RIGHT,
    TextAlignment.JUSTIFY: WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def _hex_to_rgb_color(hex_color: str) -> RGBColor:
    """Convert a #RRGGBB string into a python-docx RGBColor instance."""
    cleaned = hex_color.lstrip("#")
    if len(cleaned) != 6:
        return RGBColor(0, 0, 0)
    return RGBColor(int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))


class DocxDocumentBuilder:
    """Reconstructs Word (.docx) documents preserving typography, tables, and fitted sizes."""

    def __init__(self, fitter: AdaptiveTextFitter | None = None) -> None:
        self.fitter = fitter or AdaptiveTextFitter()

    def build(
        self,
        layout: DocumentLayout,
        transformation: DocumentTransformationOutput,
    ) -> tuple[bytes, DocumentLayout, dict[str, TransformationResult]]:
        """Assemble a .docx document from layout geometry and transformed blocks."""
        word_doc = docx.Document()
        sorted_pages = sorted(layout.pages, key=lambda p: p.page_index)

        if sorted_pages and word_doc.sections:
            first_page = sorted_pages[0]
            sec = word_doc.sections[0]
            sec.page_width = Pt(first_page.width)
            sec.page_height = Pt(first_page.height)

        reconstructed_pages: list[PageLayout] = []
        fitted_results: dict[str, TransformationResult] = {}

        for p_idx, page_layout in enumerate(sorted_pages):
            if p_idx > 0:
                word_doc.add_page_break()

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
                    if block.image_bytes_b64:
                        img_raw = base64.b64decode(block.image_bytes_b64)
                        word_doc.add_picture(io.BytesIO(img_raw), width=Pt(block.bbox.width))
                    fitted_results[block.block_id] = base_res
                    new_blocks.append(block.model_copy(deep=True))
                    continue

                if block.block_type == BlockType.TABLE and block.cells:
                    reconstructed_tbl, tbl_res = self._append_table_block(
                        word_doc=word_doc,
                        block=block,
                        base_result=base_res,
                        transformed_cells=transformation.transformed_cells_by_block_id.get(
                            block.block_id,
                            block.cells,
                        ),
                    )
                    fitted_results[block.block_id] = tbl_res
                    new_blocks.append(reconstructed_tbl)
                    continue

                enriched_res = self.fitter.apply_to_result(block, base_res)
                self._append_text_block(word_doc, block, enriched_res)
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

        out_stream = io.BytesIO()
        word_doc.save(out_stream)

        reconstructed_doc = DocumentLayout(
            document_id=layout.document_id,
            filename=layout.filename,
            source_format="docx",
            pages=reconstructed_pages,
            metadata={**layout.metadata, "reconstructed": True},
        )
        return out_stream.getvalue(), reconstructed_doc, fitted_results

    @staticmethod
    def _append_text_block(
        word_doc: Any,
        block: LayoutBlock,
        result: TransformationResult,
    ) -> None:
        """Append a styled heading, list item, or paragraph to the Word document."""
        if block.block_type == BlockType.HEADING:
            level = 1 if block.font_size >= 16.0 else 2
            para = word_doc.add_heading(level=level)
            run = para.add_run(result.transformed_text)
            run.bold = True
        else:
            para = word_doc.add_paragraph()
            run = para.add_run(result.transformed_text)

        para.alignment = _DOCX_ALIGN_MAP.get(block.alignment, WD_ALIGN_PARAGRAPH.LEFT)
        para.paragraph_format.line_spacing = result.applied_line_height

        run.font.name = block.font_family
        run.font.size = Pt(result.applied_font_size)
        run.font.color.rgb = _hex_to_rgb_color(block.color)

    def _append_table_block(
        self,
        word_doc: Any,
        block: LayoutBlock,
        base_result: TransformationResult,
        transformed_cells: list[TableCell],
    ) -> tuple[LayoutBlock, TransformationResult]:
        """Append a structured table preserving column proportions and fitted cell text."""
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

        table = word_doc.add_table(rows=row_count, cols=col_count)
        table.style = "Table Grid"

        col_widths_pt = [max(20.0, block.bbox.width * ratio) for ratio in rel_widths]
        for c_idx, w_pt in enumerate(col_widths_pt):
            for cell_obj in table.columns[c_idx].cells:
                cell_obj.width = Pt(w_pt)

        rendered_cells: list[TableCell] = []
        for cell in transformed_cells:
            r_idx = min(cell.row_index, row_count - 1)
            c_idx = min(cell.col_index, col_count - 1)
            target_cell = table.cell(r_idx, c_idx)
            target_cell.text = ""
            para = target_cell.paragraphs[0]
            run = para.add_run(cell.text)
            run.font.name = block.font_family
            run.font.size = Pt(min(block.font_size, 10.5))
            run.font.color.rgb = _hex_to_rgb_color(block.color)
            rendered_cells.append(cell.model_copy(deep=True))

        tbl_result = TransformationResult(
            block_id=block.block_id,
            original_text=block.text,
            transformed_text=base_result.transformed_text,
            status=TransformationStatus.COMPLETED,
            applied_font_size=block.font_size,
            applied_line_height=block.line_height,
            overflow_detected=False,
            warnings=list(base_result.warnings),
        )
        reconstructed_block = block.model_copy(
            update={
                "text": base_result.transformed_text,
                "cells": rendered_cells,
            },
            deep=True,
        )
        return reconstructed_block, tbl_result

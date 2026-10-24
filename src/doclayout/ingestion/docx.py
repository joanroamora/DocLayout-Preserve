"""DOCX layout decomposition engine using python-docx."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from doclayout.core.config import get_settings
from doclayout.core.models import (
    BlockType,
    BoundingBox,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    TableCell,
    TextAlignment,
)
from doclayout.ingestion.exceptions import (
    CorruptedDocumentError,
    DocumentLimitExceededError,
    EmptyDocumentError,
)

_LIST_MARKER_RE = re.compile(r"^\s*([•\-\*\u2022]|\d+[\.\)])\s+")


class DocxLayoutExtractor:
    """Extracts structural blocks, typography, and tables from Word (.docx) files."""

    def __init__(
        self,
        max_file_size_mb: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        settings = get_settings()
        self.max_file_size_mb = max_file_size_mb or settings.MAX_FILE_SIZE_MB
        self.max_pages = max_pages or settings.MAX_PAGES

    def extract_from_path(self, file_path: str | Path) -> DocumentLayout:
        """Read a DOCX file from disk and extract its layout."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise CorruptedDocumentError(f"El archivo DOCX no existe: {path}")
        return self.extract_from_bytes(path.read_bytes(), filename=path.name)

    def extract_from_bytes(
        self,
        docx_bytes: bytes,
        filename: str = "document.docx",
    ) -> DocumentLayout:
        """Validate DOCX bytes and extract ordered paragraphs and tables into pages."""
        if not docx_bytes:
            raise EmptyDocumentError("El archivo DOCX está vacío (0 bytes).")

        max_bytes = self.max_file_size_mb * 1024 * 1024
        if len(docx_bytes) > max_bytes:
            raise DocumentLimitExceededError(
                f"El archivo DOCX ({len(docx_bytes) / (1024 * 1024):.2f} MB) excede el límite "
                f"permitido de {self.max_file_size_mb} MB."
            )

        try:
            doc = docx.Document(io.BytesIO(docx_bytes))
        except Exception as exc:
            raise CorruptedDocumentError(
                f"No fue posible abrir el archivo DOCX '{filename}': archivo corrupto o inválido."
            ) from exc

        page_width, page_height, margin_x, margin_y = self._read_page_geometry(doc)
        usable_width = max(100.0, page_width - 2 * margin_x)

        pages: list[PageLayout] = []
        current_blocks: list[LayoutBlock] = []
        page_idx = 0
        cursor_y = margin_y
        z_counter = 0

        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                para = Paragraph(child, doc)
                text = para.text.strip()
                if not text:
                    continue

                font_family, font_size, color_hex = self._extract_paragraph_style(para)
                block_type = self._classify_paragraph(para, text, font_size)
                alignment = self._map_alignment(para.alignment)

                est_lines = max(
                    1, int(len(text) / max(20, int(usable_width / (font_size * 0.55)))) + 1
                )
                box_height = max(font_size * 1.4, est_lines * font_size * 1.25)

                if cursor_y + box_height > (page_height - margin_y) and current_blocks:
                    pages.append(
                        PageLayout(
                            page_index=page_idx,
                            width=page_width,
                            height=page_height,
                            blocks=current_blocks,
                        )
                    )
                    page_idx += 1
                    if page_idx >= self.max_pages:
                        raise DocumentLimitExceededError(
                            f"El documento DOCX supera el límite de {self.max_pages} páginas."
                        )
                    current_blocks = []
                    cursor_y = margin_y

                bbox = BoundingBox(
                    x0=margin_x,
                    y0=round(cursor_y, 2),
                    x1=round(page_width - margin_x, 2),
                    y1=round(min(page_height, cursor_y + box_height), 2),
                    page_index=page_idx,
                )
                current_blocks.append(
                    LayoutBlock(
                        block_type=block_type,
                        bbox=bbox,
                        text=text,
                        font_family=font_family,
                        font_size=round(font_size, 2),
                        line_height=1.2,
                        color=color_hex,
                        alignment=alignment,
                        z_index=z_counter,
                        metadata={"style_name": para.style.name if para.style else "Normal"},
                    )
                )
                z_counter += 1
                cursor_y += box_height + 8.0

            elif isinstance(child, CT_Tbl):
                table = Table(child, doc)
                tbl_block, tbl_height = self._extract_table_block(
                    table=table,
                    page_idx=page_idx,
                    x0=margin_x,
                    x1=page_width - margin_x,
                    y0=cursor_y,
                    page_height=page_height,
                    z_index=z_counter,
                )
                if tbl_block is not None:
                    current_blocks.append(tbl_block)
                    z_counter += 1
                    cursor_y += tbl_height + 12.0

        if current_blocks:
            pages.append(
                PageLayout(
                    page_index=page_idx,
                    width=page_width,
                    height=page_height,
                    blocks=current_blocks,
                )
            )

        if not pages:
            raise EmptyDocumentError(
                f"El documento DOCX '{filename}' no contiene bloques de texto ni tablas."
            )

        return DocumentLayout(
            filename=filename,
            source_format="docx",
            pages=pages,
            metadata={"page_count": len(pages)},
        )

    @staticmethod
    def _read_page_geometry(doc: Any) -> tuple[float, float, float, float]:
        """Read first section page dimensions and margins in points."""
        page_width = 595.28
        page_height = 841.89
        margin_x = 54.0
        margin_y = 54.0
        if doc.sections:
            sec = doc.sections[0]
            if sec.page_width is not None:
                page_width = float(sec.page_width.pt)
            if sec.page_height is not None:
                page_height = float(sec.page_height.pt)
            if sec.left_margin is not None:
                margin_x = float(sec.left_margin.pt)
            if sec.top_margin is not None:
                margin_y = float(sec.top_margin.pt)
        return page_width, page_height, margin_x, margin_y

    @staticmethod
    def _extract_paragraph_style(para: Paragraph) -> tuple[str, float, str]:
        """Determine dominant font family, size in pt, and hex color from runs or style."""
        font_family = "Calibri"
        font_size = 11.0
        color_hex = "#000000"

        style_name = (para.style.name if para.style else "Normal") or "Normal"
        if "heading 1" in style_name.lower() or "title" in style_name.lower():
            font_size = 18.0
        elif "heading 2" in style_name.lower():
            font_size = 15.0
        elif "heading" in style_name.lower():
            font_size = 13.0

        for run in para.runs:
            if not run.text.strip():
                continue
            if run.font.name:
                font_family = str(run.font.name)
            if run.font.size is not None:
                font_size = max(1.0, float(run.font.size.pt))
            if run.font.color is not None and run.font.color.rgb is not None:
                color_hex = f"#{run.font.color.rgb}"
            break

        return font_family, font_size, color_hex

    @staticmethod
    def _classify_paragraph(para: Paragraph, text: str, font_size: float) -> BlockType:
        """Classify paragraph into HEADING, LIST, or PARAGRAPH."""
        style_name = ((para.style.name if para.style else "") or "").lower()
        if "heading" in style_name or "title" in style_name or font_size >= 14.0:
            return BlockType.HEADING
        if "list" in style_name or _LIST_MARKER_RE.match(text):
            return BlockType.LIST
        return BlockType.PARAGRAPH

    @staticmethod
    def _map_alignment(alignment: Any) -> TextAlignment:
        """Map python-docx paragraph alignment enum to TextAlignment."""
        if alignment == WD_ALIGN_PARAGRAPH.CENTER:
            return TextAlignment.CENTER
        if alignment == WD_ALIGN_PARAGRAPH.RIGHT:
            return TextAlignment.RIGHT
        if alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
            return TextAlignment.JUSTIFY
        return TextAlignment.LEFT

    @staticmethod
    def _extract_table_block(
        table: Table,
        page_idx: int,
        x0: float,
        x1: float,
        y0: float,
        page_height: float,
        z_index: int,
    ) -> tuple[LayoutBlock | None, float]:
        """Extract cells and relative column widths from a python-docx Table."""
        rows = table.rows
        if not rows:
            return None, 0.0

        cells: list[TableCell] = []
        flat_lines: list[str] = []
        col_count = max((len(r.cells) for r in rows), default=0)
        rel_col_widths = (
            [round(1.0 / col_count, 4) for _ in range(col_count)] if col_count > 0 else []
        )

        for r_idx, row in enumerate(rows):
            row_texts: list[str] = []
            for c_idx, cell in enumerate(row.cells):
                cell_str = cell.text.strip()
                row_texts.append(cell_str)
                cells.append(
                    TableCell(
                        row_index=r_idx,
                        col_index=c_idx,
                        text=cell_str,
                    )
                )
            if any(row_texts):
                flat_lines.append(" | ".join(row_texts))

        if not cells:
            return None, 0.0

        table_height = max(24.0, len(rows) * 20.0)
        y1 = min(page_height, max(y0 + 10.0, y0 + table_height))
        bbox = BoundingBox(
            x0=round(x0, 2),
            y0=round(min(y0, y1 - 10.0), 2),
            x1=round(x1, 2),
            y1=round(y1, 2),
            page_index=page_idx,
        )

        return (
            LayoutBlock(
                block_type=BlockType.TABLE,
                bbox=bbox,
                text="\n".join(flat_lines),
                cells=cells,
                z_index=z_index,
                metadata={
                    "row_count": len(rows),
                    "col_count": col_count,
                    "relative_col_widths": rel_col_widths,
                },
            ),
            table_height,
        )

"""PDF layout decomposition engine using PyMuPDF (pymupdf) and pdfplumber."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf

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
    PasswordProtectedDocumentError,
)

_LIST_MARKER_RE = re.compile(r"^\s*([•\-\*\u2022]|\d+[\.\)])\s+")


def _int_rgb_to_hex(color_int: int) -> str:
    """Convert an integer RGB value (0xRRGGBB) from PyMuPDF into a #RRGGBB string."""
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def _clamp_bbox(
    raw_bbox: tuple[float, float, float, float] | list[float],
    page_index: int,
    page_width: float,
    page_height: float,
) -> BoundingBox | None:
    """Sanitize and clamp raw coordinates into a valid BoundingBox or return None."""
    x0 = max(0.0, float(raw_bbox[0]))
    y0 = max(0.0, float(raw_bbox[1]))
    x1 = min(page_width, max(x0 + 1.0, float(raw_bbox[2])))
    y1 = min(page_height, max(y0 + 1.0, float(raw_bbox[3])))
    if x1 <= x0 or y1 <= y0:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, page_index=page_index)


class PDFLayoutExtractor:
    """Extracts geometric layout blocks, typography, tables, and images from PDF files."""

    def __init__(
        self,
        max_file_size_mb: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        settings = get_settings()
        self.max_file_size_mb = max_file_size_mb or settings.MAX_FILE_SIZE_MB
        self.max_pages = max_pages or settings.MAX_PAGES

    def extract_from_path(self, file_path: str | Path) -> DocumentLayout:
        """Read a PDF file from disk and extract its full layout."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise CorruptedDocumentError(f"El archivo PDF no existe: {path}")
        return self.extract_from_bytes(path.read_bytes(), filename=path.name)

    def extract_from_bytes(
        self, pdf_bytes: bytes, filename: str = "document.pdf"
    ) -> DocumentLayout:
        """Validate PDF bytes and extract structural layout per page."""
        if not pdf_bytes:
            raise EmptyDocumentError("El archivo PDF está vacío (0 bytes).")

        max_bytes = self.max_file_size_mb * 1024 * 1024
        if len(pdf_bytes) > max_bytes:
            raise DocumentLimitExceededError(
                f"El archivo PDF ({len(pdf_bytes) / (1024 * 1024):.2f} MB) excede el límite "
                f"permitido de {self.max_file_size_mb} MB."
            )

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise CorruptedDocumentError(
                f"No fue posible abrir el PDF '{filename}': archivo corrupto o inválido."
            ) from exc

        try:
            if doc.needs_pass or doc.is_encrypted:
                raise PasswordProtectedDocumentError(
                    f"El archivo PDF '{filename}' está protegido con contraseña o cifrado."
                )

            page_count = len(doc)
            if page_count == 0:
                raise EmptyDocumentError(f"El archivo PDF '{filename}' no contiene páginas.")

            if page_count > self.max_pages:
                raise DocumentLimitExceededError(
                    f"El archivo PDF tiene {page_count} páginas, superando el máximo "
                    f"configurado de {self.max_pages} páginas."
                )

            pages: list[PageLayout] = []
            total_elements = 0

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumber_pdf:
                for page_idx in range(page_count):
                    fitz_page = doc[page_idx]
                    plumber_page = (
                        plumber_pdf.pages[page_idx] if page_idx < len(plumber_pdf.pages) else None
                    )
                    page_layout = self._extract_page(fitz_page, plumber_page, page_idx)
                    total_elements += len(page_layout.blocks)
                    pages.append(page_layout)

            if total_elements == 0:
                raise EmptyDocumentError(
                    f"El PDF '{filename}' no contiene bloques de texto ni imágenes extraíbles."
                )

            return DocumentLayout(
                filename=filename,
                source_format="pdf",
                pages=pages,
                metadata={"page_count": page_count},
            )
        finally:
            doc.close()

    def _extract_page(
        self,
        fitz_page: Any,
        plumber_page: Any | None,
        page_idx: int,
    ) -> PageLayout:
        """Extract tables, text blocks, and images from a single PDF page."""
        rect = fitz_page.rect
        page_width = float(rect.width) if rect.width > 0 else 595.28
        page_height = float(rect.height) if rect.height > 0 else 841.89

        blocks: list[LayoutBlock] = []
        table_bboxes: list[BoundingBox] = []
        z_counter = 0

        # 1. Extract tables using pdfplumber
        if plumber_page is not None:
            for tbl in plumber_page.find_tables():
                tbl_bbox = _clamp_bbox(tbl.bbox, page_idx, page_width, page_height)
                if tbl_bbox is None:
                    continue
                extracted_rows = tbl.extract() or []
                cells: list[TableCell] = []
                flat_lines: list[str] = []
                col_count = max((len(r) for r in extracted_rows), default=0)
                rel_col_widths = (
                    [round(1.0 / col_count, 4) for _ in range(col_count)] if col_count > 0 else []
                )

                for r_idx, row in enumerate(extracted_rows):
                    row_texts: list[str] = []
                    for c_idx, cell_val in enumerate(row):
                        cell_str = (cell_val or "").strip()
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

                table_bboxes.append(tbl_bbox)
                blocks.append(
                    LayoutBlock(
                        block_type=BlockType.TABLE,
                        bbox=tbl_bbox,
                        text="\n".join(flat_lines),
                        cells=cells,
                        z_index=z_counter,
                        metadata={
                            "row_count": len(extracted_rows),
                            "col_count": col_count,
                            "relative_col_widths": rel_col_widths,
                        },
                    )
                )
                z_counter += 1

        # 2. Extract text and image blocks via PyMuPDF dictionary structure
        page_dict = fitz_page.get_text("dict")
        for raw_block in page_dict.get("blocks", []):
            btype = raw_block.get("type", 0)
            bbox = _clamp_bbox(
                raw_block.get("bbox", (0, 0, 0, 0)), page_idx, page_width, page_height
            )
            if bbox is None:
                continue

            if btype == 0:
                extracted_text_blocks = self._extract_text_blocks_from_raw(
                    raw_block=raw_block,
                    page_idx=page_idx,
                    page_width=page_width,
                    page_height=page_height,
                    table_bboxes=table_bboxes,
                    start_z_index=z_counter,
                )
                blocks.extend(extracted_text_blocks)
                z_counter += len(extracted_text_blocks)

            elif btype == 1:
                img_bytes = raw_block.get("image")
                img_b64 = (
                    base64.b64encode(img_bytes).decode("ascii")
                    if isinstance(img_bytes, (bytes, bytearray))
                    else None
                )
                blocks.append(
                    LayoutBlock(
                        block_type=BlockType.IMAGE,
                        bbox=bbox,
                        text="",
                        image_bytes_b64=img_b64,
                        z_index=z_counter,
                        metadata={
                            "ext": raw_block.get("ext", "png"),
                            "width": raw_block.get("width"),
                            "height": raw_block.get("height"),
                        },
                    )
                )
                z_counter += 1

        # Order blocks top-to-bottom, left-to-right
        blocks.sort(key=lambda b: (round(b.bbox.y0, 1), round(b.bbox.x0, 1)))
        return PageLayout(
            page_index=page_idx,
            width=page_width,
            height=page_height,
            blocks=blocks,
        )

    def _extract_text_blocks_from_raw(
        self,
        raw_block: dict[str, Any],
        page_idx: int,
        page_width: float,
        page_height: float,
        table_bboxes: list[BoundingBox],
        start_z_index: int,
    ) -> list[LayoutBlock]:
        """Split or group lines in a raw PyMuPDF block by typography and semantic role."""
        parsed_lines: list[dict[str, Any]] = []

        for line in raw_block.get("lines", []):
            line_bbox = _clamp_bbox(
                line.get("bbox", raw_block.get("bbox", (0, 0, 0, 0))),
                page_idx,
                page_width,
                page_height,
            )
            if line_bbox is None:
                continue

            # Skip lines that fall predominantly inside an extracted table
            if any(
                line_bbox.intersection_area(tb) / line_bbox.area > 0.6
                for tb in table_bboxes
                if line_bbox.area > 0
            ):
                continue

            span_parts: list[str] = []
            font_family = "Helvetica"
            font_size = 11.0
            color_hex = "#000000"
            max_span_len = -1

            for span in line.get("spans", []):
                span_txt = str(span.get("text", ""))
                if not span_txt:
                    continue
                span_parts.append(span_txt)
                if len(span_txt.strip()) > max_span_len:
                    max_span_len = len(span_txt.strip())
                    font_family = str(span.get("font") or "Helvetica")
                    font_size = max(1.0, float(span.get("size") or 11.0))
                    color_int = int(span.get("color") or 0)
                    color_hex = _int_rgb_to_hex(color_int)

            line_text = "".join(span_parts).strip()
            if not line_text:
                continue

            line_type = self._classify_text_block(
                text=line_text,
                font_size=font_size,
                font_family=font_family,
                bbox=line_bbox,
                page_height=page_height,
            )
            parsed_lines.append(
                {
                    "text": line_text,
                    "bbox": line_bbox,
                    "font_family": font_family,
                    "font_size": font_size,
                    "color_hex": color_hex,
                    "block_type": line_type,
                }
            )

        if not parsed_lines:
            return []

        # Group consecutive lines only if they are paragraphs with matching font size and close Y
        groups: list[list[dict[str, Any]]] = []
        for pline in parsed_lines:
            if not groups:
                groups.append([pline])
                continue
            prev = groups[-1][-1]
            same_paragraph_group = (
                pline["block_type"] == BlockType.PARAGRAPH
                and prev["block_type"] == BlockType.PARAGRAPH
                and abs(pline["font_size"] - prev["font_size"]) < 1.5
                and (pline["bbox"].y0 - prev["bbox"].y1) <= prev["font_size"] * 1.8
            )
            if same_paragraph_group:
                groups[-1].append(pline)
            else:
                groups.append([pline])

        result: list[LayoutBlock] = []
        for idx, grp in enumerate(groups):
            first = grp[0]
            combined_bbox = BoundingBox(
                x0=min(item["bbox"].x0 for item in grp),
                y0=min(item["bbox"].y0 for item in grp),
                x1=max(item["bbox"].x1 for item in grp),
                y1=max(item["bbox"].y1 for item in grp),
                page_index=page_idx,
            )
            result.append(
                LayoutBlock(
                    block_type=first["block_type"],
                    bbox=combined_bbox,
                    text="\n".join(item["text"] for item in grp),
                    font_family=first["font_family"],
                    font_size=round(first["font_size"], 2),
                    line_height=1.2,
                    color=first["color_hex"],
                    alignment=TextAlignment.LEFT,
                    z_index=start_z_index + idx,
                )
            )
        return result

    @staticmethod
    def _classify_text_block(
        text: str,
        font_size: float,
        font_family: str,
        bbox: BoundingBox,
        page_height: float,
    ) -> BlockType:
        """Determine semantic BlockType based on position, typography, and markers."""
        first_line = text.splitlines()[0].strip()
        if bbox.y1 <= page_height * 0.08 and len(text) < 120:
            return BlockType.HEADER
        if bbox.y0 >= page_height * 0.92 and len(text) < 120:
            return BlockType.FOOTER
        if font_size >= 14.0 or ("bold" in font_family.lower() and len(text) < 140):
            return BlockType.HEADING
        if _LIST_MARKER_RE.match(first_line):
            return BlockType.LIST
        return BlockType.PARAGRAPH

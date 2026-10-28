"""Visual page rendering and colored bounding-box overlay engine for Side-by-Side inspection."""

from __future__ import annotations

import io

import pymupdf
from PIL import Image, ImageDraw

from doclayout.core.models import BlockType, DocumentLayout, LayoutBlock
from doclayout.llm.schemas import DocumentTransformationOutput
from doclayout.reconstruction.pdf_builder import PDFDocumentBuilder

BLOCK_TYPE_COLORS: dict[BlockType, tuple[int, int, int]] = {
    BlockType.HEADING: (30, 102, 245),
    BlockType.PARAGRAPH: (40, 167, 69),
    BlockType.TABLE: (253, 126, 20),
    BlockType.LIST: (111, 66, 193),
    BlockType.IMAGE: (23, 162, 184),
    BlockType.FIGURE: (0, 123, 255),
    BlockType.HEADER: (108, 117, 125),
    BlockType.FOOTER: (108, 117, 125),
    BlockType.CAPTION: (214, 51, 132),
}

OVERFLOW_ALERT_COLOR: tuple[int, int, int] = (220, 53, 69)


def build_layout_wireframe_pdf(layout: DocumentLayout) -> bytes:
    """Render a standalone PDF from a DocumentLayout (used to preview DOCX originals)."""
    identity_tx = DocumentTransformationOutput(
        document_id=layout.document_id,
        model_used="preview-wireframe",
    )
    pdf_bytes, _, _ = PDFDocumentBuilder().build(
        layout=layout,
        transformation=identity_tx,
        original_pdf_bytes=None,
    )
    return pdf_bytes


def render_pdf_page_png(
    pdf_bytes: bytes,
    page_index: int = 0,
    blocks: list[LayoutBlock] | None = None,
    visible_types: set[BlockType] | None = None,
    overflow_block_ids: set[str] | None = None,
    dpi: int = 120,
) -> bytes:
    """Render a PDF page to PNG bytes with optional colored bounding-box inspection layers."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if len(doc) == 0:
            blank = Image.new("RGB", (595, 842), color=(255, 255, 255))
            out = io.BytesIO()
            blank.save(out, format="PNG")
            return out.getvalue()

        clamped_idx = max(0, min(page_index, len(doc) - 1))
        page = doc[clamped_idx]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        base_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    finally:
        doc.close()

    if blocks and visible_types:
        scale = dpi / 72.0
        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        overflow_ids = overflow_block_ids or set()

        for blk in blocks:
            if blk.bbox.page_index != clamped_idx:
                continue
            if blk.block_type not in visible_types:
                continue

            is_overflow = blk.block_id in overflow_ids
            rgb = (
                OVERFLOW_ALERT_COLOR
                if is_overflow
                else BLOCK_TYPE_COLORS.get(blk.block_type, (40, 167, 69))
            )

            x0 = max(0, int(blk.bbox.x0 * scale))
            y0 = max(0, int(blk.bbox.y0 * scale))
            x1 = min(base_img.width - 1, max(x0 + 2, int(blk.bbox.x1 * scale)))
            y1 = min(base_img.height - 1, max(y0 + 2, int(blk.bbox.y1 * scale)))

            fill_alpha = 45 if is_overflow else 26
            border_width = 3 if is_overflow else 2

            draw.rectangle(
                [x0, y0, x1, y1], fill=(*rgb, fill_alpha), outline=(*rgb, 235), width=border_width
            )

            badge_label = (
                f"{blk.block_type.value.upper()} [OVERFLOW]"
                if is_overflow
                else blk.block_type.value.upper()
            )
            badge_w = min(base_img.width - x0, max(44, len(badge_label) * 7 + 8))
            badge_y0 = max(0, y0 - 14)
            draw.rectangle([x0, badge_y0, x0 + badge_w, badge_y0 + 14], fill=(*rgb, 225))
            draw.text((x0 + 4, badge_y0 + 1), badge_label, fill=(255, 255, 255, 255))

        base_img = Image.alpha_composite(base_img, overlay)

    out_buf = io.BytesIO()
    base_img.convert("RGB").save(out_buf, format="PNG")
    return out_buf.getvalue()

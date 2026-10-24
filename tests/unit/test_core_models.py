"""Unit tests for core domain models and geometric calculations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from doclayout.core.models import (
    BlockType,
    BoundingBox,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    QualityReport,
    TableCell,
    TextAlignment,
    TransformationResult,
    TransformationStatus,
)


def test_bounding_box_geometry() -> None:
    """Verify width, height, and area calculation for a valid BoundingBox."""
    bbox = BoundingBox(x0=10.0, y0=20.0, x1=110.0, y1=70.0, page_index=0)
    assert bbox.width == 100.0
    assert bbox.height == 50.0
    assert bbox.area == 5000.0


def test_bounding_box_invalid_coordinates() -> None:
    """Verify ValidationError is raised when x1 <= x0 or y1 <= y0."""
    with pytest.raises(ValidationError):
        BoundingBox(x0=100.0, y0=20.0, x1=50.0, y1=70.0)
    with pytest.raises(ValidationError):
        BoundingBox(x0=10.0, y0=80.0, x1=50.0, y1=80.0)


def test_bounding_box_iou_and_collision() -> None:
    """Verify IoU and collision calculation on same and different pages."""
    b1 = BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=100.0, page_index=0)
    b2 = BoundingBox(x0=50.0, y0=50.0, x1=150.0, y1=150.0, page_index=0)
    b_diff_page = BoundingBox(x0=0.0, y0=0.0, x1=100.0, y1=100.0, page_index=1)

    # Intersection is 50x50 = 2500; Union is 10000 + 10000 - 2500 = 17500
    assert b1.intersection_area(b2) == pytest.approx(2500.0)
    assert b1.iou(b2) == pytest.approx(2500.0 / 17500.0)
    assert b1.collides_with(b2) is True

    # Identical box has IoU 1.0
    assert b1.iou(b1) == pytest.approx(1.0)

    # Different page has 0 intersection and no collision
    assert b1.intersection_area(b_diff_page) == 0.0
    assert b1.iou(b_diff_page) == 0.0
    assert b1.collides_with(b_diff_page) is False


def test_document_layout_all_blocks_ordering() -> None:
    """Verify all_blocks returns blocks ordered by page_index."""
    b_page1 = LayoutBlock(
        block_id="blk_p1",
        block_type=BlockType.HEADING,
        bbox=BoundingBox(x0=10.0, y0=10.0, x1=200.0, y1=40.0, page_index=1),
        text="Page 2 Heading",
    )
    b_page0 = LayoutBlock(
        block_id="blk_p0",
        block_type=BlockType.PARAGRAPH,
        bbox=BoundingBox(x0=10.0, y0=10.0, x1=200.0, y1=40.0, page_index=0),
        text="Page 1 Paragraph",
        alignment=TextAlignment.JUSTIFY,
        cells=[TableCell(row_index=0, col_index=0, text="Cell 0,0")],
    )
    doc = DocumentLayout(
        filename="sample.pdf",
        source_format="pdf",
        pages=[
            PageLayout(page_index=1, blocks=[b_page1]),
            PageLayout(page_index=0, blocks=[b_page0]),
        ],
    )
    blocks = doc.all_blocks
    assert [b.block_id for b in blocks] == ["blk_p0", "blk_p1"]


def test_transformation_result_and_quality_report() -> None:
    """Verify TransformationResult and QualityReport model defaults and validation."""
    res = TransformationResult(
        block_id="blk_1",
        original_text="Hello",
        transformed_text="Hola",
        status=TransformationStatus.COMPLETED,
    )
    assert res.overflow_detected is False

    report = QualityReport(
        document_id="doc_123",
        mean_iou=0.98,
        ssim_score=0.99,
        collision_count=0,
        overflow_count=0,
        total_blocks_evaluated=5,
        passed=True,
    )
    assert report.passed is True
    assert report.mean_iou == pytest.approx(0.98)

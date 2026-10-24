"""Core data models and domain contracts for DocLayout-Preserve."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BlockType(StrEnum):
    """Semantic classification of document layout blocks."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    FIGURE = "figure"
    HEADER = "header"
    FOOTER = "footer"
    CAPTION = "caption"


class TextAlignment(StrEnum):
    """Horizontal text alignment inside a layout bounding box."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class TransformationStatus(StrEnum):
    """Status of semantic and typographic transformation for a block or job."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OVERFLOW_WARNING = "overflow_warning"
    FAILED = "failed"


class BoundingBox(BaseModel):
    """Spatial coordinates (in points) of a document element on a specific page."""

    model_config = ConfigDict(frozen=True)

    x0: float = Field(..., ge=0.0, description="Left X coordinate in points")
    y0: float = Field(..., ge=0.0, description="Top Y coordinate in points")
    x1: float = Field(..., gt=0.0, description="Right X coordinate in points")
    y1: float = Field(..., gt=0.0, description="Bottom Y coordinate in points")
    page_index: int = Field(default=0, ge=0, description="Zero-based page index")

    @model_validator(mode="after")
    def validate_coordinates(self) -> BoundingBox:
        """Ensure right/bottom coordinates are strictly greater than left/top."""
        if self.x1 <= self.x0:
            raise ValueError(f"x1 ({self.x1}) must be greater than x0 ({self.x0})")
        if self.y1 <= self.y0:
            raise ValueError(f"y1 ({self.y1}) must be greater than y0 ({self.y0})")
        return self

    @property
    def width(self) -> float:
        """Return horizontal width of the bounding box."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Return vertical height of the bounding box."""
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        """Return geometric area of the bounding box."""
        return self.width * self.height

    def intersection_area(self, other: BoundingBox) -> float:
        """Compute overlapping area with another bounding box on the same page."""
        if self.page_index != other.page_index:
            return 0.0
        inter_x0 = max(self.x0, other.x0)
        inter_y0 = max(self.y0, other.y0)
        inter_x1 = min(self.x1, other.x1)
        inter_y1 = min(self.y1, other.y1)
        if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
            return 0.0
        return (inter_x1 - inter_x0) * (inter_y1 - inter_y0)

    def iou(self, other: BoundingBox) -> float:
        """Compute Intersection over Union (IoU) with another bounding box."""
        inter = self.intersection_area(other)
        if inter == 0.0:
            return 0.0
        union = self.area + other.area - inter
        return inter / union if union > 0.0 else 0.0

    def collides_with(self, other: BoundingBox, tolerance: float = 0.5) -> bool:
        """Return True if two boxes on the same page overlap beyond a small tolerance."""
        if self.page_index != other.page_index:
            return False
        inter_x0 = max(self.x0, other.x0)
        inter_y0 = max(self.y0, other.y0)
        inter_x1 = min(self.x1, other.x1)
        inter_y1 = min(self.y1, other.y1)
        return (inter_x1 - inter_x0) > tolerance and (inter_y1 - inter_y0) > tolerance


class TableCell(BaseModel):
    """Representation of a single cell inside a table block."""

    row_index: int = Field(..., ge=0)
    col_index: int = Field(..., ge=0)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    text: str = Field(default="")
    bbox: BoundingBox | None = None


class LayoutBlock(BaseModel):
    """Extracted structural block preserving geometry and typographic style."""

    block_id: str = Field(default_factory=lambda: f"blk_{uuid4().hex[:10]}")
    block_type: BlockType = Field(default=BlockType.PARAGRAPH)
    bbox: BoundingBox
    text: str = Field(default="")
    font_family: str = Field(default="Helvetica")
    font_size: float = Field(default=11.0, gt=0.0)
    line_height: float = Field(default=1.2, gt=0.0)
    color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    alignment: TextAlignment = Field(default=TextAlignment.LEFT)
    z_index: int = Field(default=0)
    cells: list[TableCell] = Field(default_factory=list)
    image_bytes_b64: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageLayout(BaseModel):
    """Spatial layout representation of a single page."""

    page_index: int = Field(..., ge=0)
    width: float = Field(default=595.28, gt=0.0, description="Page width in points (default A4)")
    height: float = Field(default=841.89, gt=0.0, description="Page height in points (default A4)")
    blocks: list[LayoutBlock] = Field(default_factory=list)


class DocumentLayout(BaseModel):
    """Full document structural decomposition across all pages."""

    document_id: str = Field(default_factory=lambda: f"doc_{uuid4().hex[:12]}")
    filename: str = Field(..., min_length=1)
    source_format: str = Field(default="pdf", pattern=r"^(pdf|docx)$")
    pages: list[PageLayout] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_blocks(self) -> list[LayoutBlock]:
        """Return a flat list of all blocks across all pages in reading order."""
        blocks: list[LayoutBlock] = []
        for page in sorted(self.pages, key=lambda p: p.page_index):
            blocks.extend(page.blocks)
        return blocks


class TransformationResult(BaseModel):
    """Result of transforming and fitting a single layout block."""

    block_id: str = Field(..., min_length=1)
    original_text: str = Field(default="")
    transformed_text: str = Field(default="")
    status: TransformationStatus = Field(default=TransformationStatus.COMPLETED)
    applied_font_size: float = Field(default=11.0, gt=0.0)
    applied_tracking: float = Field(default=0.0)
    applied_line_height: float = Field(default=1.2, gt=0.0)
    overflow_detected: bool = Field(default=False)
    warnings: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    """Quantitative visual and structural fidelity verification report."""

    document_id: str
    mean_iou: float = Field(default=1.0, ge=0.0, le=1.0)
    ssim_score: float = Field(default=1.0, ge=0.0, le=1.0)
    collision_count: int = Field(default=0, ge=0)
    overflow_count: int = Field(default=0, ge=0)
    total_blocks_evaluated: int = Field(default=0, ge=0)
    passed: bool = Field(default=True)
    warnings: list[str] = Field(default_factory=list)

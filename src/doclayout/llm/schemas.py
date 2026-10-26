"""Pydantic contracts and structured output schemas for Gemini transformation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from doclayout.core.models import TableCell, TransformationResult


class TaskType(StrEnum):
    """Supported semantic transformation operations."""

    TRANSLATE = "translate"
    REWRITE = "rewrite"
    SUMMARIZE = "summarize"
    ANONYMIZE = "anonymize"


class TransformationRequest(BaseModel):
    """User parameters controlling semantic transformation and spatial conciseness."""

    task_type: TaskType = Field(default=TaskType.TRANSLATE)
    target_language: str = Field(default="Español", min_length=2)
    style_instructions: str = Field(default="", max_length=2000)
    max_length_expansion_ratio: float = Field(
        default=1.15,
        ge=0.5,
        le=2.0,
        description="Maximum allowed character expansion factor relative to original box capacity",
    )


class TransformedCellPayload(BaseModel):
    """Structured output schema for a single transformed table cell."""

    row_index: int = Field(..., ge=0)
    col_index: int = Field(..., ge=0)
    transformed_text: str = Field(default="")


class TransformedBlockPayload(BaseModel):
    """Structured output schema for a single transformed layout block."""

    block_id: str = Field(..., min_length=1)
    transformed_text: str = Field(default="")
    cells: list[TransformedCellPayload] = Field(default_factory=list)


class BatchTransformationPayload(BaseModel):
    """Top-level JSON Schema enforced via Gemini Structured Outputs."""

    blocks: list[TransformedBlockPayload] = Field(default_factory=list)


class DocumentTransformationOutput(BaseModel):
    """Aggregated transformation output across all blocks of a DocumentLayout."""

    document_id: str
    model_used: str
    results_by_block_id: dict[str, TransformationResult] = Field(default_factory=dict)
    transformed_cells_by_block_id: dict[str, list[TableCell]] = Field(default_factory=dict)
    input_token_count: int = Field(default=0, ge=0)
    output_token_count: int = Field(default=0, ge=0)

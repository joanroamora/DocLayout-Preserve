"""Deterministic offline MockGeminiClient for zero-quota automated testing."""

from __future__ import annotations

import re

from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    TableCell,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm.exceptions import (
    MissingAPIKeyError,
    ModelSafetyBlockedError,
    RateLimitExceededError,
)
from doclayout.llm.schemas import (
    DocumentTransformationOutput,
    TaskType,
    TransformationRequest,
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class MockGeminiClient:
    """Simulated Gemini client that transforms blocks deterministically with zero API cost."""

    def __init__(
        self,
        model_name: str = "mock-gemini-2.5-flash",
        simulate_missing_key: bool = False,
        simulate_rate_limit: bool = False,
        simulate_safety_block: bool = False,
    ) -> None:
        self.model_name = model_name
        self.simulate_missing_key = simulate_missing_key
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_safety_block = simulate_safety_block
        self.call_count = 0

    def transform_document(
        self,
        layout: DocumentLayout,
        request: TransformationRequest,
    ) -> DocumentTransformationOutput:
        """Deterministically transform all blocks and table cells without external calls."""
        self.call_count += 1
        if self.simulate_missing_key:
            raise MissingAPIKeyError("Simulated missing GEMINI_API_KEY in MockGeminiClient.")
        if self.simulate_rate_limit:
            raise RateLimitExceededError("Simulated 429 RESOURCE_EXHAUSTED in MockGeminiClient.")
        if self.simulate_safety_block:
            raise ModelSafetyBlockedError("Simulated safety block in MockGeminiClient.")

        results_by_id: dict[str, TransformationResult] = {}
        cells_by_id: dict[str, list[TableCell]] = {}

        for blk in layout.all_blocks:
            if blk.block_type == BlockType.IMAGE or (not blk.text.strip() and not blk.cells):
                results_by_id[blk.block_id] = TransformationResult(
                    block_id=blk.block_id,
                    original_text=blk.text,
                    transformed_text=blk.text,
                    status=TransformationStatus.COMPLETED,
                    applied_font_size=blk.font_size,
                    applied_line_height=blk.line_height,
                )
                continue

            new_text = self._transform_str(blk.text, request)
            if blk.block_type == BlockType.TABLE and blk.cells:
                new_cells = [
                    TableCell(
                        row_index=c.row_index,
                        col_index=c.col_index,
                        row_span=c.row_span,
                        col_span=c.col_span,
                        text=self._transform_str(c.text, request) if c.text else "",
                        bbox=c.bbox,
                    )
                    for c in blk.cells
                ]
                cells_by_id[blk.block_id] = new_cells

            results_by_id[blk.block_id] = TransformationResult(
                block_id=blk.block_id,
                original_text=blk.text,
                transformed_text=new_text,
                status=TransformationStatus.COMPLETED,
                applied_font_size=blk.font_size,
                applied_line_height=blk.line_height,
            )

        return DocumentTransformationOutput(
            document_id=layout.document_id,
            model_used=self.model_name,
            results_by_block_id=results_by_id,
            transformed_cells_by_block_id=cells_by_id,
            input_token_count=0,
            output_token_count=0,
        )

    @staticmethod
    def _transform_str(text: str, request: TransformationRequest) -> str:
        """Apply deterministic mock transformation preserving approximate spatial length."""
        if not text.strip():
            return text
        if request.task_type == TaskType.ANONYMIZE:
            return _EMAIL_RE.sub("[REDACTED]", text)
        if request.task_type == TaskType.SUMMARIZE:
            words = text.split()
            kept = words[: max(1, int(len(words) * 0.7))]
            return " ".join(kept)
        tag = f"[{request.target_language[:2].upper()}]"
        return f"{tag} {text}"

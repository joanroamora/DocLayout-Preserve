"""Gemini Developer API client using google-genai with structured outputs and backoff."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any, Protocol

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from doclayout.core.config import Settings, get_settings
from doclayout.core.models import (
    BlockType,
    DocumentLayout,
    LayoutBlock,
    TableCell,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm.exceptions import (
    InvalidStructuredOutputError,
    LLMTransformationError,
    MissingAPIKeyError,
    ModelSafetyBlockedError,
    RateLimitExceededError,
)
from doclayout.llm.prompts import (
    build_blocks_user_prompt,
    build_system_instruction,
    estimate_max_chars,
)
from doclayout.llm.schemas import (
    BatchTransformationPayload,
    DocumentTransformationOutput,
    TransformationRequest,
    TransformedBlockPayload,
)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class LLMClientProtocol(Protocol):
    """Protocol implemented by both real GeminiClient and offline MockGeminiClient."""

    def transform_document(
        self,
        layout: DocumentLayout,
        request: TransformationRequest,
    ) -> DocumentTransformationOutput:
        """Transform textual blocks of a DocumentLayout while preserving block_id mappings."""


class GeminiClient:
    """Production client for Gemini Developer API via official google-genai SDK."""

    def __init__(
        self,
        settings: Settings | None = None,
        sdk_client: Any | None = None,
        batch_size: int = 25,
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 15.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._sdk_client = sdk_client
        self.batch_size = max(1, batch_size)
        self.max_retries = max(0, max_retries)
        self.base_delay_seconds = max(0.05, base_delay_seconds)
        self.max_delay_seconds = max(self.base_delay_seconds, max_delay_seconds)
        self._sleep = sleep_fn

    @property
    def model_name(self) -> str:
        """Return configured Gemini model identifier."""
        return self._settings.GEMINI_MODEL

    def _get_sdk_client(self) -> Any:
        """Lazily initialize google.genai.Client after validating GEMINI_API_KEY."""
        if self._sdk_client is not None:
            return self._sdk_client

        if not self._settings.has_gemini_api_key:
            raise MissingAPIKeyError(
                "No se encontró una GEMINI_API_KEY válida configurada en el entorno o .env."
            )

        raw_key = self._settings.GEMINI_API_KEY.get_secret_value().strip()
        self._sdk_client = genai.Client(api_key=raw_key)
        return self._sdk_client

    def _sanitize_message(self, message: str) -> str:
        """Scrub API key from any exception or diagnostic string."""
        raw_key = self._settings.GEMINI_API_KEY.get_secret_value().strip()
        if raw_key and len(raw_key) >= 6 and raw_key in message:
            return message.replace(raw_key, "[REDACTED_API_KEY]")
        return message

    def transform_document(
        self,
        layout: DocumentLayout,
        request: TransformationRequest,
    ) -> DocumentTransformationOutput:
        """Transform all textual blocks in batches using Gemini Structured Outputs."""
        client = self._get_sdk_client()
        results_by_id: dict[str, TransformationResult] = {}
        transformed_cells_by_id: dict[str, list[TableCell]] = {}
        total_in_tokens = 0
        total_out_tokens = 0

        transformable_blocks: list[LayoutBlock] = []
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
            else:
                transformable_blocks.append(blk)

        system_instruction = build_system_instruction(request)

        for start in range(0, len(transformable_blocks), self.batch_size):
            batch = transformable_blocks[start : start + self.batch_size]
            payload, in_tok, out_tok = self._generate_batch_with_retry(
                client=client,
                batch=batch,
                system_instruction=system_instruction,
                request=request,
            )
            total_in_tokens += in_tok
            total_out_tokens += out_tok

            mapped_payloads: dict[str, TransformedBlockPayload] = {
                item.block_id: item for item in payload.blocks
            }

            for blk in batch:
                matched = mapped_payloads.get(blk.block_id)
                if matched is None:
                    raise InvalidStructuredOutputError(
                        f"La respuesta estructurada de Gemini omitió el bloque '{blk.block_id}'."
                    )

                warnings: list[str] = []
                max_chars = estimate_max_chars(blk, request.max_length_expansion_ratio)
                if len(matched.transformed_text) > max_chars:
                    warnings.append(
                        f"Texto transformado ({len(matched.transformed_text)} chars) supera "
                        f"presupuesto sugerido ({max_chars} chars); requerirá ajuste tipográfico."
                    )

                if blk.block_type == BlockType.TABLE and blk.cells:
                    cell_lookup = {
                        (c.row_index, c.col_index): c.transformed_text for c in matched.cells
                    }
                    new_cells: list[TableCell] = []
                    for orig_cell in blk.cells:
                        new_txt = cell_lookup.get(
                            (orig_cell.row_index, orig_cell.col_index),
                            orig_cell.text,
                        )
                        new_cells.append(
                            TableCell(
                                row_index=orig_cell.row_index,
                                col_index=orig_cell.col_index,
                                row_span=orig_cell.row_span,
                                col_span=orig_cell.col_span,
                                text=new_txt,
                                bbox=orig_cell.bbox,
                            )
                        )
                    transformed_cells_by_id[blk.block_id] = new_cells

                results_by_id[blk.block_id] = TransformationResult(
                    block_id=blk.block_id,
                    original_text=blk.text,
                    transformed_text=matched.transformed_text,
                    status=TransformationStatus.COMPLETED,
                    applied_font_size=blk.font_size,
                    applied_line_height=blk.line_height,
                    warnings=warnings,
                )

        return DocumentTransformationOutput(
            document_id=layout.document_id,
            model_used=self.model_name,
            results_by_block_id=results_by_id,
            transformed_cells_by_block_id=transformed_cells_by_id,
            input_token_count=total_in_tokens,
            output_token_count=total_out_tokens,
        )

    def _generate_batch_with_retry(
        self,
        client: Any,
        batch: list[LayoutBlock],
        system_instruction: str,
        request: TransformationRequest,
    ) -> tuple[BatchTransformationPayload, int, int]:
        """Invoke Gemini generate_content with exponential backoff + jitter on transient errors."""
        user_prompt = build_blocks_user_prompt(batch, request)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=BatchTransformationPayload,
        )

        attempt = 0
        while True:
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=config,
                )
                return self._parse_response(response)
            except (ModelSafetyBlockedError, InvalidStructuredOutputError):
                raise
            except errors.APIError as exc:
                status_code = int(getattr(exc, "code", 0) or 0)
                status_str = str(getattr(exc, "status", "") or "").upper()
                is_rate_limit = status_code == 429 or "RESOURCE_EXHAUSTED" in status_str
                is_retryable = is_rate_limit or status_code in _RETRYABLE_STATUS_CODES

                if not is_retryable:
                    safe_msg = self._sanitize_message(str(exc))
                    raise LLMTransformationError(
                        f"Error no recuperable en Gemini API (código {status_code}): {safe_msg}"
                    ) from exc

                if attempt >= self.max_retries:
                    if is_rate_limit:
                        raise RateLimitExceededError(
                            "Se alcanzó el límite de cuota o tasa de Gemini API (429 / "
                            f"RESOURCE_EXHAUSTED) tras {attempt + 1} intentos."
                        ) from exc
                    safe_msg = self._sanitize_message(str(exc))
                    raise LLMTransformationError(
                        f"Error temporal persistente en Gemini API tras {attempt + 1} intentos: "
                        f"{safe_msg}"
                    ) from exc

                delay = self._compute_backoff_delay(attempt)
                self._sleep(delay)
                attempt += 1
            except TimeoutError as exc:
                if attempt >= self.max_retries:
                    raise LLMTransformationError(
                        f"Tiempo de espera agotado consultando Gemini API tras {attempt + 1} intentos."
                    ) from exc
                delay = self._compute_backoff_delay(attempt)
                self._sleep(delay)
                attempt += 1
            except Exception as exc:
                safe_msg = self._sanitize_message(str(exc))
                raise LLMTransformationError(
                    f"Fallo inesperado comunicándose con Gemini API: {safe_msg}"
                ) from exc

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Compute bounded exponential backoff with jitter."""
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * (2**attempt))
        return round(random.uniform(ceiling * 0.5, ceiling), 4)

    @staticmethod
    def _parse_response(response: Any) -> tuple[BatchTransformationPayload, int, int]:
        """Validate structured output text and extract token usage metadata."""
        raw_text = getattr(response, "text", None)
        if not raw_text or not str(raw_text).strip():
            raise ModelSafetyBlockedError(
                "Gemini devolvió una respuesta vacía o bloqueada por políticas de seguridad."
            )

        try:
            parsed = BatchTransformationPayload.model_validate_json(str(raw_text))
        except ValidationError as exc:
            raise InvalidStructuredOutputError(
                "El JSON devuelto por Gemini no cumple el esquema estructurado esperado."
            ) from exc

        usage = getattr(response, "usage_metadata", None)
        in_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        out_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        return parsed, in_tokens, out_tokens

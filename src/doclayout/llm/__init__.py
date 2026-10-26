"""Gemini API integration and structured prompt orchestration package."""

from doclayout.llm.client import GeminiClient, LLMClientProtocol
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
    TaskType,
    TransformationRequest,
    TransformedBlockPayload,
    TransformedCellPayload,
)

__all__ = [
    "BatchTransformationPayload",
    "DocumentTransformationOutput",
    "GeminiClient",
    "InvalidStructuredOutputError",
    "LLMClientProtocol",
    "LLMTransformationError",
    "MissingAPIKeyError",
    "ModelSafetyBlockedError",
    "RateLimitExceededError",
    "TaskType",
    "TransformationRequest",
    "TransformedBlockPayload",
    "TransformedCellPayload",
    "build_blocks_user_prompt",
    "build_system_instruction",
    "estimate_max_chars",
]

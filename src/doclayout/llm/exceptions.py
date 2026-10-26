"""Custom exceptions for Gemini LLM integration and semantic transformation."""

from __future__ import annotations


class LLMTransformationError(Exception):
    """Base exception for errors during semantic block transformation with Gemini."""


class MissingAPIKeyError(LLMTransformationError):
    """Raised when GEMINI_API_KEY is missing, empty, or set to a placeholder."""


class RateLimitExceededError(LLMTransformationError):
    """Raised when API rate limits (429 / RESOURCE_EXHAUSTED) persist after retries."""


class ModelSafetyBlockedError(LLMTransformationError):
    """Raised when Gemini returns an empty or blocked response due to safety filters."""


class InvalidStructuredOutputError(LLMTransformationError):
    """Raised when the structured JSON response from Gemini fails schema validation."""

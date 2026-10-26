"""Unit tests for GeminiClient, structured prompts, backoff retries, and MockGeminiClient."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors
from pydantic import SecretStr

from doclayout.core.config import Settings
from doclayout.core.models import (
    BlockType,
    BoundingBox,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    TableCell,
)
from doclayout.llm import (
    BatchTransformationPayload,
    GeminiClient,
    InvalidStructuredOutputError,
    LLMTransformationError,
    MissingAPIKeyError,
    ModelSafetyBlockedError,
    RateLimitExceededError,
    TaskType,
    TransformationRequest,
    TransformedBlockPayload,
    TransformedCellPayload,
    build_blocks_user_prompt,
    build_system_instruction,
    estimate_max_chars,
)
from tests.mocks import MockGeminiClient


def _sample_layout() -> DocumentLayout:
    """Create a multi-block layout with paragraph, table, and image blocks."""
    p_block = LayoutBlock(
        block_id="blk_para",
        block_type=BlockType.PARAGRAPH,
        bbox=BoundingBox(x0=50.0, y0=80.0, x1=450.0, y1=140.0, page_index=0),
        text="Contact support@example.com for architecture details.",
        font_size=11.0,
    )
    t_block = LayoutBlock(
        block_id="blk_tbl",
        block_type=BlockType.TABLE,
        bbox=BoundingBox(x0=50.0, y0=160.0, x1=450.0, y1=220.0, page_index=0),
        text="Key | Value",
        cells=[
            TableCell(row_index=0, col_index=0, text="Key"),
            TableCell(row_index=0, col_index=1, text="Value"),
        ],
    )
    img_block = LayoutBlock(
        block_id="blk_img",
        block_type=BlockType.IMAGE,
        bbox=BoundingBox(x0=50.0, y0=250.0, x1=200.0, y1=350.0, page_index=0),
        text="",
    )
    return DocumentLayout(
        document_id="doc_test_llm",
        filename="architecture.pdf",
        source_format="pdf",
        pages=[PageLayout(page_index=0, blocks=[p_block, t_block, img_block])],
    )


class _FakeModelsEndpoint:
    """Controllable fake for google.genai.Client().models."""

    def __init__(self, side_effects: list[Any]) -> None:
        self._side_effects = list(side_effects)
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        effect = self._side_effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


class _FakeSDKClient:
    """Minimal wrapper exposing .models endpoint."""

    def __init__(self, side_effects: list[Any]) -> None:
        self.models = _FakeModelsEndpoint(side_effects)


def test_prompts_untrusted_delimiters_and_max_chars() -> None:
    """Verify untrusted XML delimiters, system guardrails, and max_chars calculation."""
    layout = _sample_layout()
    req = TransformationRequest(
        task_type=TaskType.TRANSLATE,
        target_language="Inglés",
        style_instructions="Mantener tono técnico formal",
    )

    sys_inst = build_system_instruction(req)
    assert "UNTRUSTED_DOCUMENT_BLOCKS_JSON" in sys_inst
    assert "Inglés" in sys_inst
    assert "Mantener tono técnico formal" in sys_inst

    blocks_to_send = [b for b in layout.all_blocks if b.block_type != BlockType.IMAGE]
    user_prompt = build_blocks_user_prompt(blocks_to_send, req)
    assert user_prompt.count("<UNTRUSTED_DOCUMENT_BLOCKS_JSON>") == 1
    assert user_prompt.count("</UNTRUSTED_DOCUMENT_BLOCKS_JSON>") == 1
    assert '"block_id": "blk_para"' in user_prompt
    assert '"max_chars":' in user_prompt
    assert estimate_max_chars(blocks_to_send[0]) >= len(blocks_to_send[0].text)


def test_gemini_client_missing_api_key_raises_error() -> None:
    """Verify MissingAPIKeyError is raised before any network call when key is absent."""
    settings = Settings(GEMINI_API_KEY=SecretStr(""))
    client = GeminiClient(settings=settings)
    with pytest.raises(MissingAPIKeyError):
        client.transform_document(_sample_layout(), TransformationRequest())


def test_gemini_client_structured_transformation_and_image_skip() -> None:
    """Verify structured response mapping for paragraphs, table cells, and skipped images."""
    payload = BatchTransformationPayload(
        blocks=[
            TransformedBlockPayload(
                block_id="blk_para",
                transformed_text="Contacte a support@example.com para detalles.",
            ),
            TransformedBlockPayload(
                block_id="blk_tbl",
                transformed_text="Clave | Valor",
                cells=[
                    TransformedCellPayload(row_index=0, col_index=0, transformed_text="Clave"),
                    TransformedCellPayload(row_index=0, col_index=1, transformed_text="Valor"),
                ],
            ),
        ]
    )
    fake_resp = SimpleNamespace(
        text=payload.model_dump_json(),
        usage_metadata=SimpleNamespace(prompt_token_count=120, candidates_token_count=65),
    )
    fake_sdk = _FakeSDKClient([fake_resp])
    settings = Settings(
        GEMINI_API_KEY=SecretStr("valid-secret-key-xyz"), GEMINI_MODEL="gemini-2.5-flash"
    )
    client = GeminiClient(settings=settings, sdk_client=fake_sdk)

    out = client.transform_document(_sample_layout(), TransformationRequest())

    assert len(fake_sdk.models.calls) == 1
    assert "blk_img" not in fake_sdk.models.calls[0]["contents"]
    assert out.input_token_count == 120
    assert out.output_token_count == 65
    assert out.results_by_block_id["blk_para"].transformed_text.startswith("Contacte a")
    assert [c.text for c in out.transformed_cells_by_block_id["blk_tbl"]] == ["Clave", "Valor"]
    assert out.results_by_block_id["blk_img"].transformed_text == ""


def test_gemini_client_exponential_backoff_on_429_then_success() -> None:
    """Verify transient 429 RESOURCE_EXHAUSTED retries with jitter and succeeds."""
    rate_err = errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota limit"}},
    )
    payload = BatchTransformationPayload(
        blocks=[
            TransformedBlockPayload(block_id="blk_para", transformed_text="Translated para"),
            TransformedBlockPayload(
                block_id="blk_tbl",
                transformed_text="K | V",
                cells=[
                    TransformedCellPayload(row_index=0, col_index=0, transformed_text="K"),
                    TransformedCellPayload(row_index=0, col_index=1, transformed_text="V"),
                ],
            ),
        ]
    )
    fake_resp = SimpleNamespace(text=payload.model_dump_json(), usage_metadata=None)
    fake_sdk = _FakeSDKClient([rate_err, rate_err, fake_resp])

    recorded_sleeps: list[float] = []
    settings = Settings(GEMINI_API_KEY=SecretStr("secret-12345678"))
    client = GeminiClient(
        settings=settings,
        sdk_client=fake_sdk,
        max_retries=3,
        base_delay_seconds=0.5,
        sleep_fn=recorded_sleeps.append,
    )

    out = client.transform_document(_sample_layout(), TransformationRequest())
    assert out.results_by_block_id["blk_para"].transformed_text == "Translated para"
    assert len(recorded_sleeps) == 2
    assert all(delay > 0 for delay in recorded_sleeps)


def test_gemini_client_exhausted_retries_and_secret_scrubbing() -> None:
    """Verify persistent 429 raises RateLimitExceededError and 401 scrubs API key without retry."""
    rate_err = errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota limit"}},
    )
    sleeps: list[float] = []
    settings = Settings(GEMINI_API_KEY=SecretStr("MY_SUPER_SECRET_KEY_999"))

    exhausted_client = GeminiClient(
        settings=settings,
        sdk_client=_FakeSDKClient([rate_err, rate_err]),
        max_retries=1,
        sleep_fn=sleeps.append,
    )
    with pytest.raises(RateLimitExceededError):
        exhausted_client.transform_document(_sample_layout(), TransformationRequest())
    assert len(sleeps) == 1

    # Non-retryable 401 containing secret key in error details must fail immediately and scrub key
    auth_err = errors.ClientError(
        401,
        {
            "error": {
                "code": 401,
                "status": "UNAUTHENTICATED",
                "message": "Invalid key MY_SUPER_SECRET_KEY_999 provided",
            }
        },
    )
    no_retry_sleeps: list[float] = []
    auth_client = GeminiClient(
        settings=settings,
        sdk_client=_FakeSDKClient([auth_err]),
        max_retries=3,
        sleep_fn=no_retry_sleeps.append,
    )
    with pytest.raises(LLMTransformationError) as exc_info:
        auth_client.transform_document(_sample_layout(), TransformationRequest())

    assert len(no_retry_sleeps) == 0
    assert "MY_SUPER_SECRET_KEY_999" not in str(exc_info.value)
    assert "[REDACTED_API_KEY]" in str(exc_info.value)


def test_gemini_client_safety_block_and_invalid_json() -> None:
    """Verify empty response raises ModelSafetyBlockedError and bad JSON raises InvalidStructuredOutputError."""
    settings = Settings(GEMINI_API_KEY=SecretStr("secret-key-abc"))

    blocked_client = GeminiClient(
        settings=settings,
        sdk_client=_FakeSDKClient([SimpleNamespace(text="")]),
    )
    with pytest.raises(ModelSafetyBlockedError):
        blocked_client.transform_document(_sample_layout(), TransformationRequest())

    invalid_json_client = GeminiClient(
        settings=settings,
        sdk_client=_FakeSDKClient([SimpleNamespace(text="{not-valid-json")]),
    )
    with pytest.raises(InvalidStructuredOutputError):
        invalid_json_client.transform_document(_sample_layout(), TransformationRequest())


def test_mock_gemini_client_offline_modes() -> None:
    """Verify MockGeminiClient transforms paragraphs, tables, and anonymizes emails with 0 quota."""
    mock_client = MockGeminiClient()
    layout = _sample_layout()

    anon_out = mock_client.transform_document(
        layout,
        TransformationRequest(task_type=TaskType.ANONYMIZE),
    )
    assert "[REDACTED]" in anon_out.results_by_block_id["blk_para"].transformed_text
    assert "support@example.com" not in anon_out.results_by_block_id["blk_para"].transformed_text
    assert mock_client.call_count == 1
    assert anon_out.input_token_count == 0

"""Unit tests for visual and geometric layout fidelity verification (IoU, SSIM, collisions)."""

from __future__ import annotations

import json

import numpy as np
import pymupdf
import pytest

from doclayout.core.models import (
    BlockType,
    BoundingBox,
    DocumentLayout,
    LayoutBlock,
    PageLayout,
    TransformationResult,
    TransformationStatus,
)
from doclayout.llm import TaskType, TransformationRequest
from doclayout.reconstruction import reconstruct_document
from doclayout.verification import (
    IncidentSeverity,
    IncidentType,
    LayoutQualityVerifier,
    TrafficLightStatus,
    compute_grayscale_ssim,
    verify_document_quality,
)
from tests.mocks import MockGeminiClient


def test_end_to_end_clean_verification_green_traffic_light() -> None:
    """Verify full pipeline with spacious boxes yields IoU=1.0, high SSIM, and GREEN semaphore."""
    src_doc = pymupdf.open()
    p = src_doc.new_page(width=595, height=842)
    # Static graphic banner at top
    p.draw_rect((50, 30, 545, 65), color=(0.1, 0.2, 0.5), fill=(0.1, 0.2, 0.5))
    p.insert_textbox((50, 100, 500, 160), "Short title", fontsize=14)
    p.insert_textbox(
        (50, 180, 500, 280), "Concise paragraph inside spacious container.", fontsize=11
    )
    orig_bytes = bytes(src_doc.tobytes())
    src_doc.close()

    layout = DocumentLayout(
        document_id="doc_clean",
        filename="clean.pdf",
        source_format="pdf",
        pages=[
            PageLayout(
                page_index=0,
                width=595.0,
                height=842.0,
                blocks=[
                    LayoutBlock(
                        block_id="blk_1",
                        block_type=BlockType.HEADING,
                        bbox=BoundingBox(x0=50.0, y0=100.0, x1=500.0, y1=160.0, page_index=0),
                        text="Short title",
                        font_size=14.0,
                    ),
                    LayoutBlock(
                        block_id="blk_2",
                        block_type=BlockType.PARAGRAPH,
                        bbox=BoundingBox(x0=50.0, y0=180.0, x1=500.0, y1=280.0, page_index=0),
                        text="Concise paragraph inside spacious container.",
                        font_size=11.0,
                    ),
                ],
            )
        ],
    )

    tx_out = MockGeminiClient().transform_document(
        layout,
        TransformationRequest(task_type=TaskType.TRANSLATE, target_language="Español"),
    )
    artifact = reconstruct_document(
        layout=layout,
        transformation=tx_out,
        output_format="pdf",
        original_pdf_bytes=orig_bytes,
    )

    report = verify_document_quality(
        original_layout=layout,
        reconstructed_layout=artifact.reconstructed_layout,
        fitted_results=artifact.fitted_results_by_block_id,
        original_pdf_bytes=orig_bytes,
        reconstructed_pdf_bytes=artifact.output_bytes,
    )

    assert report.summary.passed is True
    assert report.traffic_light == TrafficLightStatus.GREEN
    assert report.summary.mean_iou == pytest.approx(1.0)
    assert report.summary.ssim_score >= 0.98
    assert report.summary.collision_count == 0
    assert report.summary.overflow_count == 0

    parsed_json = json.loads(report.to_json_report())
    assert parsed_json["traffic_light"] == "green"
    assert parsed_json["summary"]["mean_iou"] == 1.0


def test_collision_low_iou_and_missing_block_detection() -> None:
    """Verify detection of text-image collisions, displaced low-IoU boxes, and missing blocks."""
    orig = DocumentLayout(
        document_id="doc_anom",
        filename="anom.pdf",
        pages=[
            PageLayout(
                page_index=0,
                blocks=[
                    LayoutBlock(
                        block_id="blk_txt",
                        block_type=BlockType.PARAGRAPH,
                        bbox=BoundingBox(x0=50.0, y0=50.0, x1=250.0, y1=120.0, page_index=0),
                        text="Paragraph",
                    ),
                    LayoutBlock(
                        block_id="blk_img",
                        block_type=BlockType.IMAGE,
                        bbox=BoundingBox(x0=180.0, y0=80.0, x1=350.0, y1=220.0, page_index=0),
                    ),
                    LayoutBlock(
                        block_id="blk_dropped",
                        block_type=BlockType.HEADING,
                        bbox=BoundingBox(x0=50.0, y0=250.0, x1=250.0, y1=280.0, page_index=0),
                        text="Dropped heading",
                    ),
                ],
            )
        ],
    )

    # Reconstructed shifts blk_txt to collide with blk_img and drops blk_dropped
    rec = DocumentLayout(
        document_id="doc_anom",
        filename="anom.pdf",
        pages=[
            PageLayout(
                page_index=0,
                blocks=[
                    LayoutBlock(
                        block_id="blk_txt",
                        block_type=BlockType.PARAGRAPH,
                        bbox=BoundingBox(x0=150.0, y0=60.0, x1=300.0, y1=140.0, page_index=0),
                        text="Paragraph shifted",
                    ),
                    LayoutBlock(
                        block_id="blk_img",
                        block_type=BlockType.IMAGE,
                        bbox=BoundingBox(x0=180.0, y0=80.0, x1=350.0, y1=220.0, page_index=0),
                    ),
                ],
            )
        ],
    )

    report = verify_document_quality(original_layout=orig, reconstructed_layout=rec)
    assert report.summary.passed is False
    assert report.traffic_light == TrafficLightStatus.RED
    assert report.summary.collision_count == 1

    inc_types = {inc.incident_type for inc in report.incidents}
    assert IncidentType.COLLISION in inc_types
    assert IncidentType.LOW_IOU in inc_types
    assert IncidentType.MISSING_BLOCK in inc_types

    collision_inc = next(i for i in report.incidents if i.incident_type == IncidentType.COLLISION)
    assert collision_inc.severity == IncidentSeverity.CRITICAL


def test_overflow_truncation_and_margin_bleed_incidents() -> None:
    """Verify overflow warnings (YELLOW) vs empty truncation and margin bleeds."""
    orig = DocumentLayout(
        document_id="doc_margins",
        filename="margins.pdf",
        pages=[
            PageLayout(
                page_index=0,
                width=595.0,
                height=842.0,
                blocks=[
                    LayoutBlock(
                        block_id="blk_over",
                        bbox=BoundingBox(x0=50.0, y0=50.0, x1=250.0, y1=90.0, page_index=0),
                        text="Original text",
                    ),
                ],
            )
        ],
    )
    fitted = {
        "blk_over": TransformationResult(
            block_id="blk_over",
            original_text="Original text",
            transformed_text="Overflowing text",
            status=TransformationStatus.OVERFLOW_WARNING,
            applied_font_size=8.25,
            overflow_detected=True,
            warnings=["Desbordamiento en caja."],
        )
    }

    yellow_rep = verify_document_quality(
        original_layout=orig,
        reconstructed_layout=orig,
        fitted_results=fitted,
    )
    assert yellow_rep.summary.passed is True
    assert yellow_rep.summary.overflow_count == 1
    assert yellow_rep.traffic_light == TrafficLightStatus.YELLOW

    # Now introduce empty truncation + margin bleed
    bleed_rec = DocumentLayout(
        document_id="doc_margins",
        filename="margins.pdf",
        pages=[
            PageLayout(
                page_index=0,
                width=595.0,
                height=842.0,
                blocks=[
                    LayoutBlock(
                        block_id="blk_over",
                        bbox=BoundingBox(x0=2.0, y0=50.0, x1=250.0, y1=90.0, page_index=0),
                        text="",
                    ),
                ],
            )
        ],
    )
    red_rep = verify_document_quality(original_layout=orig, reconstructed_layout=bleed_rec)
    red_types = {inc.incident_type for inc in red_rep.incidents}
    assert IncidentType.EMPTY_TRUNCATION in red_types
    assert IncidentType.MARGIN_BLEED in red_types
    assert red_rep.summary.passed is False
    assert red_rep.traffic_light == TrafficLightStatus.RED


def test_static_ssim_detects_corrupted_visual_background() -> None:
    """Verify SSIM drops and triggers LOW_SSIM when static graphic regions are altered."""
    white = np.full((64, 64), 255.0, dtype=np.float64)
    black = np.zeros((64, 64), dtype=np.float64)
    assert compute_grayscale_ssim(white, white) == pytest.approx(1.0)
    assert compute_grayscale_ssim(white, black) < 0.05

    doc_a = pymupdf.open()
    pa = doc_a.new_page(width=400, height=400)
    pa.draw_rect((20, 20, 380, 380), color=(0, 0, 0), fill=(0, 0, 0))
    bytes_a = bytes(doc_a.tobytes())
    doc_a.close()

    doc_b = pymupdf.open()
    doc_b.new_page(width=400, height=400)  # Missing dark graphic background!
    bytes_b = bytes(doc_b.tobytes())
    doc_b.close()

    minimal_layout = DocumentLayout(
        document_id="doc_ssim",
        filename="graphic.pdf",
        pages=[PageLayout(page_index=0, width=400.0, height=400.0, blocks=[])],
    )
    verifier = LayoutQualityVerifier(min_ssim_threshold=0.90)
    rep = verifier.verify(
        original_layout=minimal_layout,
        reconstructed_layout=minimal_layout,
        original_pdf_bytes=bytes_a,
        reconstructed_pdf_bytes=bytes_b,
    )
    assert rep.summary.ssim_score < 0.50
    assert rep.summary.passed is False
    assert any(inc.incident_type == IncidentType.LOW_SSIM for inc in rep.incidents)

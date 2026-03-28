"""Tests for app.services.ocr — OCR text extraction from keyframe images."""

import logging
from pathlib import Path

import pytest
import torch

from app.services.keyframes import KeyFrame
from app.services.ocr import extract_text

logger = logging.getLogger(__name__)

requires_cuda = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="CUDA not available"
)

TEST_IMAGE = Path("data/test/funsd_82092117.png")


@requires_cuda
@pytest.mark.asyncio
async def test_ocr_extract_text():
    """OCR a document image and verify key text is extracted."""
    if not TEST_IMAGE.exists():
        pytest.skip(f"Test image not found: {TEST_IMAGE}")

    keyframes = [KeyFrame(timestamp=0.0, image_path=TEST_IMAGE)]
    results = await extract_text(keyframes)

    assert len(results) == 1
    result = results[0]
    assert result.timestamp == 0.0
    assert result.image_path == TEST_IMAGE

    logger.info("OCR text (%d chars):\n%s", len(result.text), result.text)

    text_lower = result.text.lower()
    assert "attorney general" in text_lower, f"Expected 'attorney general' in OCR output: {result.text[:200]}"
    assert "george baroody" in text_lower, f"Expected 'george baroody' in OCR output: {result.text[:200]}"
    assert "facsimile" in text_lower or "fax" in text_lower, f"Expected 'facsimile' or 'fax' in OCR output: {result.text[:200]}"


@requires_cuda
@pytest.mark.asyncio
async def test_ocr_vram_freed():
    """Verify VRAM is not leaked after OCR processing."""
    if not TEST_IMAGE.exists():
        pytest.skip(f"Test image not found: {TEST_IMAGE}")

    free_before, total = torch.cuda.mem_get_info(0)

    keyframes = [KeyFrame(timestamp=0.0, image_path=TEST_IMAGE)]
    await extract_text(keyframes)

    free_after, _ = torch.cuda.mem_get_info(0)
    leaked_mb = (free_before - free_after) / (1024 ** 2)
    logger.info("VRAM: %.0f MB free before, %.0f MB free after, delta=%.0f MB",
                free_before / (1024 ** 2), free_after / (1024 ** 2), leaked_mb)

    # Allow some tolerance (100 MB) for driver/framework overhead
    assert leaked_mb < 100, f"VRAM leak detected: {leaked_mb:.0f} MB not freed"

"""Tests for app.services.ocr — OCR text extraction from keyframe images."""

import logging
from pathlib import Path

import pytest
import torch

from app.config import TMP_DIR
from app.services.keyframes import KeyFrame
from app.services.ocr import OcrResult, extract_text, save_ocr_results

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

    output_dir = TMP_DIR / "test_ocr"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "ocr_result.md"
    output_path.write_text(result.text, encoding="utf-8")
    logger.info("OCR result saved to %s", output_path)

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


def test_save_ocr_results(tmp_path):
    """save_ocr_results writes text files and returns paths."""
    ocr_results = [
        OcrResult(timestamp=0.0, image_path=Path("/tmp/f1.png"), text="Hello world"),
        OcrResult(timestamp=5.0, image_path=Path("/tmp/f2.png"), text="Second frame"),
    ]
    paths = save_ocr_results(ocr_results, tmp_path)

    assert len(paths) == 2
    assert all(p is not None for p in paths)
    assert paths[0].read_text() == "Hello world"
    assert paths[1].read_text() == "Second frame"
    assert paths[0].name == "frame_0000_ocr.txt"
    assert paths[1].name == "frame_0001_ocr.txt"
    logger.info("Saved %d OCR files: %s", len(paths), [p.name for p in paths])


def test_save_ocr_results_empty_text(tmp_path):
    """save_ocr_results returns None for empty-text results."""
    ocr_results = [
        OcrResult(timestamp=0.0, image_path=Path("/tmp/f1.png"), text="Has text"),
        OcrResult(timestamp=5.0, image_path=Path("/tmp/f2.png"), text=""),
        OcrResult(timestamp=10.0, image_path=Path("/tmp/f3.png"), text="Also text"),
    ]
    paths = save_ocr_results(ocr_results, tmp_path)

    assert len(paths) == 3
    assert paths[0] is not None
    assert paths[1] is None
    assert paths[2] is not None
    # Only 2 files should exist
    ocr_dir = tmp_path / "ocr"
    assert len(list(ocr_dir.glob("*.txt"))) == 2
    logger.info("OCR paths (None=empty text): %s", [p.name if p else None for p in paths])

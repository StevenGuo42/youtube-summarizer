"""Tests for cli.py — requires network and Claude auth for summarization tests."""

import json
import logging
from pathlib import Path

import pytest

from app.config import TMP_DIR
from app.database import init_db
from app.services.keyframes import KeyFrame, deduplicate_keyframes
from app.services.transcript import Segment, TranscriptResult
from app.services.ocr import OcrResult
from cli import extract_video_id, run, parse_args

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def _ensure_db():
    await init_db()


# --- Unit tests ---

class TestExtractVideoId:
    def test_standard_url(self):
        result = extract_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        logger.info("standard_url: %s -> %s", "https://www.youtube.com/watch?v=jNQXAC9IVRw", result)
        assert result == "jNQXAC9IVRw"

    def test_short_url(self):
        result = extract_video_id("https://youtu.be/jNQXAC9IVRw")
        logger.info("short_url: %s -> %s", "https://youtu.be/jNQXAC9IVRw", result)
        assert result == "jNQXAC9IVRw"

    def test_embed_url(self):
        result = extract_video_id("https://www.youtube.com/embed/jNQXAC9IVRw")
        logger.info("embed_url: %s -> %s", "https://www.youtube.com/embed/jNQXAC9IVRw", result)
        assert result == "jNQXAC9IVRw"

    def test_with_params(self):
        result = extract_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=30")
        logger.info("with_params: %s -> %s", "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=30", result)
        assert result == "jNQXAC9IVRw"

    def test_bare_id(self):
        result = extract_video_id("jNQXAC9IVRw")
        logger.info("bare_id: %s -> %s", "jNQXAC9IVRw", result)
        assert result == "jNQXAC9IVRw"

    def test_invalid(self):
        with pytest.raises(ValueError):
            extract_video_id("not-a-url")
        logger.info("invalid: 'not-a-url' -> ValueError raised")


# --- Integration tests ---

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.asyncio
async def test_transcript_only(tmp_path):
    """Test --transcript-only outputs timestamped transcript."""
    output_file = tmp_path / "transcript.txt"
    args = parse_args.__wrapped__(
        [TEST_VIDEO_URL, "--transcript-only", "-o", str(output_file)]
    ) if hasattr(parse_args, '__wrapped__') else _make_args(
        url=TEST_VIDEO_URL, transcript_only=True, output=str(output_file),
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    logger.info("Transcript output (%d chars):\n%s", len(text), text[:500])
    assert len(text) > 0
    assert "[00:" in text  # has timestamps


@pytest.mark.asyncio
async def test_summarize_no_keyframes(tmp_path):
    """Test summarization without keyframes."""
    from app.services.llm import get_auth_status

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    output_file = tmp_path / "summary.md"
    args = _make_args(
        url=TEST_VIDEO_URL, no_keyframes=True, output=str(output_file),
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    logger.info("Summary output (%d chars):\n%s", len(text), text[:500])
    assert len(text) > 100
    assert "# " in text  # has markdown heading


@pytest.mark.asyncio
async def test_summarize_json_format(tmp_path):
    """Test JSON output format."""
    from app.services.llm import get_auth_status

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    output_file = tmp_path / "summary.json"
    args = _make_args(
        url=TEST_VIDEO_URL, no_keyframes=True, output=str(output_file), format="json",
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    data = json.loads(text)
    logger.info("JSON output: %s", json.dumps(data, indent=2, ensure_ascii=False)[:500])
    assert data["video_id"] == "jNQXAC9IVRw"
    assert data["title"]
    assert data["summary"]


def _make_args(**kwargs):
    """Create an argparse-like namespace with defaults."""
    defaults = {
        "url": "",
        "cookies": "data/cookies.txt",
        "prompt": None,
        "model": None,
        "output": None,
        "format": "markdown",
        "transcript_only": False,
        "no_keyframes": False,
    }
    defaults.update(kwargs)

    class Args:
        pass

    args = Args()
    for k, v in defaults.items():
        setattr(args, k, v)
    return args



class TestDeduplicateKeyframes:
    @staticmethod
    def _make_gradient(axis: str, start: tuple, end: tuple) -> "Image.Image":
        """Create a gradient image with actual spatial variation (solid colors are not pHash-distinguishable)."""
        import numpy as np
        from PIL import Image

        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        for i in range(100):
            t = i / 99
            color = [int(start[j] * (1 - t) + end[j] * t) for j in range(3)]
            if axis == "x":
                arr[:, i] = color
            else:
                arr[i, :] = color
        return Image.fromarray(arr)

    @staticmethod
    def _make_checkerboard(n: int) -> "Image.Image":
        """Create a checkerboard image."""
        import numpy as np
        from PIL import Image

        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        for y in range(100):
            for x in range(100):
                if (x // n + y // n) % 2 == 0:
                    arr[y, x] = [255, 255, 255]
        return Image.fromarray(arr)

    def test_phash_groups_identical_images(self, tmp_path):
        """Identical images are grouped, only first kept.

        Note: pHash on solid-color images is degenerate (all map to the same hash).
        Use gradient images with real spatial variation instead.
        img_a and img_b are identical horizontal gradients (distance=0, <= 5 => grouped).
        img_c is a vertical gradient (distance > 5 from img_a => kept).
        """
        img_a = self._make_gradient("x", (255, 0, 0), (0, 0, 255))   # red->blue horiz
        img_b = self._make_gradient("x", (255, 0, 0), (0, 0, 255))   # identical copy
        img_c = self._make_gradient("y", (0, 255, 0), (255, 0, 255)) # green->magenta vert
        path_a = tmp_path / "a.png"
        path_b = tmp_path / "b.png"
        path_c = tmp_path / "c.png"
        img_a.save(path_a)
        img_b.save(path_b)
        img_c.save(path_c)

        keyframes = [
            KeyFrame(timestamp=0.0, image_path=path_a),
            KeyFrame(timestamp=1.0, image_path=path_b),
            KeyFrame(timestamp=2.0, image_path=path_c),
        ]
        deduped, ocr_out = deduplicate_keyframes(keyframes)

        assert len(deduped) == 2
        assert deduped[0].timestamp == 0.0
        assert deduped[1].timestamp == 2.0
        assert ocr_out is None

    def test_phash_all_unique(self, tmp_path):
        """All unique images are preserved.

        Uses three visually distinct gradient/pattern images with pairwise pHash distance > 5.
        """
        img_a = self._make_gradient("x", (255, 0, 0), (0, 0, 255))   # red->blue horiz
        img_b = self._make_gradient("y", (0, 255, 0), (255, 0, 255)) # green->magenta vert
        img_c = self._make_checkerboard(10)                            # checkerboard
        keyframes = []
        for i, (img, name) in enumerate([(img_a, "a"), (img_b, "b"), (img_c, "c")]):
            path = tmp_path / f"{name}.png"
            img.save(path)
            keyframes.append(KeyFrame(timestamp=float(i), image_path=path))

        deduped, _ = deduplicate_keyframes(keyframes)
        assert len(deduped) == 3

    def test_phash_all_identical(self, tmp_path):
        """All identical images collapse to one."""
        img_template = self._make_gradient("x", (255, 0, 0), (0, 0, 255))

        keyframes = []
        for i in range(5):
            path = tmp_path / f"frame_{i}.png"
            img_template.save(path)
            keyframes.append(KeyFrame(timestamp=float(i), image_path=path))

        deduped, _ = deduplicate_keyframes(keyframes)
        assert len(deduped) == 1
        assert deduped[0].timestamp == 0.0

    def test_ocr_fuzzy_groups_similar_text(self):
        """OCR results with similar text are grouped."""
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/a.png")),
            KeyFrame(timestamp=1.0, image_path=Path("/tmp/b.png")),
            KeyFrame(timestamp=2.0, image_path=Path("/tmp/c.png")),
        ]
        ocr_results = [
            OcrResult(timestamp=0.0, image_path=Path("/tmp/a.png"), text="S&P 500 Index"),
            OcrResult(timestamp=1.0, image_path=Path("/tmp/b.png"), text="S&P 500 Index "),
            OcrResult(timestamp=2.0, image_path=Path("/tmp/c.png"), text="Dow Jones Industrial"),
        ]
        deduped, ocr_out = deduplicate_keyframes(keyframes, ocr_results=ocr_results)

        assert len(deduped) == 2
        assert deduped[0].timestamp == 0.0
        assert deduped[1].timestamp == 2.0
        assert len(ocr_out) == 2
        assert ocr_out[0].text == "S&P 500 Index"
        assert ocr_out[1].text == "Dow Jones Industrial"

    def test_ocr_empty_text_not_grouped(self):
        """Keyframes with empty OCR text are each kept as unique."""
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/a.png")),
            KeyFrame(timestamp=1.0, image_path=Path("/tmp/b.png")),
            KeyFrame(timestamp=2.0, image_path=Path("/tmp/c.png")),
        ]
        ocr_results = [
            OcrResult(timestamp=0.0, image_path=Path("/tmp/a.png"), text=""),
            OcrResult(timestamp=1.0, image_path=Path("/tmp/b.png"), text=""),
            OcrResult(timestamp=2.0, image_path=Path("/tmp/c.png"), text="Some text"),
        ]
        deduped, ocr_out = deduplicate_keyframes(keyframes, ocr_results=ocr_results)

        assert len(deduped) == 3
        assert len(ocr_out) == 3

    def test_empty_input(self):
        """Empty keyframes returns empty."""
        deduped, ocr_out = deduplicate_keyframes([])
        assert deduped == []
        assert ocr_out is None

    def test_single_keyframe(self):
        """Single keyframe is returned as-is."""
        kf = KeyFrame(timestamp=0.0, image_path=Path("/tmp/a.png"))
        deduped, _ = deduplicate_keyframes([kf])
        assert len(deduped) == 1
        assert deduped[0] is kf

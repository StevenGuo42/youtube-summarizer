"""Tests for cli.py — requires network and Claude auth for summarization tests."""

import json
import logging
from pathlib import Path

import pytest

from app.config import TMP_DIR
from app.database import init_db
from app.services.transcript import Segment, TranscriptResult, inject_ocr_into_transcript
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


class TestInjectOcrIntoTranscript:
    def test_basic_injection(self):
        """OCR text segments are inserted at correct timestamp positions."""
        transcript = TranscriptResult(
            text="hello world",
            segments=[
                Segment(start=0.0, end=3.0, text="hello"),
                Segment(start=3.0, end=6.0, text="world"),
            ],
            source="captions",
        )
        ocr_results = [
            OcrResult(timestamp=2.0, image_path=Path("/tmp/f1.png"), text="Screen text A"),
        ]
        result = inject_ocr_into_transcript(transcript, ocr_results)

        assert len(result.segments) == 3
        assert result.segments[0].text == "hello"
        assert result.segments[1].text == "[OCR TEXT: Screen text A]"
        assert result.segments[1].start == 2.0
        assert result.segments[1].end == 2.0
        assert result.segments[2].text == "world"
        assert result.source == "captions"

    def test_does_not_mutate_original(self):
        """Original transcript is not modified."""
        transcript = TranscriptResult(
            text="hello",
            segments=[Segment(start=0.0, end=3.0, text="hello")],
            source="captions",
        )
        ocr_results = [
            OcrResult(timestamp=1.0, image_path=Path("/tmp/f.png"), text="OCR"),
        ]
        result = inject_ocr_into_transcript(transcript, ocr_results)

        assert len(transcript.segments) == 1  # original unchanged
        assert len(result.segments) == 2

    def test_skips_empty_ocr(self):
        """OCR results with empty text are skipped."""
        transcript = TranscriptResult(
            text="hello",
            segments=[Segment(start=0.0, end=3.0, text="hello")],
            source="captions",
        )
        ocr_results = [
            OcrResult(timestamp=1.0, image_path=Path("/tmp/f.png"), text=""),
        ]
        result = inject_ocr_into_transcript(transcript, ocr_results)

        assert len(result.segments) == 1  # no OCR segment added

    def test_multiple_ocr_results(self):
        """Multiple OCR results inserted in timestamp order."""
        transcript = TranscriptResult(
            text="a b c",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
                Segment(start=6.0, end=9.0, text="c"),
            ],
            source="captions",
        )
        ocr_results = [
            OcrResult(timestamp=2.0, image_path=Path("/tmp/f1.png"), text="OCR1"),
            OcrResult(timestamp=5.0, image_path=Path("/tmp/f2.png"), text="OCR2"),
        ]
        result = inject_ocr_into_transcript(transcript, ocr_results)

        assert len(result.segments) == 5
        texts = [s.text for s in result.segments]
        assert texts == ["a", "[OCR TEXT: OCR1]", "b", "[OCR TEXT: OCR2]", "c"]

    def test_updates_text_field(self):
        """The .text field is rebuilt to include OCR text."""
        transcript = TranscriptResult(
            text="hello world",
            segments=[
                Segment(start=0.0, end=3.0, text="hello"),
                Segment(start=3.0, end=6.0, text="world"),
            ],
            source="captions",
        )
        ocr_results = [
            OcrResult(timestamp=2.0, image_path=Path("/tmp/f.png"), text="SCREEN"),
        ]
        result = inject_ocr_into_transcript(transcript, ocr_results)
        assert "[OCR TEXT: SCREEN]" in result.text

    def test_empty_ocr_list(self):
        """Empty OCR list returns equivalent transcript."""
        transcript = TranscriptResult(
            text="hello",
            segments=[Segment(start=0.0, end=3.0, text="hello")],
            source="captions",
        )
        result = inject_ocr_into_transcript(transcript, [])
        assert len(result.segments) == 1
        assert result.segments[0].text == "hello"

"""Tests for app.services.llm — unit tests (no network) and integration tests (require Claude auth)."""

import json
import logging

import pytest

from app.database import init_db
from app.services.llm import (
    DEFAULT_PROMPT,
    SummaryResult,
    _build_interleaved_transcript,
    _format_duration,
    _format_timestamp,
    _parse_response,
    get_auth_status,
    summarize,
)
from app.services.keyframes import KeyFrame
from app.services.transcript import Segment, TranscriptResult

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def _ensure_db():
    """Ensure database tables exist before integration tests."""
    await init_db()


# --- Unit tests (no network) ---


class TestFormatTimestamp:
    def test_seconds_only(self):
        assert _format_timestamp(45.0) == "0:45"

    def test_minutes_and_seconds(self):
        assert _format_timestamp(125.5) == "2:05"

    def test_hours(self):
        assert _format_timestamp(3661.0) == "1:01:01"

    def test_zero(self):
        assert _format_timestamp(0.0) == "0:00"


class TestFormatDuration:
    def test_minutes(self):
        assert _format_duration(125) == "2m05s"

    def test_hours(self):
        assert _format_duration(3725) == "1h02m"

    def test_short(self):
        assert _format_duration(30) == "0m30s"


class TestParseResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "title": "Test Video",
            "tldr": "A test.",
            "summary": "# Summary\n\nDetails here.",
        })
        result = _parse_response(raw)
        assert result.title == "Test Video"
        assert result.tldr == "A test."
        assert result.summary == "# Summary\n\nDetails here."
        assert result.raw_response == raw

    def test_json_in_code_block(self):
        raw = '```json\n{"title": "T", "tldr": "TL", "summary": "S"}\n```'
        result = _parse_response(raw)
        assert result.title == "T"
        assert result.tldr == "TL"
        assert result.summary == "S"

    def test_plain_code_block(self):
        raw = '```\n{"title": "T", "tldr": "TL", "summary": "S"}\n```'
        result = _parse_response(raw)
        assert result.title == "T"

    def test_invalid_json_falls_back(self):
        raw = "This is just plain text, not JSON at all."
        result = _parse_response(raw)
        assert result.title == ""
        assert result.tldr == ""
        assert result.summary == raw
        assert result.raw_response == raw

    def test_partial_json(self):
        raw = json.dumps({"title": "Only Title"})
        result = _parse_response(raw)
        assert result.title == "Only Title"
        assert result.tldr == ""
        assert result.summary == ""


class TestBuildInterleavedTranscript:
    def test_no_keyframes(self):
        """Without keyframes, all segments merge into one block."""
        transcript = TranscriptResult(
            text="hello world",
            segments=[
                Segment(start=0.0, end=2.0, text="hello"),
                Segment(start=2.0, end=4.0, text="world"),
            ],
            source="captions",
        )
        result = _build_interleaved_transcript(transcript, [])
        assert result == "[0:00 - 0:04] hello world"
        assert "KEYFRAME" not in result

    def test_keyframes_group_segments(self):
        """Segments are grouped by keyframe boundaries."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b c d",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=5.0, text="b"),
                Segment(start=5.0, end=8.0, text="c"),
                Segment(start=8.0, end=10.0, text="d"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
            KeyFrame(timestamp=5.0, image_path=Path("/tmp/frame2.png")),
        ]
        result = _build_interleaved_transcript(transcript, keyframes)
        blocks = result.split("\n\n")
        assert len(blocks) == 2
        # First group: frame1 + segments a,b
        assert "[KEYFRAME: /tmp/frame1.png]" in blocks[0]
        assert "[0:00 - 0:05] a b" in blocks[0]
        # Second group: frame2 + segments c,d
        assert "[KEYFRAME: /tmp/frame2.png]" in blocks[1]
        assert "[0:05 - 0:10] c d" in blocks[1]

    def test_segments_before_first_keyframe(self):
        """Segments before the first keyframe form their own group."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="intro main",
            segments=[
                Segment(start=0.0, end=3.0, text="intro"),
                Segment(start=3.0, end=6.0, text="main"),
            ],
            source="captions",
        )
        keyframes = [KeyFrame(timestamp=3.0, image_path=Path("/tmp/frame1.png"))]
        result = _build_interleaved_transcript(transcript, keyframes)
        blocks = result.split("\n\n")
        assert len(blocks) == 2
        # First block: pre-keyframe segment
        assert blocks[0] == "[0:00 - 0:03] intro"
        # Second block: keyframe + segment
        assert "[KEYFRAME: /tmp/frame1.png]" in blocks[1]
        assert "[0:03 - 0:06] main" in blocks[1]

    def test_keyframe_at_start(self):
        """Keyframe at timestamp 0 groups all segments."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="hello world",
            segments=[
                Segment(start=0.0, end=2.0, text="hello"),
                Segment(start=2.0, end=4.0, text="world"),
            ],
            source="captions",
        )
        keyframes = [KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame0.png"))]
        result = _build_interleaved_transcript(transcript, keyframes)
        assert "[KEYFRAME: /tmp/frame0.png]" in result
        assert "[0:00 - 0:04] hello world" in result

    def test_keyframe_after_all_segments(self):
        """Keyframe after all segments appears as its own block."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="hello",
            segments=[Segment(start=0.0, end=2.0, text="hello")],
            source="captions",
        )
        keyframes = [KeyFrame(timestamp=5.0, image_path=Path("/tmp/frame_end.png"))]
        result = _build_interleaved_transcript(transcript, keyframes)
        blocks = result.split("\n\n")
        assert blocks[0] == "[0:00 - 0:02] hello"
        assert "[KEYFRAME: /tmp/frame_end.png]" in blocks[1]

    def test_no_segments_fallback(self):
        transcript = TranscriptResult(text="plain text only", segments=[], source="captions")
        result = _build_interleaved_transcript(transcript, [])
        assert result == "plain text only"


# --- Integration tests (require Claude auth) ---


@pytest.mark.asyncio
async def test_auth_status():
    """Check that auth status returns a valid response."""
    status = await get_auth_status()
    logger.info("Auth status: %s", status)
    assert "loggedIn" in status
    if status["loggedIn"]:
        logger.info("Logged in as %s (%s)", status.get("email"), status.get("subscriptionType"))


@pytest.mark.asyncio
async def test_summarize_basic():
    """Test summarization with a short transcript and no keyframes."""
    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    transcript = TranscriptResult(
        text="Welcome to this video about making pasta...",
        segments=[
            Segment(start=0.0, end=5.0, text="Welcome to this video about making pasta."),
            Segment(start=5.0, end=12.0, text="First, boil water in a large pot. Add salt generously."),
            Segment(start=12.0, end=20.0, text="When the water is at a rolling boil, add the pasta."),
            Segment(start=20.0, end=28.0, text="Cook for 8 to 10 minutes until al dente."),
            Segment(start=28.0, end=38.0, text="Drain the pasta and toss with your favorite sauce."),
            Segment(start=38.0, end=45.0, text="Today we used a simple garlic and olive oil sauce."),
            Segment(start=45.0, end=50.0, text="Serve immediately and enjoy."),
        ],
        source="captions",
    )

    video_meta = {
        "title": "How to Cook Perfect Pasta",
        "channel": "Cooking 101",
        "duration": 300,
    }

    result = await summarize(
        transcript=transcript,
        keyframes=[],
        video_meta=video_meta,
    )

    logger.info("Title: %s", result.title)
    logger.info("TLDR: %s", result.tldr)
    logger.info("Summary: %s", result.summary)
    logger.info("Raw response length: %d", len(result.raw_response))

    assert isinstance(result, SummaryResult)
    assert result.title
    assert result.tldr
    assert result.summary
    assert len(result.raw_response) > 0


@pytest.mark.asyncio
async def test_summarize_custom_prompt():
    """Test summarization with a custom prompt."""
    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    transcript = TranscriptResult(
        text="In this video we review the new smartphone from TechBrand...",
        segments=[
            Segment(start=0.0, end=8.0, text="In this video we review the new smartphone from TechBrand."),
            Segment(start=8.0, end=16.0, text="The display is a 6.7 inch OLED with 120Hz refresh rate."),
            Segment(start=16.0, end=24.0, text="The camera system includes a 50MP main sensor and 12MP ultrawide."),
            Segment(start=24.0, end=32.0, text="Battery life lasted about 8 hours in our testing."),
            Segment(start=32.0, end=38.0, text="Price starts at 799 dollars."),
        ],
        source="captions",
    )

    video_meta = {
        "title": "TechBrand Phone Review",
        "channel": "GadgetReviews",
        "duration": 600,
    }

    custom_prompt = (
        "You are a tech reviewer. Summarize the video as a product review. "
        "Return valid JSON with keys: title, tldr, summary. "
        "The summary should focus on specs, pros, and cons. "
        "Return ONLY valid JSON."
    )

    result = await summarize(
        transcript=transcript,
        keyframes=[],
        video_meta=video_meta,
        custom_prompt=custom_prompt,
    )

    logger.info("Custom prompt result - Title: %s", result.title)
    logger.info("Custom prompt result - Summary: %s", result.summary)

    assert isinstance(result, SummaryResult)
    assert result.raw_response


@pytest.mark.asyncio
async def test_summarize_with_keyframes():
    """Test summarization using real transcript + keyframes from the members-only video."""
    from pathlib import Path

    from app.config import TMP_DIR

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    transcript_dir = TMP_DIR / "test_members_transcript"
    keyframes_dir = TMP_DIR / "test_members_keyframes" / "frames"

    if not transcript_dir.exists() or not keyframes_dir.exists():
        pytest.skip("Run test_transcript and test_keyframes members-only tests first to generate artifacts")

    # Load transcript from whisper results
    from app.services.transcript import _transcribe_whisper

    audio_path = transcript_dir / "CMCNg8B6tA0.m4a"
    if not audio_path.exists():
        pytest.skip("Members-only audio not downloaded")

    transcript = await _transcribe_whisper(audio_path, transcript_dir)

    # Load keyframes
    frame_files = sorted(keyframes_dir.glob("*.png"))
    if not frame_files:
        pytest.skip("No keyframe files found")

    # Assign timestamps evenly across 2 minutes (matching the test video clip)
    interval = 120.0 / len(frame_files)
    keyframes_list = [
        KeyFrame(timestamp=i * interval, image_path=f)
        for i, f in enumerate(frame_files)
    ]

    video_meta = {
        "title": "标普 道指 纳指 罗素 指数 成分结构 特性和区别",
        "channel": "视野环球财经",
        "duration": 1644,
    }

    # Log the prompt that will be sent
    from app.services.llm import _build_interleaved_transcript

    prompt_preview = _build_interleaved_transcript(transcript, keyframes_list)
    logger.info("Prompt preview (first 1000 chars):\n%s", prompt_preview[:1000])
    logger.info("Total keyframe groups: %d", prompt_preview.count("[KEYFRAME:"))

    result = await summarize(
        transcript=transcript,
        keyframes=keyframes_list,
        video_meta=video_meta,
    )

    logger.info("Title: %s", result.title)
    logger.info("TLDR: %s", result.tldr)
    logger.info("Summary (first 500 chars): %s", result.summary[:500])

    # Save result for verification
    output_dir = TMP_DIR / "test_members_summary"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "summary.md").write_text(
        f"# {result.title}\n\n**TL;DR:** {result.tldr}\n\n{result.summary}",
        encoding="utf-8",
    )
    (output_dir / "raw_response.txt").write_text(result.raw_response, encoding="utf-8")
    logger.info("Summary saved to %s", output_dir)

    assert isinstance(result, SummaryResult)
    assert result.title
    assert result.tldr
    assert result.summary

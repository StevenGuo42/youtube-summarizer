"""Tests for app.services.llm — unit tests (no network) and integration tests (require Claude auth)."""

import json
import logging

import pytest

from cli import _resolve_keyframe_mode
from app.database import init_db
from app.services.llm import (
    DEFAULT_PROMPT,
    PROMPT_PLACEHOLDER,
    KeyframeMode,
    SummaryResult,
    _build_interleaved_transcript,
    _format_duration,
    _format_timestamp,
    _parse_response,
    build_system_prompt,
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
        logger.info("parse_response fallback: raw text -> title='', summary=raw")
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
    def test_no_keyframes_none_mode(self):
        """NONE mode wraps transcript in <transcript> tags."""
        transcript = TranscriptResult(
            text="hello world",
            segments=[
                Segment(start=0.0, end=2.0, text="hello"),
                Segment(start=2.0, end=4.0, text="world"),
            ],
            source="captions",
        )
        result = _build_interleaved_transcript(transcript, [], mode=KeyframeMode.NONE)
        logger.info("NONE mode output:\n%s", result)
        assert result == "<transcript>\nhello world\n</transcript>"
        assert "KEYFRAME" not in result

    def test_image_mode_with_timestamp_range(self):
        """IMAGE mode shows timestamp range + KEYFRAME + <transcript> tags."""
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
        meta = {"duration": 60}
        result = _build_interleaved_transcript(
            transcript, keyframes, video_meta=meta,
        )
        logger.info("IMAGE mode output:\n%s", result)
        blocks = result.split("\n\n")
        assert len(blocks) == 2
        assert "[0:00 - 0:05]" in blocks[0]
        assert "[KEYFRAME: /tmp/frame1.png]" in blocks[0]
        assert "<transcript>" in blocks[0]
        assert "a b" in blocks[0]
        assert "</transcript>" in blocks[0]
        assert "[0:05 - 1:00]" in blocks[1]
        assert "[KEYFRAME: /tmp/frame2.png]" in blocks[1]
        assert "c d" in blocks[1]

    def test_ocr_mode(self):
        """OCR mode shows [OCR: path] instead of [KEYFRAME:]."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        ocr_paths = [Path("/tmp/ocr/frame_0000_ocr.txt")]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, mode=KeyframeMode.OCR,
            ocr_paths=ocr_paths, video_meta=meta,
        )
        logger.info("OCR mode output:\n%s", result)
        assert "[OCR: /tmp/ocr/frame_0000_ocr.txt]" in result
        assert "[KEYFRAME:" not in result
        assert "<transcript>" in result

    def test_ocr_image_mode(self):
        """OCR_IMAGE mode shows both [KEYFRAME:] and [OCR:]."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        ocr_paths = [Path("/tmp/ocr/frame_0000_ocr.txt")]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, mode=KeyframeMode.OCR_IMAGE,
            ocr_paths=ocr_paths, video_meta=meta,
        )
        logger.info("OCR_IMAGE mode output:\n%s", result)
        assert "[KEYFRAME: /tmp/frame1.png]" in result
        assert "[OCR: /tmp/ocr/frame_0000_ocr.txt]" in result
        assert "<transcript>" in result

    def test_ocr_inline_mode(self):
        """OCR_INLINE mode shows <ocr_text> tags, no KEYFRAME."""
        from pathlib import Path
        from app.services.ocr import OcrResult

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        ocr_results = [
            OcrResult(timestamp=0.0, image_path=Path("/tmp/frame1.png"), text="Screen text"),
        ]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, mode=KeyframeMode.OCR_INLINE,
            ocr_results=ocr_results, video_meta=meta,
        )
        logger.info("OCR_INLINE mode output:\n%s", result)
        assert "[KEYFRAME:" not in result
        assert "<transcript>" in result
        assert "<ocr_text>" in result
        assert "Screen text" in result
        assert "</ocr_text>" in result

    def test_ocr_inline_image_mode(self):
        """OCR_INLINE_IMAGE mode shows KEYFRAME + <transcript> + <ocr_text>."""
        from pathlib import Path
        from app.services.ocr import OcrResult

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        ocr_results = [
            OcrResult(timestamp=0.0, image_path=Path("/tmp/frame1.png"), text="Screen text"),
        ]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, mode=KeyframeMode.OCR_INLINE_IMAGE,
            ocr_results=ocr_results, video_meta=meta,
        )
        logger.info("OCR_INLINE_IMAGE mode output:\n%s", result)
        assert "[KEYFRAME: /tmp/frame1.png]" in result
        assert "<transcript>" in result
        assert "<ocr_text>" in result
        assert "Screen text" in result

    def test_no_per_segment_timestamps(self):
        """Transcript text has no per-segment timestamps."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, video_meta=meta,
        )
        lines = result.split("\n")
        transcript_lines = []
        in_transcript = False
        for line in lines:
            if line == "<transcript>":
                in_transcript = True
                continue
            if line == "</transcript>":
                in_transcript = False
                continue
            if in_transcript:
                transcript_lines.append(line)
        for line in transcript_lines:
            assert not line.startswith("["), f"Found timestamp in transcript: {line}"

    def test_first_keyframe_starts_at_zero(self):
        """First keyframe range always starts at 0:00."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a",
            segments=[Segment(start=0.0, end=3.0, text="a")],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=2.0, image_path=Path("/tmp/frame1.png")),
        ]
        meta = {"duration": 10}
        result = _build_interleaved_transcript(
            transcript, keyframes, video_meta=meta,
        )
        logger.info("First keyframe range output:\n%s", result)
        assert "[0:00 - 0:02]" in result
        assert "[0:02 - 0:10]" in result

    def test_last_keyframe_ends_at_duration(self):
        """Last keyframe range ends at video duration."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        meta = {"duration": 120}
        result = _build_interleaved_transcript(
            transcript, keyframes, video_meta=meta,
        )
        logger.info("Last keyframe range output:\n%s", result)
        assert "[0:00 - 2:00]" in result

    def test_no_segments_fallback(self):
        """No segments returns raw text."""
        transcript = TranscriptResult(text="plain text only", segments=[], source="captions")
        result = _build_interleaved_transcript(transcript, [])
        logger.info("No segments fallback: raw text returned as-is")
        assert result == "plain text only"

    def test_duration_fallback_to_last_segment(self):
        """Without video_meta duration, last range uses last segment end time."""
        from pathlib import Path

        transcript = TranscriptResult(
            text="a b",
            segments=[
                Segment(start=0.0, end=3.0, text="a"),
                Segment(start=3.0, end=6.0, text="b"),
            ],
            source="captions",
        )
        keyframes = [
            KeyFrame(timestamp=0.0, image_path=Path("/tmp/frame1.png")),
        ]
        result = _build_interleaved_transcript(transcript, keyframes)
        logger.info("No duration -> falls back to last segment end:\n%s", result)
        assert "[0:00 - 0:06]" in result


class TestBuildSystemPrompt:
    def test_no_language_appends_follow_transcript(self):
        """No output_language appends 'same language as the transcript'."""
        result = build_system_prompt(None)
        assert "You MUST write your entire response in the same language as the transcript." in result

    def test_specific_language(self):
        """Specific output_language appends explicit instruction."""
        result = build_system_prompt(None, output_language="Japanese")
        assert "You MUST write your entire response in Japanese." in result
        assert "same language as the transcript" not in result

    def test_empty_string_language_follows_transcript(self):
        """Empty string output_language treated same as None."""
        result = build_system_prompt(None, output_language="")
        assert "same language as the transcript" in result

    def test_language_with_custom_prompt_insert(self):
        """Language instruction appended after custom prompt insertion."""
        result = build_system_prompt("Focus on key points.", custom_prompt_mode="insert", output_language="French")
        assert "Focus on key points." in result
        assert "You MUST write your entire response in French." in result

    def test_language_with_custom_prompt_replace(self):
        """Language instruction appended even with replace mode."""
        result = build_system_prompt("My custom prompt.", custom_prompt_mode="replace", output_language="Korean")
        assert result.startswith("My custom prompt.")
        assert "You MUST write your entire response in Korean." in result


class TestResolveKeyframeMode:
    def test_defaults(self):
        """Default flags resolve to IMAGE."""
        assert _resolve_keyframe_mode(no_keyframes=False, ocr="none") == KeyframeMode.IMAGE

    def test_no_keyframes_only(self):
        assert _resolve_keyframe_mode(no_keyframes=True, ocr="none") == KeyframeMode.NONE

    def test_ocr_file(self):
        assert _resolve_keyframe_mode(no_keyframes=False, ocr="file") == KeyframeMode.OCR_IMAGE

    def test_ocr_inline(self):
        assert _resolve_keyframe_mode(no_keyframes=False, ocr="inline") == KeyframeMode.OCR_INLINE_IMAGE

    def test_no_keyframes_ocr_file(self):
        assert _resolve_keyframe_mode(no_keyframes=True, ocr="file") == KeyframeMode.OCR

    def test_no_keyframes_ocr_inline(self):
        assert _resolve_keyframe_mode(no_keyframes=True, ocr="inline") == KeyframeMode.OCR_INLINE


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


# --- Mode combination matrix (all 6 keyframe modes with real data) ---

# Maps (no_keyframes, ocr) CLI flag combos to their expected mode
_MODE_COMBOS = [
    (False, "none",   KeyframeMode.IMAGE),
    (False, "file",   KeyframeMode.OCR_IMAGE),
    (False, "inline", KeyframeMode.OCR_INLINE_IMAGE),
    (True,  "none",   KeyframeMode.NONE),
    (True,  "file",   KeyframeMode.OCR),
    (True,  "inline", KeyframeMode.OCR_INLINE),
]


async def _load_test_data():
    """Load transcript + keyframes from 5-minute members-only test clip.

    Uses data/tmp/test_members_5min/ which has a 5-minute video clip
    with pre-extracted keyframes (real timestamps from ffmpeg).
    Transcript is generated via whisper from the video's audio.
    """
    from pathlib import Path
    import subprocess

    from app.config import TMP_DIR
    from app.services.keyframes import extract_keyframes
    from app.services.transcript import _transcribe_whisper

    work_dir = TMP_DIR / "test_members_5min"
    frames_dir = work_dir / "frames"
    video_path = work_dir / "CMCNg8B6tA0.webm"

    if not video_path.exists():
        pytest.skip("5-minute test clip not downloaded (data/tmp/test_members_5min/)")

    # Get video duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    video_duration = float(probe.stdout.strip()) if probe.returncode == 0 else 300.0

    # Extract keyframes (with real timestamps from ffmpeg)
    keyframes = await extract_keyframes(video_path, work_dir)
    logger.info("Loaded %d keyframes from %s", len(keyframes), video_path.name)

    # Transcribe audio
    transcript = await _transcribe_whisper(video_path, work_dir)
    logger.info("Transcribed %d segments (%.0fs)", len(transcript.segments), video_duration)

    video_meta = {
        "title": "标普 道指 纳指 罗素 指数 成分结构 特性和区别",
        "channel": "视野环球财经",
        "duration": video_duration,
    }

    return transcript, keyframes, video_meta


@pytest.mark.asyncio
@pytest.mark.parametrize("no_keyframes,ocr_flag,expected_mode", _MODE_COMBOS,
                         ids=[m.value for _, _, m in _MODE_COMBOS])
async def test_summarize_all_modes(no_keyframes, ocr_flag, expected_mode):
    """Test summarization with all 6 keyframe mode combinations using real data."""
    from pathlib import Path

    from app.config import TMP_DIR
    from app.services.ocr import extract_text, save_ocr_results

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    transcript, keyframes, video_meta = await _load_test_data()
    mode = _resolve_keyframe_mode(no_keyframes, ocr_flag)
    assert mode == expected_mode

    output_dir = TMP_DIR / f"test_mode_{mode.value.replace('+', '_')}"
    output_dir.mkdir(exist_ok=True)

    logger.info("=== Testing mode: %s ===", mode.value)
    logger.info("Flags: --no-keyframes=%s --ocr=%s", no_keyframes, ocr_flag)

    # Determine what keyframes to pass to summarize
    kf_for_summarize = keyframes
    ocr_paths = None

    # Run OCR if needed
    needs_ocr = mode in (
        KeyframeMode.OCR, KeyframeMode.OCR_IMAGE,
        KeyframeMode.OCR_INLINE, KeyframeMode.OCR_INLINE_IMAGE,
    )
    ocr_results = None
    if needs_ocr:
        logger.info("Running OCR on %d keyframes...", len(keyframes))
        ocr_results = await extract_text(keyframes)
        ocr_count = sum(1 for r in ocr_results if r.text)
        logger.info("OCR extracted text from %d/%d keyframes", ocr_count, len(ocr_results))

        # Save OCR text for inspection regardless of mode
        for i, r in enumerate(ocr_results):
            if r.text:
                (output_dir / f"ocr_{i:04d}.txt").write_text(r.text, encoding="utf-8")

    # Deduplicate keyframes (pHash first, OCR only on deduped frames)
    if kf_for_summarize:
        from app.services.keyframes import deduplicate_keyframes
        kf_for_summarize, ocr_results = deduplicate_keyframes(
            kf_for_summarize, ocr_results=ocr_results, mode="regular",
        )
        logger.info("After dedup: %d keyframes", len(kf_for_summarize))

    # Save OCR files for file-based modes (after dedup)
    if ocr_results and mode in (KeyframeMode.OCR, KeyframeMode.OCR_IMAGE):
        ocr_paths = save_ocr_results(ocr_results, output_dir)
        logger.info("Saved %d OCR files", sum(1 for p in ocr_paths if p))

    # For NONE mode, pass no keyframes
    if mode == KeyframeMode.NONE:
        kf_for_summarize = []

    # Save the transcript that will be sent
    transcript_text = _build_interleaved_transcript(
        transcript, kf_for_summarize, mode=mode, ocr_paths=ocr_paths,
        video_meta=video_meta, ocr_results=ocr_results,
    )
    (output_dir / "transcript_prompt.txt").write_text(transcript_text, encoding="utf-8")
    logger.info("Transcript prompt saved (%d chars)", len(transcript_text))

    # Summarize
    result = await summarize(
        transcript=transcript,
        keyframes=kf_for_summarize,
        video_meta=video_meta,
        keyframe_mode=mode,
        ocr_paths=ocr_paths,
        ocr_results=ocr_results,
    )

    # Save outputs
    (output_dir / "summary.md").write_text(
        f"# {result.title}\n\n**TL;DR:** {result.tldr}\n\n{result.summary}",
        encoding="utf-8",
    )
    (output_dir / "raw_response.txt").write_text(result.raw_response, encoding="utf-8")
    logger.info("Mode %s — Title: %s", mode.value, result.title)
    logger.info("Mode %s — TLDR: %s", mode.value, result.tldr)
    logger.info("Mode %s — Summary (%d chars): %s", mode.value, len(result.summary), result.summary[:300])
    logger.info("Outputs saved to %s", output_dir)

    assert isinstance(result, SummaryResult)
    assert result.raw_response
    assert result.title or result.summary, f"Mode {mode.value} produced no output"


@pytest.mark.asyncio
@pytest.mark.parametrize("dedup_mode", ["regular", "slides", "none"])
async def test_dedup_modes(dedup_mode):
    """Compare dedup modes on real keyframes — saves transcript prompts for inspection."""
    from pathlib import Path

    from app.config import TMP_DIR
    from app.services.keyframes import deduplicate_keyframes

    transcript, keyframes, video_meta = await _load_test_data()

    output_dir = TMP_DIR / f"test_dedup_{dedup_mode}"
    output_dir.mkdir(exist_ok=True)

    logger.info("=== Dedup mode: %s, input: %d keyframes ===", dedup_mode, len(keyframes))

    deduped, _ = deduplicate_keyframes(keyframes, mode=dedup_mode)
    logger.info("Dedup %s: %d -> %d keyframes", dedup_mode, len(keyframes), len(deduped))

    # Log which frames survived
    for i, kf in enumerate(deduped):
        logger.info("  [%d] t=%.1f %s", i, kf.timestamp, kf.image_path.name)

    # Save transcript prompt for inspection
    transcript_text = _build_interleaved_transcript(
        transcript, deduped, mode=KeyframeMode.IMAGE, video_meta=video_meta,
    )
    (output_dir / "transcript_prompt.txt").write_text(transcript_text, encoding="utf-8")
    logger.info("Saved to %s (%d chars)", output_dir, len(transcript_text))

    # Verify expectations
    if dedup_mode == "none":
        assert len(deduped) == len(keyframes), "none mode should keep all frames"
    elif dedup_mode == "slides":
        assert len(deduped) >= 1, "slides mode should keep at least 1 frame"
        # slides should keep >= regular (stricter threshold keeps more)
    elif dedup_mode == "regular":
        assert len(deduped) >= 1, "regular mode should keep at least 1 frame"

    # Log comparison if not 'none'
    if dedup_mode != "none":
        logger.info("Dedup %s kept %d/%d (%.0f%%) keyframes",
                     dedup_mode, len(deduped), len(keyframes),
                     100 * len(deduped) / len(keyframes))


# ---------------------------------------------------------------------------
# Phase 13 Wave 0 test stubs — added for Plan 01
# ---------------------------------------------------------------------------


class TestPipelineImportCompat:
    """Verify pipeline.py and routers/settings.py imports resolve after package split (D-02)."""

    def test_pipeline_imports_still_resolve(self):
        """from app.services.llm import summarize, KeyframeMode must work post-refactor."""
        from app.services.llm import summarize, KeyframeMode  # noqa: F401
        assert callable(summarize)
        assert KeyframeMode.IMAGE == "image"
        logger.info("pipeline.py import compat: OK")

    def test_settings_router_imports_still_resolve(self):
        """routers/settings.py imports must work post-refactor."""
        from app.services.llm import DEFAULT_PROMPT, PROMPT_PLACEHOLDER, get_auth_status  # noqa: F401
        assert PROMPT_PLACEHOLDER in DEFAULT_PROMPT
        assert callable(get_auth_status)
        logger.info("routers/settings.py import compat: OK")

    def test_summary_result_importable(self):
        from app.services.llm import SummaryResult  # noqa: F401
        assert SummaryResult is not None

    def test_list_backends_returns_list(self):
        from app.services.llm import list_backends
        backends = list_backends()
        assert isinstance(backends, list)
        assert "claude" in backends
        logger.info("list_backends: %s", backends)

    def test_get_active_backend_returns_backend(self):
        from app.services.llm import get_active_backend
        backend = get_active_backend()
        assert backend is not None
        logger.info("get_active_backend: %s", type(backend).__name__)


class TestBuildCodexTranscript:
    """Codex transcript uses [KEYFRAME N] index markers, not [KEYFRAME: path] (D-06)."""

    def _make_transcript(self):
        return TranscriptResult(
            text="hello world. next segment.",
            segments=[
                Segment(start=0.0, end=5.0, text="hello world."),
                Segment(start=10.0, end=15.0, text="next segment."),
            ],
            source="captions",
        )

    def _make_keyframes(self, tmp_path):
        kf1 = KeyFrame(timestamp=3.0, image_path=tmp_path / "frame1.png")
        kf2 = KeyFrame(timestamp=12.0, image_path=tmp_path / "frame2.png")
        kf1.image_path.write_bytes(b"fake png 1")
        kf2.image_path.write_bytes(b"fake png 2")
        return [kf1, kf2]

    def test_image_mode_uses_index_markers(self, tmp_path):
        """Codex transcript uses [KEYFRAME N] not [KEYFRAME: path]."""
        from app.services.llm.prompt import _build_codex_transcript
        transcript = self._make_transcript()
        keyframes = self._make_keyframes(tmp_path)
        result, sorted_kf = _build_codex_transcript(
            transcript, keyframes, mode=KeyframeMode.IMAGE,
        )
        assert "[KEYFRAME 1]" in result
        assert "[KEYFRAME 2]" in result
        assert "[KEYFRAME: " not in result
        logger.info("Codex transcript:\n%s", result)

    def test_index_order_matches_sorted_timestamp(self, tmp_path):
        """KEYFRAME index 1 corresponds to the earliest timestamp keyframe."""
        from app.services.llm.prompt import _build_codex_transcript
        transcript = self._make_transcript()
        keyframes = self._make_keyframes(tmp_path)
        result, sorted_kf = _build_codex_transcript(
            transcript, keyframes, mode=KeyframeMode.IMAGE,
        )
        assert len(sorted_kf) == 2
        assert sorted_kf[0].timestamp == 3.0  # earliest = index 1
        assert sorted_kf[1].timestamp == 12.0  # later = index 2

    def test_text_only_mode_returns_empty_list(self, tmp_path):
        """Non-image modes return empty list for keyframe list (no -i flags needed)."""
        from app.services.llm.prompt import _build_codex_transcript
        transcript = self._make_transcript()
        keyframes = self._make_keyframes(tmp_path)
        result, sorted_kf = _build_codex_transcript(
            transcript, keyframes, mode=KeyframeMode.OCR_INLINE,
        )
        assert sorted_kf == []
        # Should fall back to regular interleaved transcript
        assert "<transcript>" in result


class TestSupportedModes:
    """Each backend returns correct set[KeyframeMode] from supported_modes() (D-05)."""

    def test_claude_backend_supports_all_modes(self):
        from app.services.llm.claude import ClaudeBackend
        modes = ClaudeBackend().supported_modes()
        assert len(modes) == 6
        assert KeyframeMode.IMAGE in modes
        assert KeyframeMode.NONE in modes
        logger.info("ClaudeBackend modes: %s", modes)

    def test_codex_backend_supports_all_modes(self):
        from app.services.llm.codex import CodexBackend
        modes = CodexBackend().supported_modes()
        assert len(modes) == 6

    def test_litellm_backend_supports_all_modes(self):
        from app.services.llm.litellm import LiteLLMBackend
        modes = LiteLLMBackend().supported_modes()
        assert len(modes) == 6


@pytest.mark.asyncio
async def test_auth_status_all_backends():
    """All three auth endpoints return a dict with expected keys (D-16/D-17)."""
    from app.services.llm.claude import ClaudeBackend
    claude_s = await ClaudeBackend().auth_status()
    assert "loggedIn" in claude_s or "configured" in claude_s
    assert "cli_error" in claude_s
    logger.info("Auth status claude=%s", claude_s)

    try:
        from app.services.llm.codex import CodexBackend
        codex_s = await CodexBackend().auth_status()
        assert "loggedIn" in codex_s
        logger.info("Auth status codex=%s", codex_s)
    except ImportError:
        pytest.skip("CodexBackend not yet implemented (Plan 03)")

    try:
        from app.services.llm.litellm import LiteLLMBackend
        litellm_s = await LiteLLMBackend().auth_status()
        assert "configured" in litellm_s
        logger.info("Auth status litellm=%s", litellm_s)
    except ImportError:
        pytest.skip("LiteLLMBackend not yet implemented (Plan 04)")


@pytest.mark.asyncio
async def test_codex_backend_summarize():
    """CodexBackend produces SummaryResult (skipped if not logged in to Codex)."""
    from app.services.llm.codex import CodexBackend
    status = await CodexBackend().auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Codex — run `codex login` first")
    # Minimal integration: one sentence transcript, no keyframes
    from app.services.transcript import TranscriptResult, Segment
    transcript = TranscriptResult(
        text="This is a short test video about Python programming.",
        segments=[Segment(start=0.0, end=5.0, text="This is a short test video about Python programming.")],
        source="captions",
    )
    result = await CodexBackend().summarize(
        transcript=transcript,
        keyframes=[],
        video_meta={"title": "Test Video", "channel": "Test Channel", "duration": 5},
        keyframe_mode=KeyframeMode.NONE,
    )
    assert isinstance(result, SummaryResult)
    assert result.title or result.summary  # At least one non-empty field
    logger.info("Codex summary: title=%r tldr=%r", result.title, result.tldr)


class TestCodexBackendUnit:
    """CodexBackend unit tests — no live Codex invocation required."""

    def test_supported_modes_all_six(self):
        from app.services.llm.codex import CodexBackend
        modes = CodexBackend().supported_modes()
        assert len(modes) == 6
        assert KeyframeMode.IMAGE in modes
        assert KeyframeMode.NONE in modes

    @pytest.mark.asyncio
    async def test_auth_status_returns_dict(self):
        """auth_status() returns dict with loggedIn and cli_error keys."""
        from app.services.llm.codex import CodexBackend
        status = await CodexBackend().auth_status()
        assert isinstance(status, dict)
        assert "loggedIn" in status
        assert "cli_error" in status
        logger.info("CodexBackend auth_status: %s", status)

    def test_ensure_schema_file_creates_json(self, tmp_path, monkeypatch):
        """_ensure_schema_file writes schema with additionalProperties: false."""
        from app.services.llm import codex as codex_mod
        import app.config as config_mod
        schema_path = tmp_path / "codex_schema.json"
        # Patch both the schema path and DATA_DIR in the codex module's namespace
        # so tempfile.mkstemp uses the same filesystem as the target (no cross-device rename)
        monkeypatch.setattr(codex_mod, "CODEX_SCHEMA_PATH", schema_path)
        monkeypatch.setattr(codex_mod, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
        result = codex_mod._ensure_schema_file()
        assert result.exists()
        schema = json.loads(result.read_text())
        assert schema["additionalProperties"] is False
        assert "title" in schema["properties"]
        assert "tldr" in schema["properties"]
        assert "summary" in schema["properties"]
        logger.info("Schema: %s", schema)

    def test_codex_in_list_backends(self):
        from app.services.llm import list_backends
        backends = list_backends()
        assert "codex" in backends
        logger.info("list_backends: %s", backends)


class TestLiteLLMBackendUnit:
    """LiteLLMBackend unit tests — no real API calls."""

    def test_supported_modes_all_six(self):
        from app.services.llm.litellm import LiteLLMBackend
        modes = LiteLLMBackend().supported_modes()
        assert len(modes) == 6
        assert KeyframeMode.IMAGE in modes
        assert KeyframeMode.NONE in modes

    @pytest.mark.asyncio
    async def test_auth_status_returns_configured_false_when_no_key(self, tmp_path, monkeypatch):
        """auth_status returns configured=False when no API key is set."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": None, "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        from app.services.llm.litellm import LiteLLMBackend
        status = await LiteLLMBackend().auth_status()
        assert isinstance(status, dict)
        assert "configured" in status
        assert status["configured"] is False
        assert status["cli_error"] is False
        logger.info("LiteLLM auth_status (no key): %s", status)

    @pytest.mark.asyncio
    async def test_auth_status_returns_configured_true_when_key_set(self, tmp_path, monkeypatch):
        """auth_status returns configured=True when a real (unmasked) API key is stored."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": "sk-realkeyabcdefghij", "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        from app.services.llm.litellm import LiteLLMBackend
        status = await LiteLLMBackend().auth_status()
        assert status["configured"] is True
        assert status["cli_error"] is False
        logger.info("LiteLLM auth_status (key set): %s", status)

    @pytest.mark.asyncio
    async def test_auth_status_masked_key_is_not_configured(self, tmp_path, monkeypatch):
        """A masked API key (starting with '...') is treated as not configured."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": "...ghij", "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        from app.services.llm.litellm import LiteLLMBackend
        status = await LiteLLMBackend().auth_status()
        # Masked value "...ghij" is NOT a real key — configured should be False
        assert status["configured"] is False
        logger.info("LiteLLM auth_status (masked key): %s", status)

    @pytest.mark.asyncio
    async def test_summarize_with_mocked_acompletion(self, tmp_path, monkeypatch):
        """LiteLLMBackend.summarize() returns SummaryResult with mocked acompletion."""
        import app.settings as settings_mod
        import app.services.llm.litellm as litellm_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": "sk-test", "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

        # Mock litellm.acompletion to return a fake response
        mock_response_text = json.dumps({
            "title": "Mock LiteLLM Title",
            "tldr": "Mock TL;DR.",
            "summary": "# Mock Summary\n\nMock content.",
        })

        class _MockChoice:
            class message:
                content = mock_response_text

        class _MockResponse:
            choices = [_MockChoice()]

        async def _mock_acompletion(**kwargs):
            return _MockResponse()

        monkeypatch.setattr(litellm_mod.litellm, "acompletion", _mock_acompletion)
        monkeypatch.setattr(litellm_mod.litellm, "supports_vision", lambda model: True)

        from app.services.transcript import TranscriptResult, Segment
        transcript = TranscriptResult(
            text="Test content.",
            segments=[Segment(start=0.0, end=5.0, text="Test content.")],
            source="captions",
        )
        result = await litellm_mod.LiteLLMBackend().summarize(
            transcript=transcript,
            keyframes=[],
            video_meta={"title": "Test", "channel": "Test Channel", "duration": 5},
            keyframe_mode=KeyframeMode.NONE,
        )
        assert isinstance(result, SummaryResult)
        assert result.title == "Mock LiteLLM Title"
        assert result.tldr == "Mock TL;DR."
        logger.info("Mocked LiteLLM result: title=%r", result.title)

    def test_litellm_in_list_backends(self):
        from app.services.llm import list_backends
        backends = list_backends()
        assert "litellm" in backends
        assert "claude" in backends
        assert "codex" in backends
        logger.info("list_backends: %s", backends)

    def test_ollama_provider_uses_ollama_chat_prefix(self):
        """Provider 'ollama' maps to 'ollama_chat' prefix in LiteLLM model string."""
        from app.services.llm.litellm import _PROVIDER_PREFIX
        assert _PROVIDER_PREFIX["ollama"] == "ollama_chat"
        logger.info("ollama prefix confirmed: %s", _PROVIDER_PREFIX["ollama"])

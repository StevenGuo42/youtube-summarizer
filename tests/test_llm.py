"""Tests for app.services.llm — unit tests (no network) and integration tests (require Claude auth)."""

import json
import logging

import pytest

from cli import _resolve_keyframe_mode
from app.database import init_db
from app.services.llm import (
    DEFAULT_PROMPT,
    KeyframeMode,
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

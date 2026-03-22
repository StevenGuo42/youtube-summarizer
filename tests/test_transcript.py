"""Tests for app.services.transcript — uses real YouTube API, requires network."""

import logging

import pytest

from app.config import TMP_DIR
from app.services.transcript import extract_transcript

logger = logging.getLogger(__name__)

# "Me at the zoo" — has auto-generated captions
TEST_VIDEO_ID = "jNQXAC9IVRw"


@pytest.mark.asyncio
async def test_extract_captions():
    """Test caption extraction from a video with available captions."""
    work_dir = TMP_DIR / "test_captions"
    work_dir.mkdir(exist_ok=True)

    result = await extract_transcript(TEST_VIDEO_ID, video_path=None, work_dir=work_dir)

    logger.info("Source: %s, segments: %d", result.source, len(result.segments))
    logger.info("First 200 chars: %s", result.text[:200])

    assert result.source == "captions"
    assert len(result.text) > 0
    assert len(result.segments) > 0
    assert all(s.start <= s.end for s in result.segments)


@pytest.mark.asyncio
async def test_whisper_fallback():
    """Test whisper transcription using a downloaded video."""
    from app.services.ytdlp import download_video

    work_dir = TMP_DIR / "test_whisper"
    work_dir.mkdir(exist_ok=True)

    video_path = await download_video(TEST_VIDEO_ID, work_dir)
    logger.info("Downloaded video to %s", video_path)

    # Pass a fake video_id that won't have captions to force whisper fallback
    result = await extract_transcript(
        "fake_no_captions", video_path=video_path, work_dir=work_dir
    )

    logger.info("Source: %s, segments: %d", result.source, len(result.segments))
    logger.info("First 200 chars: %s", result.text[:200])

    assert result.source == "whisper"
    assert len(result.text) > 0
    assert len(result.segments) > 0

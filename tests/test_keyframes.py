"""Tests for app.services.keyframes — requires ffmpeg and a test video."""

import logging

import pytest

from app.config import TMP_DIR
from app.services.keyframes import extract_keyframes

logger = logging.getLogger(__name__)

TEST_VIDEO_ID = "jNQXAC9IVRw"


@pytest.mark.asyncio
async def test_extract_keyframes():
    """Test keyframe extraction from a short video (19s, mostly static)."""
    from app.services.ytdlp import download_video

    work_dir = TMP_DIR / "test_keyframes"
    work_dir.mkdir(exist_ok=True)

    video_path = work_dir / f"{TEST_VIDEO_ID}.mp4"
    if not video_path.exists():
        video_path = await download_video(TEST_VIDEO_ID, work_dir)

    keyframes = await extract_keyframes(video_path, work_dir)

    logger.info("Extracted %d keyframes", len(keyframes))
    for kf in keyframes:
        logger.info("  t=%.2fs %s (%d bytes)", kf.timestamp, kf.image_path.name, kf.image_path.stat().st_size)

    assert len(keyframes) > 0
    assert all(kf.image_path.exists() for kf in keyframes)
    assert all(kf.timestamp >= 0 for kf in keyframes)

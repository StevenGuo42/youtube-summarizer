"""Tests for app.services.keyframes — requires ffmpeg and a test video."""

import logging

import pytest

from app.config import COOKIES_PATH, TMP_DIR
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


@pytest.mark.asyncio
async def test_members_only_keyframes(members_only_video_id):
    """Test keyframe extraction from a members-only video.

    Downloads video and extracts keyframes.
    Requires valid cookies with channel membership.
    """
    import asyncio

    from app.services.ytdlp import _base_opts

    if not COOKIES_PATH.exists():
        pytest.skip("No cookies.txt — cannot access members-only content")

    work_dir = TMP_DIR / "test_members_keyframes"
    work_dir.mkdir(exist_ok=True)

    video_path = work_dir / f"{members_only_video_id}.mp4"
    if not video_path.exists():
        def _download():
            import yt_dlp

            opts = {
                **_base_opts(),
                "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "merge_output_format": "mp4",
                "outtmpl": str(video_path),
                "download_ranges": yt_dlp.utils.download_range_func(None, [(0, 120)]),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    ydl.download([f"https://www.youtube.com/watch?v={members_only_video_id}"])
                except yt_dlp.utils.DownloadError as e:
                    if "members-only" in str(e) or "Join this channel" in str(e):
                        raise pytest.skip(
                            "Cannot download members-only video — cookies may be expired or lack membership"
                        ) from e
                    raise

        await asyncio.to_thread(_download)

    if not video_path.exists():
        pytest.skip("Video not available for download")

    logger.info("Video file: %s (%d bytes)", video_path.name, video_path.stat().st_size)

    keyframes = await extract_keyframes(video_path, work_dir)

    logger.info("Extracted %d keyframes", len(keyframes))
    for kf in keyframes:
        logger.info("  t=%.2fs %s (%d bytes)", kf.timestamp, kf.image_path.name, kf.image_path.stat().st_size)

    assert len(keyframes) > 0
    assert all(kf.image_path.exists() for kf in keyframes)
    assert all(kf.timestamp >= 0 for kf in keyframes)

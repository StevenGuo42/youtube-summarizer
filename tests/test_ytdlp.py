"""Tests for app.services.ytdlp — uses real YouTube API, requires network."""

import logging

import pytest

logger = logging.getLogger(__name__)

from app.services.ytdlp import (
    download_video,
    get_video_info,
    list_channel_videos,
    list_playlist_videos,
    search_channels,
)

# "Me at the zoo" — first YouTube video, 19 seconds
TEST_VIDEO_ID = "jNQXAC9IVRw"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"
TEST_CHANNEL_ID = "UC4QobU6STFB0P71PMvOGN5A"  # jawed (uploader of first YT video)
TEST_PLAYLIST_ID = "PL2F4AF82A41D0D2C6"


@pytest.mark.asyncio
async def test_get_video_info():
    info = await get_video_info(TEST_VIDEO_URL)
    logger.info("Video info: %s", info)
    assert info["id"] == TEST_VIDEO_ID
    assert info["title"] is not None
    assert info["duration"] is not None and info["duration"] > 0


@pytest.mark.asyncio
async def test_search_channels():
    results = await search_channels("jawed karim")
    logger.info("Found %d channels: %s", len(results), results)
    assert len(results) > 0
    assert all("id" in ch and "name" in ch for ch in results)


@pytest.mark.asyncio
async def test_list_channel_videos():
    videos = await list_channel_videos(TEST_CHANNEL_ID, page=1, per_page=5)
    logger.info("Channel videos: %d results", len(videos))
    assert isinstance(videos, list)
    assert len(videos) > 0
    assert all("id" in v and "title" in v for v in videos)


@pytest.mark.asyncio
async def test_list_playlist_videos():
    videos = await list_playlist_videos(TEST_PLAYLIST_ID)
    logger.info("Playlist videos: %d results", len(videos))
    assert isinstance(videos, list)
    assert len(videos) > 0
    assert all("id" in v for v in videos)


@pytest.mark.asyncio
async def test_download_video():
    from app.config import TMP_DIR

    output_dir = TMP_DIR / "test_download"
    output_dir.mkdir(exist_ok=True)
    video_path = await download_video(TEST_VIDEO_ID, output_dir)
    logger.info("Downloaded to %s (%d bytes)", video_path, video_path.stat().st_size if video_path.exists() else 0)
    assert video_path.exists()
    assert video_path.suffix == ".mp4"
    assert video_path.stat().st_size > 0

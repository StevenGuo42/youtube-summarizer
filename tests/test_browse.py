"""Tests for browse router — thin wrappers over ytdlp service.

Hits real YouTube API, requires network.
"""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

logger = logging.getLogger(__name__)

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
TEST_CHANNEL_ID = "UC4QobU6STFB0P71PMvOGN5A"
TEST_PLAYLIST_ID = "PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH"  # Coding Train — The Nature of Code


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_search_channels(client):
    resp = await client.get("/api/channel/search", params={"q": "jawed karim"})
    assert resp.status_code == 200
    results = resp.json()
    logger.info("Search results: %d channels", len(results))
    assert len(results) > 0
    assert all("id" in ch and "name" in ch for ch in results)


@pytest.mark.asyncio
async def test_channel_videos(client):
    resp = await client.get(f"/api/channel/{TEST_CHANNEL_ID}/videos", params={"page": 1, "per_page": 5})
    assert resp.status_code == 200
    videos = resp.json()
    logger.info("Channel videos: %d results", len(videos))
    assert len(videos) > 0
    assert all("id" in v and "title" in v for v in videos)


@pytest.mark.asyncio
async def test_playlist_videos(client):
    resp = await client.get(f"/api/playlist/{TEST_PLAYLIST_ID}/videos")
    assert resp.status_code == 200
    videos = resp.json()
    logger.info("Playlist videos: %d results", len(videos))
    assert len(videos) > 0
    assert all("id" in v for v in videos)


@pytest.mark.asyncio
async def test_video_info(client):
    resp = await client.get("/api/video/info", params={"url": TEST_VIDEO_URL})
    assert resp.status_code == 200
    info = resp.json()
    logger.info("Video info: id=%s title='%s' duration=%ss", info["id"], info["title"], info.get("duration"))
    assert info["id"] == "jNQXAC9IVRw"
    assert info["title"] is not None
    assert info["duration"] > 0

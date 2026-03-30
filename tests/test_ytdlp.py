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
    assert all(v.get("visibility") in ("public", "members_only") for v in videos)


@pytest.mark.asyncio
async def test_list_channel_videos_public():
    videos = await list_channel_videos(TEST_CHANNEL_ID, visibility="public", page=1, per_page=5)
    logger.info("Public channel videos: %d results", len(videos))
    assert isinstance(videos, list)
    # All returned videos should be public (members-only filtered out)
    assert all(v.get("visibility") == "public" for v in videos)


@pytest.mark.asyncio
async def test_list_channel_videos_date_range():
    # jawed's channel has videos from ~2005-2012
    videos = await list_channel_videos(
        TEST_CHANNEL_ID, page=1, per_page=5,
        date_from="20050101", date_to="20251231",
    )
    logger.info("Date-filtered channel videos: %d results", len(videos))
    assert isinstance(videos, list)
    assert len(videos) > 0
    # Verify upload_date is within range for entries that have it
    for v in videos:
        if v.get("upload_date"):
            assert v["upload_date"] >= "20050101"
            assert v["upload_date"] <= "20251231"


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


@pytest.mark.asyncio
async def test_members_only_video_info(members_only_video_id, members_only_url):
    """Test that we can fetch metadata for a members-only video (requires cookies)."""
    from app.config import COOKIES_PATH

    if not COOKIES_PATH.exists():
        pytest.skip("No cookies.txt — cannot access members-only content")

    info = await get_video_info(members_only_url)
    logger.info("Members-only video info: id=%s title=%s duration=%s", info["id"], info["title"], info["duration"])
    assert info["id"] == members_only_video_id
    assert info["title"] is not None


@pytest.mark.asyncio
async def test_members_only_download_starts(members_only_url):
    """Test that downloading a members-only video can start (cancels after first bytes).

    Requires valid cookies with active membership to the channel.
    """
    import asyncio

    from app.config import COOKIES_PATH, TMP_DIR, VIDEO_FORMAT
    from app.services.ytdlp import _base_opts

    if not COOKIES_PATH.exists():
        pytest.skip("No cookies.txt — cannot access members-only content")

    output_dir = TMP_DIR / "test_members_download"
    output_dir.mkdir(exist_ok=True)

    started = asyncio.Event()
    no_formats = False

    def _try_download():
        import yt_dlp

        nonlocal no_formats

        def progress_hook(d):
            if d["status"] in ("downloading", "finished"):
                started.set()
                if d["status"] == "downloading":
                    raise yt_dlp.utils.DownloadCancelled("Download started successfully, stopping early")

        opts = {
            **_base_opts(),
            "format": VIDEO_FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "progress_hooks": [progress_hook],
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                ydl.download([members_only_url])
            except yt_dlp.utils.DownloadCancelled:
                pass  # expected — we cancelled on purpose
            except yt_dlp.utils.DownloadError as e:
                if "Requested format is not available" in str(e):
                    no_formats = True
                else:
                    raise

    await asyncio.to_thread(_try_download)

    # Clean up partial files
    for f in output_dir.iterdir():
        f.unlink()
    if output_dir.exists():
        output_dir.rmdir()

    if no_formats:
        pytest.skip(
            "No playable formats — cookies may lack membership to this channel. "
            "Metadata was still accessible, so cookies are valid for YouTube auth."
        )

    logger.info("Members-only download started successfully")
    assert started.is_set(), "Download never started — cookies may be invalid"

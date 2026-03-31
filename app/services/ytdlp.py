import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from app.config import COOKIES_PATH, VIDEO_FORMAT

# yt-dlp modifies cookiefile in place (writes back rotated cookies from YouTube).
# Use a temp copy so the original stays untouched.
_tmp_cookies: Path | None = None


def _get_tmp_cookies() -> str | None:
    """Copy cookies to a temp file on first use. Returns temp path or None."""
    global _tmp_cookies
    if not COOKIES_PATH.exists():
        return None
    if _tmp_cookies and _tmp_cookies.exists():
        return str(_tmp_cookies)
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="yt_cookies_")
    import os
    os.close(fd)
    _tmp_cookies = Path(path)
    shutil.copy2(COOKIES_PATH, _tmp_cookies)
    return str(_tmp_cookies)


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {"enabled": True}},
    }
    cookies = _get_tmp_cookies()
    if cookies:
        opts["cookiefile"] = cookies
    return opts


async def download_video(video_id: str, output_dir: Path) -> Path:
    """Download a video at 720p max. Returns path to the downloaded file."""
    output_template = str(output_dir / "%(id)s.%(ext)s")
    opts = {
        **_base_opts(),
        "format": VIDEO_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": output_template,
    }

    def _download():
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            filename = ydl.prepare_filename(info)
            # merge_output_format forces .mp4
            return Path(filename).with_suffix(".mp4")

    return await asyncio.to_thread(_download)


async def get_video_info(url: str) -> dict:
    """Fetch metadata for a single video URL without downloading.

    For channel/playlist URLs, uses extract_flat to avoid enumerating all videos.
    """
    is_collection = any(p in url for p in ["/channel/", "/c/", "/@", "/playlist?"])
    opts = {
        **_base_opts(),
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    if is_collection:
        opts["extract_flat"] = True
        opts["playlist_items"] = "0"  # metadata only, no entries

    def _fetch():
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "id": info.get("id"),
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "channel_id": info.get("channel_id"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "upload_date": info.get("upload_date"),
                "view_count": info.get("view_count"),
                "description": info.get("description"),
            }

    return await asyncio.to_thread(_fetch)


async def search_channels(query: str) -> list[dict]:
    """Search YouTube channels by name."""
    opts = {
        **_base_opts(),
        "extract_flat": True,
        "playlist_items": "1-10",
    }

    def _search():
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            results = ydl.extract_info(f"ytsearch10:{query}", download=False)
            channels = {}
            for entry in results.get("entries", []):
                cid = entry.get("channel_id")
                if cid and cid not in channels:
                    channels[cid] = {
                        "id": cid,
                        "name": entry.get("channel") or entry.get("uploader"),
                        "url": f"https://www.youtube.com/channel/{cid}",
                    }
            return list(channels.values())

    return await asyncio.to_thread(_search)


async def list_channel_videos(
    channel_id: str, visibility: str = "all", page: int = 1, per_page: int = 20,
    *, date_from: str | None = None, date_to: str | None = None
) -> list[dict]:
    """List videos for a channel with visibility and date range filtering.

    visibility: "all" (default), "public", or "members_only"
    date_from/date_to: YYYYMMDD strings for date range filtering
    """
    if visibility == "members_only":
        url = f"https://www.youtube.com/channel/{channel_id}/membership"
    else:
        url = f"https://www.youtube.com/channel/{channel_id}/videos"

    end = page * per_page
    start = end - per_page + 1
    opts = {
        **_base_opts(),
        "extract_flat": True,
        "playlist_items": f"{start}-{end}",
    }

    def _list():
        import yt_dlp

        if date_from or date_to:
            opts["daterange"] = yt_dlp.utils.DateRange(date_from, date_to)

        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
            entries = result.get("entries", []) if result else []

            is_membership = visibility == "members_only"
            videos = [
                {
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "duration": e.get("duration"),
                    "thumbnail": e.get("thumbnails", [{}])[-1].get("url") if e.get("thumbnails") else None,
                    "upload_date": e.get("upload_date"),
                    "visibility": "members_only" if is_membership
                        else ("members_only" if e.get("availability") in ("subscriber_only", "premium_only")
                              else "public"),
                }
                for e in entries
                if e
            ]

            if visibility == "public":
                videos = [v for v in videos if v["visibility"] == "public"]

            return videos

    return await asyncio.to_thread(_list)


async def list_playlist_videos(playlist_id: str) -> list[dict]:
    """List all videos in a playlist."""
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    opts = {
        **_base_opts(),
        "extract_flat": True,
    }

    def _list():
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
            entries = result.get("entries", []) if result else []
            return [
                {
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "duration": e.get("duration"),
                    "thumbnail": e.get("thumbnails", [{}])[-1].get("url") if e.get("thumbnails") else None,
                    "upload_date": e.get("upload_date"),
                }
                for e in entries
                if e
            ]

    return await asyncio.to_thread(_list)

import asyncio
import json
from pathlib import Path

from app.config import COOKIES_PATH, VIDEO_FORMAT


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
    }
    if COOKIES_PATH.exists():
        opts["cookiefile"] = str(COOKIES_PATH)
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
    """Fetch metadata for a single video URL without downloading."""
    opts = {
        **_base_opts(),
        "skip_download": True,
    }

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
    channel_id: str, members_only: bool = False, page: int = 1, per_page: int = 20
) -> list[dict]:
    """List videos for a channel. Uses flat-playlist for speed."""
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    if members_only:
        url = f"https://www.youtube.com/channel/{channel_id}/membership"

    end = page * per_page
    start = end - per_page + 1
    opts = {
        **_base_opts(),
        "extract_flat": True,
        "playlist_items": f"{start}-{end}",
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

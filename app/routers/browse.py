from fastapi import APIRouter

from app.services.ytdlp import (
    get_video_info as _get_video_info,
    list_channel_videos as _list_channel_videos,
    list_playlist_videos as _list_playlist_videos,
    search_channels as _search_channels,
)

router = APIRouter()


@router.get("/channel/search")
async def search_channels(q: str):
    return await _search_channels(q)


@router.get("/channel/{channel_id}/videos")
async def channel_videos(channel_id: str, members_only: bool = False, page: int = 1, per_page: int = 20):
    return await _list_channel_videos(channel_id, members_only=members_only, page=page, per_page=per_page)


@router.get("/playlist/{playlist_id}/videos")
async def playlist_videos(playlist_id: str):
    return await _list_playlist_videos(playlist_id)


@router.get("/video/info")
async def video_info(url: str):
    return await _get_video_info(url)

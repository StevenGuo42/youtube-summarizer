import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.ytdlp import (
    fetch_video_date as _fetch_video_date,
    get_video_info as _get_video_info,
    list_channel_videos as _list_channel_videos,
    list_playlist_videos as _list_playlist_videos,
    search_channels as _search_channels,
)

router = APIRouter()


# Kept for v2: search-by-name is a planned future feature (no frontend consumer yet)
@router.get("/channel/search")
async def search_channels(q: str):
    return await _search_channels(q)


@router.get("/channel/{channel_id}/videos")
async def channel_videos(
    channel_id: str, visibility: str = "all", page: int = 1, per_page: int = 20,
):
    return await _list_channel_videos(
        channel_id, visibility=visibility, page=page, per_page=per_page,
    )


@router.get("/playlist/{playlist_id}/videos")
async def playlist_videos(playlist_id: str):
    return await _list_playlist_videos(playlist_id)


class VideoDatesRequest(BaseModel):
    video_ids: list[str]


@router.post("/video/dates")
async def video_dates(req: VideoDatesRequest):
    async def stream():
        for video_id in req.video_ids:
            try:
                date = await _fetch_video_date(video_id)
                yield json.dumps({"id": video_id, "upload_date": date}) + "\n"
            except Exception:
                yield json.dumps({"id": video_id, "upload_date": None}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.get("/video/info")
async def video_info(url: str):
    return await _get_video_info(url)

from fastapi import APIRouter

router = APIRouter()


@router.get("/channel/search")
async def search_channels(q: str):
    pass


@router.get("/channel/{channel_id}/videos")
async def channel_videos(channel_id: str, members_only: bool = False, page: int = 1, per_page: int = 20):
    pass


@router.get("/playlist/{playlist_id}/videos")
async def playlist_videos(playlist_id: str):
    pass


@router.get("/video/info")
async def video_info(url: str):
    pass

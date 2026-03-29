from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile

from app.config import COOKIES_PATH

router = APIRouter()


@router.post("/cookies")
async def upload_cookies(file: UploadFile):
    content = await file.read()
    COOKIES_PATH.write_bytes(content)
    return {"status": "ok"}


@router.delete("/cookies")
async def delete_cookies():
    if COOKIES_PATH.exists():
        COOKIES_PATH.unlink()
    return {"status": "ok"}


@router.get("/status")
async def auth_status():
    if not COOKIES_PATH.exists():
        return {"exists": False, "modified": None}
    mtime = COOKIES_PATH.stat().st_mtime
    modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {"exists": True, "modified": modified}

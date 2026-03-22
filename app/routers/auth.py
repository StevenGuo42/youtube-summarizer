from fastapi import APIRouter, UploadFile

router = APIRouter()


@router.post("/cookies")
async def upload_cookies(file: UploadFile):
    pass


@router.delete("/cookies")
async def delete_cookies():
    pass


@router.get("/status")
async def auth_status():
    pass

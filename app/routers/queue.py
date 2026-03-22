from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def add_to_queue(video_ids: list[str]):
    pass


@router.get("")
async def list_jobs():
    pass


@router.get("/{job_id}")
async def get_job(job_id: str):
    pass


@router.delete("/{job_id}")
async def cancel_job(job_id: str):
    pass

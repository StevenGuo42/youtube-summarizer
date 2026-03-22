from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def list_summaries():
    pass


@router.get("/{job_id}")
async def get_summary(job_id: str):
    pass


@router.delete("/{job_id}")
async def delete_summary(job_id: str):
    pass


@router.get("/{job_id}/export")
async def export_summary(job_id: str):
    pass

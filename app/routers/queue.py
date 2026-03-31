import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.queue.worker import cancel, enqueue
from app.services.ytdlp import get_video_info

router = APIRouter()


class QueueRequest(BaseModel):
    video_ids: list[str]
    dedup_mode: str = "regular"
    keyframe_mode: str = "image"
    custom_prompt: str | None = None
    custom_prompt_mode: str = "replace"


@router.post("")
async def add_to_queue(req: QueueRequest):
    """Add videos to the processing queue."""
    jobs = []
    for video_id in req.video_ids:
        job_id = str(uuid.uuid4())

        # Fetch video metadata
        try:
            info = await get_video_info(f"https://www.youtube.com/watch?v={video_id}")
        except Exception:
            info = {}

        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO jobs (id, video_id, title, channel, duration, thumbnail_url,
                                    dedup_mode, keyframe_mode, custom_prompt, custom_prompt_mode, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    job_id,
                    video_id,
                    info.get("title"),
                    info.get("channel"),
                    info.get("duration"),
                    info.get("thumbnail"),
                    req.dedup_mode,
                    req.keyframe_mode,
                    req.custom_prompt,
                    req.custom_prompt_mode,
                ),
            )
            await db.commit()
        finally:
            await db.close()

        await enqueue(job_id)
        jobs.append({"job_id": job_id, "video_id": video_id})

    return {"jobs": jobs}


@router.get("")
async def list_jobs():
    """List all jobs."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# Kept for v2: single-job detail view for future job detail page (no frontend consumer yet)
@router.get("/{job_id}")
async def get_job(job_id: str):
    """Get a single job's status."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row)


@router.delete("/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a pending or processing job."""
    success = await cancel(job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found or already completed")
    return {"status": "cancelled"}

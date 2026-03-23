import asyncio
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_cancelled: set[str] = set()


async def start_worker():
    """Start the background worker. Call from FastAPI lifespan."""
    global _worker_task
    _worker_task = asyncio.create_task(_worker_loop())
    logger.info("Worker started")

    # Re-queue any jobs left in 'pending' or 'processing' state from a previous run
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM jobs WHERE status IN ('pending', 'processing') ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        for row in rows:
            await _queue.put(row["id"])
            logger.info("Re-queued job %s", row["id"])
    finally:
        await db.close()


async def stop_worker():
    """Stop the background worker. Call from FastAPI lifespan."""
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    logger.info("Worker stopped")


async def enqueue(job_id: str):
    """Add a job to the processing queue."""
    await _queue.put(job_id)
    logger.info("Enqueued job %s (queue size: %d)", job_id, _queue.qsize())


async def cancel(job_id: str) -> bool:
    """Mark a job for cancellation."""
    _cancelled.add(job_id)
    # Update DB status
    db = await get_db()
    try:
        await db.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status IN ('pending', 'processing')",
            (job_id,),
        )
        await db.commit()
        changed = db.total_changes > 0
    finally:
        await db.close()
    if changed:
        logger.info("Cancelled job %s", job_id)
    return changed


def is_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled."""
    return job_id in _cancelled


async def _worker_loop():
    """Process jobs one at a time from the queue."""
    from app.services.pipeline import process_job

    while True:
        job_id = await _queue.get()
        try:
            if job_id in _cancelled:
                _cancelled.discard(job_id)
                logger.info("Skipping cancelled job %s", job_id)
                continue

            logger.info("Processing job %s", job_id)
            await process_job(job_id)
        except Exception:
            logger.exception("Worker error processing job %s", job_id)
        finally:
            _queue.task_done()

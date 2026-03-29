import asyncio
import logging

from app.database import get_db

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_cancelled: set[str] = set()


async def get_worker_settings() -> dict:
    """Read worker settings from database."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM worker_settings WHERE id = 1")
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"processing_mode": "sequential", "batch_size": 5}
    finally:
        await db.close()


async def _drain_queue(batch_size: int) -> list[str]:
    """Pull up to batch_size jobs from the queue.
    Blocks on the first item (waits for work), then non-blocking for the rest.
    Filters out cancelled jobs.
    """
    job_ids = []

    # Block on first item
    job_id = await _queue.get()
    if job_id not in _cancelled:
        job_ids.append(job_id)
    else:
        _cancelled.discard(job_id)
        logger.info("Skipping cancelled job %s", job_id)
    _queue.task_done()

    # Non-blocking for remaining
    while len(job_ids) < batch_size and not _queue.empty():
        try:
            job_id = _queue.get_nowait()
            if job_id not in _cancelled:
                job_ids.append(job_id)
            else:
                _cancelled.discard(job_id)
                logger.info("Skipping cancelled job %s", job_id)
            _queue.task_done()
        except asyncio.QueueEmpty:
            break

    return job_ids


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
    """Process jobs from the queue. Supports sequential and batch modes."""
    from app.services.pipeline import process_batch, process_job

    while True:
        try:
            settings = await get_worker_settings()

            if settings["processing_mode"] == "batch":
                job_ids = await _drain_queue(settings["batch_size"])
                if job_ids:
                    logger.info("Processing batch of %d jobs: %s", len(job_ids), job_ids)
                    await process_batch(job_ids)
            else:
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

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker loop error")

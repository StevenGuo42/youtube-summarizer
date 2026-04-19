"""Tests for the rerun endpoint."""

import asyncio
import logging

import pytest

from app.database import get_db, init_db

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def _ensure_db():
    await init_db()


async def _insert_job(job_id: str, video_id: str, status: str):
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO jobs (id, video_id, status, error, warnings)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, video_id, status, "some error" if status == "failed" else None,
             '["warning1"]' if status == "failed" else None),
        )
        await db.commit()
    finally:
        await db.close()


async def _get_job(job_id: str) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return dict(row) if row else {}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rerun_failed_job():
    """Rerunning a failed job resets status, error, current_step, and warnings."""
    from app.routers.queue import _reset_job_for_rerun

    await _insert_job("test-fail-1", "dQw4w9WgXcQ", "failed")
    result = await _reset_job_for_rerun("test-fail-1")
    assert result is True

    job = await _get_job("test-fail-1")
    logger.info("Rerun job: %s", job)
    assert job["status"] == "pending"
    assert job["error"] is None
    assert job["current_step"] is None
    assert job["warnings"] is None


@pytest.mark.asyncio
async def test_rerun_cancelled_job():
    """Rerunning a cancelled job resets status."""
    from app.routers.queue import _reset_job_for_rerun

    await _insert_job("test-cancel-1", "dQw4w9WgXcQ", "cancelled")
    result = await _reset_job_for_rerun("test-cancel-1")
    assert result is True

    job = await _get_job("test-cancel-1")
    assert job["status"] == "pending"


@pytest.mark.asyncio
async def test_rerun_done_job_rejected():
    """Cannot rerun a completed job."""
    from app.routers.queue import _reset_job_for_rerun

    await _insert_job("test-done-1", "dQw4w9WgXcQ", "done")
    result = await _reset_job_for_rerun("test-done-1")
    assert result is False

    job = await _get_job("test-done-1")
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_rerun_nonexistent_job():
    """Rerunning a nonexistent job returns False."""
    from app.routers.queue import _reset_job_for_rerun

    result = await _reset_job_for_rerun("does-not-exist")
    assert result is False

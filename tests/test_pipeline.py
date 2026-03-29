"""Tests for the complete pipeline — requires network, cookies, and Claude auth."""

import json
import logging
import uuid

import pytest

from app.database import get_db, init_db
from app.services.llm import get_auth_status

logger = logging.getLogger(__name__)

# "Me at the zoo" — short public video (19s) with auto-captions
TEST_VIDEO_ID = "jNQXAC9IVRw"


@pytest.fixture(autouse=True)
async def _ensure_db():
    await init_db()


@pytest.mark.asyncio
async def test_pipeline_public_video():
    """Test the full pipeline end-to-end with a short public video."""
    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    from app.services.pipeline import process_job

    # Create a job in the database
    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status)
               VALUES (?, ?, 'Me at the zoo', 'pending')""",
            (job_id, TEST_VIDEO_ID),
        )
        await db.commit()
    finally:
        await db.close()

    # Run the pipeline
    await process_job(job_id)

    # Check job status
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job = dict(await cursor.fetchone())

        cursor = await db.execute("SELECT * FROM summaries WHERE job_id = ?", (job_id,))
        summary = await cursor.fetchone()
    finally:
        await db.close()

    logger.info("Job status: %s", job["status"])
    assert job["status"] == "done"
    assert job["current_step"] is None

    assert summary is not None
    summary = dict(summary)
    logger.info("Transcript length: %d", len(summary["transcript"] or ""))
    logger.info("Raw response length: %d", len(summary["raw_response"] or ""))

    structured = json.loads(summary["structured_summary"])
    logger.info("Title: %s", structured["title"])
    logger.info("TLDR: %s", structured["tldr"])
    logger.info("Summary: %s", structured["summary"][:300])

    assert structured["title"]
    assert structured["tldr"]
    assert structured["summary"]


@pytest.mark.asyncio
async def test_db_schema_has_new_columns():
    """Verify jobs table has dedup_mode, keyframe_mode, warnings columns
    and worker_settings table exists."""
    db = await get_db()
    try:
        # Check jobs columns
        cursor = await db.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "dedup_mode" in columns
        assert "keyframe_mode" in columns
        assert "warnings" in columns

        # Check worker_settings table
        cursor = await db.execute("PRAGMA table_info(worker_settings)")
        ws_columns = {row[1] for row in await cursor.fetchall()}
        assert "processing_mode" in ws_columns
        assert "batch_size" in ws_columns
    finally:
        await db.close()

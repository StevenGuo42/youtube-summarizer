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
async def test_pipeline_add_warning():
    """Test that warnings are appended to a job."""
    from app.services.pipeline import _add_warning

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status)
               VALUES (?, ?, 'Test', 'pending')""",
            (job_id, "test123"),
        )
        await db.commit()
    finally:
        await db.close()

    await _add_warning(job_id, "First warning")
    await _add_warning(job_id, "Second warning")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT warnings FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()

    warnings = json.loads(row["warnings"])
    assert warnings == ["First warning", "Second warning"]


@pytest.mark.asyncio
async def test_pipeline_reads_job_modes():
    """Test that process_job reads dedup_mode and keyframe_mode from the job row."""
    from unittest.mock import AsyncMock, patch

    from app.services.pipeline import process_job

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status, dedup_mode, keyframe_mode)
               VALUES (?, ?, 'Test', 'pending', 'slides', 'ocr-inline')""",
            (job_id, "jNQXAC9IVRw"),
        )
        await db.commit()
    finally:
        await db.close()

    # Mock all service calls to verify the pipeline wires them correctly
    with patch("app.services.pipeline.download_video", new_callable=AsyncMock) as mock_dl, \
         patch("app.services.pipeline.extract_transcript", new_callable=AsyncMock) as mock_tr, \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.deduplicate_keyframes") as mock_dedup, \
         patch("app.services.pipeline.extract_text", new_callable=AsyncMock) as mock_ocr, \
         patch("app.services.pipeline.save_ocr_results") as mock_save_ocr, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum:

        from unittest.mock import MagicMock
        from app.services.keyframes import KeyFrame
        from app.services.ocr import OcrResult
        from app.services.transcript import TranscriptResult, Segment
        from app.services.llm import SummaryResult
        from pathlib import Path

        fake_video = MagicMock(spec=Path)
        fake_video.exists.return_value = True
        mock_dl.return_value = fake_video
        mock_tr.return_value = TranscriptResult(
            text="hello", segments=[Segment(start=0, end=1, text="hello")], source="captions",
        )
        mock_kf.return_value = [KeyFrame(timestamp=0.5, image_path=Path("/tmp/frame.png"))]
        mock_dedup.return_value = (
            [KeyFrame(timestamp=0.5, image_path=Path("/tmp/frame.png"))],
            None,
        )
        mock_ocr.return_value = [OcrResult(timestamp=0.5, image_path=Path("/tmp/frame.png"), text="screen text")]
        mock_save_ocr.return_value = [Path("/tmp/ocr.txt")]
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

        await process_job(job_id)

    # Verify dedup was called with mode="slides"
    mock_dedup.assert_called_once()
    assert mock_dedup.call_args.kwargs["mode"] == "slides"

    # Verify summarize was called with keyframe_mode=OCR_INLINE
    mock_sum.assert_called_once()
    assert mock_sum.call_args.kwargs["keyframe_mode"].value == "ocr-inline"

    # Verify job completed
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_process_batch_runs_steps_across_jobs():
    """Test that process_batch runs each step for all jobs before moving to next step."""
    from unittest.mock import AsyncMock, patch

    from app.services.pipeline import process_batch

    job_ids = []
    for i in range(3):
        jid = str(uuid.uuid4())
        job_ids.append(jid)
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO jobs (id, video_id, title, status, dedup_mode, keyframe_mode)
                   VALUES (?, ?, ?, 'pending', 'regular', 'image')""",
                (jid, f"vid{i}", f"Video {i}"),
            )
            await db.commit()
        finally:
            await db.close()

    step_order = []

    async def track_download(vid, wdir):
        step_order.append(("download", vid))
        return wdir / "fake.mp4"

    async def track_transcript(vid, vpath, wdir, **kwargs):
        step_order.append(("transcript", vid))
        return TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")

    async def track_keyframes(vpath, wdir):
        step_order.append(("keyframes", str(vpath)))
        return []

    from app.services.transcript import TranscriptResult, Segment

    with patch("app.services.pipeline.download_video", side_effect=track_download), \
         patch("app.services.pipeline.extract_transcript", side_effect=track_transcript), \
         patch("app.services.pipeline.extract_keyframes", side_effect=track_keyframes), \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum, \
         patch("app.services.pipeline._cleanup"):

        from app.services.llm import SummaryResult
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

        await process_batch(job_ids)

    # Verify step ordering: all downloads before all transcripts before all keyframes
    download_indices = [i for i, (step, _) in enumerate(step_order) if step == "download"]
    transcript_indices = [i for i, (step, _) in enumerate(step_order) if step == "transcript"]
    keyframe_indices = [i for i, (step, _) in enumerate(step_order) if step == "keyframes"]

    if download_indices and transcript_indices:
        assert max(download_indices) < min(transcript_indices)
    if transcript_indices and keyframe_indices:
        assert max(transcript_indices) < min(keyframe_indices)

    # Verify all jobs completed
    for jid in job_ids:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT status FROM jobs WHERE id = ?", (jid,))
            row = await cursor.fetchone()
        finally:
            await db.close()
        assert row["status"] == "done"


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

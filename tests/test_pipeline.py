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
    logger.info("Warnings stored: %s", warnings)
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
    dedup_mode_used = mock_dedup.call_args.kwargs["mode"]
    assert dedup_mode_used == "slides"
    logger.info("Dedup called with mode=%s", dedup_mode_used)

    # Verify summarize was called with keyframe_mode=OCR_INLINE
    mock_sum.assert_called_once()
    kf_mode_used = mock_sum.call_args.kwargs["keyframe_mode"]
    assert kf_mode_used.value == "ocr-inline"
    logger.info("Summarize called with keyframe_mode=%s", kf_mode_used.value)

    # Verify job completed
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    logger.info("Job status: %s", row["status"])
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

    logger.info("Step execution order: %s", [s for s, _ in step_order])

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
        logger.info("Job %s status: %s", jid[:8], row["status"])
        assert row["status"] == "done"


@pytest.mark.asyncio
async def test_worker_drain_queue():
    """Test that _drain_queue pulls up to batch_size items."""
    from app.queue.worker import _queue, _drain_queue

    # Clear queue
    while not _queue.empty():
        _queue.get_nowait()

    # Add 7 items
    for i in range(7):
        await _queue.put(f"job-{i}")

    result = await _drain_queue(5)
    logger.info("Drained %d jobs (batch_size=5), %d remaining in queue", len(result), _queue.qsize())
    assert len(result) == 5
    assert result == [f"job-{i}" for i in range(5)]
    assert _queue.qsize() == 2


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

    logger.info("Jobs columns: %s", sorted(columns))
    logger.info("Worker settings columns: %s", sorted(ws_columns))


@pytest.mark.asyncio
async def test_pipeline_passes_llm_settings():
    """Test that process_job passes custom_prompt and model from llm_settings to summarize."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from pathlib import Path

    from app.services.pipeline import process_job
    from app.services.keyframes import KeyFrame
    from app.services.transcript import TranscriptResult, Segment
    from app.services.llm import SummaryResult

    # Set LLM settings
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO llm_settings (id, model, custom_prompt) VALUES (1, ?, ?)",
            ("claude-opus-4-20250514", "Be very concise"),
        )
        await db.commit()
    finally:
        await db.close()

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO jobs (id, video_id, title, status) VALUES (?, ?, 'Test', 'pending')",
            (job_id, "test_vid"),
        )
        await db.commit()
    finally:
        await db.close()

    with patch("app.services.pipeline.download_video", new_callable=AsyncMock) as mock_dl, \
         patch("app.services.pipeline.extract_transcript", new_callable=AsyncMock) as mock_tr, \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.deduplicate_keyframes") as mock_dedup, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum:

        fake_video = MagicMock(spec=Path)
        fake_video.exists.return_value = True
        mock_dl.return_value = fake_video
        mock_tr.return_value = TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")
        mock_kf.return_value = [KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))]
        mock_dedup.return_value = ([KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))], None)
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}', title="T", tldr="TL", summary="S",
        )

        await process_job(job_id)

    prompt_used = mock_sum.call_args.kwargs["custom_prompt"]
    model_used = mock_sum.call_args.kwargs["model"]
    logger.info("Summarize received: model=%s, custom_prompt='%s'", model_used, prompt_used)
    assert prompt_used == "Be very concise"
    assert model_used == "claude-opus-4-20250514"

    # Clean up to avoid leaking into other tests
    db = await get_db()
    try:
        await db.execute("DELETE FROM llm_settings WHERE id = 1")
        await db.commit()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pipeline_ocr_dedup_mode():
    """Test OCR-first dedup path: OCR all frames first, then dedup by text similarity."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from pathlib import Path

    from app.services.pipeline import process_job
    from app.services.keyframes import KeyFrame
    from app.services.ocr import OcrResult
    from app.services.transcript import TranscriptResult, Segment
    from app.services.llm import SummaryResult

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status, dedup_mode, keyframe_mode)
               VALUES (?, ?, 'Test', 'pending', 'ocr', 'ocr-inline')""",
            (job_id, "test_vid"),
        )
        await db.commit()
    finally:
        await db.close()

    kf1 = KeyFrame(timestamp=1.0, image_path=Path("/tmp/f1.png"))
    kf2 = KeyFrame(timestamp=2.0, image_path=Path("/tmp/f2.png"))
    kf3 = KeyFrame(timestamp=3.0, image_path=Path("/tmp/f3.png"))
    all_kf = [kf1, kf2, kf3]

    ocr1 = OcrResult(timestamp=1.0, image_path=Path("/tmp/f1.png"), text="Hello world")
    ocr2 = OcrResult(timestamp=2.0, image_path=Path("/tmp/f2.png"), text="Hello world")
    ocr3 = OcrResult(timestamp=3.0, image_path=Path("/tmp/f3.png"), text="Different text")
    all_ocr = [ocr1, ocr2, ocr3]
    deduped_kf = [kf2, kf3]
    deduped_ocr = [ocr2, ocr3]

    with patch("app.services.pipeline.download_video", new_callable=AsyncMock) as mock_dl, \
         patch("app.services.pipeline.extract_transcript", new_callable=AsyncMock) as mock_tr, \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.deduplicate_keyframes") as mock_dedup, \
         patch("app.services.pipeline.extract_text", new_callable=AsyncMock) as mock_ocr, \
         patch("app.services.pipeline.save_ocr_results") as mock_save_ocr, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum:

        fake_video = MagicMock(spec=Path)
        fake_video.exists.return_value = True
        mock_dl.return_value = fake_video
        mock_tr.return_value = TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")
        mock_kf.return_value = all_kf
        mock_ocr.return_value = all_ocr
        mock_save_ocr.return_value = [Path("/tmp/o1.txt"), Path("/tmp/o2.txt")]
        mock_dedup.return_value = (deduped_kf, deduped_ocr)
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}', title="T", tldr="TL", summary="S",
        )

        await process_job(job_id)

    # OCR was called on ALL keyframes (before dedup)
    mock_ocr.assert_called_once()
    ocr_input_count = len(mock_ocr.call_args[0][0])
    assert ocr_input_count == 3
    logger.info("OCR called on %d keyframes (all, before dedup)", ocr_input_count)

    # Dedup was called with mode="ocr" and received OCR results
    mock_dedup.assert_called_once()
    assert mock_dedup.call_args.kwargs["mode"] == "ocr"
    assert mock_dedup.call_args.kwargs["ocr_results"] == all_ocr
    logger.info("Dedup called with mode=ocr, %d OCR results passed", len(all_ocr))

    # save_ocr_results called twice: after OCR, then again after dedup
    assert mock_save_ocr.call_count == 2
    logger.info("save_ocr_results called %d times (after OCR + after dedup)", mock_save_ocr.call_count)

    # Job completed
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    logger.info("Job status: %s", row["status"])
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_pipeline_null_modes_use_defaults():
    """Test that NULL dedup_mode/keyframe_mode default to regular/image."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from pathlib import Path

    from app.services.pipeline import process_job
    from app.services.keyframes import KeyFrame
    from app.services.transcript import TranscriptResult, Segment
    from app.services.llm import SummaryResult

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status, dedup_mode, keyframe_mode)
               VALUES (?, ?, 'Test', 'pending', NULL, NULL)""",
            (job_id, "test_vid"),
        )
        await db.commit()
    finally:
        await db.close()

    with patch("app.services.pipeline.download_video", new_callable=AsyncMock) as mock_dl, \
         patch("app.services.pipeline.extract_transcript", new_callable=AsyncMock) as mock_tr, \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.deduplicate_keyframes") as mock_dedup, \
         patch("app.services.pipeline.extract_text", new_callable=AsyncMock) as mock_ocr, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum:

        fake_video = MagicMock(spec=Path)
        fake_video.exists.return_value = True
        mock_dl.return_value = fake_video
        mock_tr.return_value = TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")
        mock_kf.return_value = [KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))]
        mock_dedup.return_value = ([KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))], None)
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}', title="T", tldr="TL", summary="S",
        )

        await process_job(job_id)

    # Dedup used "regular" mode (default for NULL)
    mock_dedup.assert_called_once()
    dedup_mode_used = mock_dedup.call_args.kwargs["mode"]
    assert dedup_mode_used == "regular"
    logger.info("NULL dedup_mode defaulted to: %s", dedup_mode_used)

    # OCR was NOT called ("image" mode doesn't need OCR)
    mock_ocr.assert_not_called()
    logger.info("OCR skipped (image mode does not require OCR)")

    # Summarize used IMAGE mode (default for NULL)
    mock_sum.assert_called_once()
    kf_mode_used = mock_sum.call_args.kwargs["keyframe_mode"]
    assert kf_mode_used.value == "image"
    logger.info("NULL keyframe_mode defaulted to: %s", kf_mode_used.value)


@pytest.mark.asyncio
async def test_pipeline_ocr_failure_falls_back_to_image():
    """Test that OCR failure falls back to image-only mode with a warning."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from pathlib import Path

    from app.services.pipeline import process_job
    from app.services.keyframes import KeyFrame
    from app.services.transcript import TranscriptResult, Segment
    from app.services.llm import SummaryResult

    job_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO jobs (id, video_id, title, status, dedup_mode, keyframe_mode)
               VALUES (?, ?, 'Test', 'pending', 'regular', 'ocr-inline')""",
            (job_id, "test_vid"),
        )
        await db.commit()
    finally:
        await db.close()

    with patch("app.services.pipeline.download_video", new_callable=AsyncMock) as mock_dl, \
         patch("app.services.pipeline.extract_transcript", new_callable=AsyncMock) as mock_tr, \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.deduplicate_keyframes") as mock_dedup, \
         patch("app.services.pipeline.extract_text", new_callable=AsyncMock) as mock_ocr, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum:

        fake_video = MagicMock(spec=Path)
        fake_video.exists.return_value = True
        mock_dl.return_value = fake_video
        mock_tr.return_value = TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")
        mock_kf.return_value = [KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))]
        mock_dedup.return_value = ([KeyFrame(timestamp=0.5, image_path=Path("/tmp/f.png"))], None)
        mock_ocr.side_effect = RuntimeError("GPU out of memory")
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}', title="T", tldr="TL", summary="S",
        )

        await process_job(job_id)

    # Summarize fell back to IMAGE mode
    mock_sum.assert_called_once()
    kf_mode_used = mock_sum.call_args.kwargs["keyframe_mode"]
    assert kf_mode_used.value == "image"
    logger.info("OCR failed -> summarize fell back to keyframe_mode=%s", kf_mode_used.value)

    # Job completed with warning
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status, warnings FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    assert row["status"] == "done"
    warnings = json.loads(row["warnings"])
    logger.info("Job status: %s, warnings: %s", row["status"], warnings)
    assert any("OCR failed" in w for w in warnings)


@pytest.mark.asyncio
async def test_batch_partial_failure():
    """Test that one job failing in a batch doesn't block others."""
    from unittest.mock import AsyncMock, patch

    from app.services.pipeline import process_batch
    from app.services.transcript import TranscriptResult, Segment
    from app.services.llm import SummaryResult

    job_ids = []
    for i in range(3):
        jid = str(uuid.uuid4())
        job_ids.append(jid)
        vid = "fail_vid" if i == 0 else f"ok_vid_{i}"
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO jobs (id, video_id, title, status) VALUES (?, ?, ?, 'pending')",
                (jid, vid, f"Video {i}"),
            )
            await db.commit()
        finally:
            await db.close()

    async def selective_download(vid, wdir):
        if vid == "fail_vid":
            raise RuntimeError("Download failed")
        return wdir / "fake.mp4"

    async def selective_transcript(vid, vpath, wdir, **kwargs):
        if vid == "fail_vid":
            raise RuntimeError("No captions available")
        return TranscriptResult(text="hi", segments=[Segment(0, 1, "hi")], source="captions")

    with patch("app.services.pipeline.download_video", side_effect=selective_download), \
         patch("app.services.pipeline.extract_transcript", side_effect=selective_transcript), \
         patch("app.services.pipeline.extract_keyframes", new_callable=AsyncMock) as mock_kf, \
         patch("app.services.pipeline.summarize", new_callable=AsyncMock) as mock_sum, \
         patch("app.services.pipeline._cleanup"):

        mock_kf.return_value = []
        mock_sum.return_value = SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

        await process_batch(job_ids)

    # First job failed (no download + no transcript = nothing to work with)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT status, error FROM jobs WHERE id = ?", (job_ids[0],))
        row = await cursor.fetchone()
    finally:
        await db.close()
    logger.info("Job 0 (fail_vid): status=%s, error=%s", row["status"], row["error"])
    assert row["status"] == "failed"
    assert "Both transcript and keyframe" in row["error"]

    # Other jobs completed successfully
    for i, jid in enumerate(job_ids[1:], 1):
        db = await get_db()
        try:
            cursor = await db.execute("SELECT status FROM jobs WHERE id = ?", (jid,))
            row = await cursor.fetchone()
        finally:
            await db.close()
        logger.info("Job %d (ok_vid_%d): status=%s", i, i, row["status"])
        assert row["status"] == "done"

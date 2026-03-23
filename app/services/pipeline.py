import json
import logging
import shutil
import uuid
from pathlib import Path

from app.config import TMP_DIR
from app.database import get_db

logger = logging.getLogger(__name__)

STEPS = ["downloading", "transcribing", "extracting_keyframes", "summarizing", "cleanup"]


async def _update_job(job_id: str, **kwargs):
    """Update job fields in the database."""
    db = await get_db()
    try:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values())
        vals.append(job_id)
        await db.execute(
            f"UPDATE jobs SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            vals,
        )
        await db.commit()
    finally:
        await db.close()


async def process_job(job_id: str) -> None:
    """Run the full pipeline for a single video job."""
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job = await row.fetchone()
    finally:
        await db.close()

    if not job:
        logger.error("Job %s not found", job_id)
        return

    video_id = job["video_id"]
    work_dir = TMP_DIR / job_id
    work_dir.mkdir(exist_ok=True)

    transcript = None
    keyframes = []
    video_path = None

    try:
        # Step 1: Download
        await _update_job(job_id, status="processing", current_step="downloading")
        try:
            from app.services.ytdlp import download_video

            video_path = await download_video(video_id, work_dir)
            logger.info("[%s] Downloaded: %s", job_id, video_path)
        except Exception:
            logger.exception("[%s] Download failed", job_id)
            # Continue — transcript might still work via captions

        # Step 2: Transcript
        await _update_job(job_id, current_step="transcribing")
        try:
            from app.services.transcript import extract_transcript

            transcript = await extract_transcript(video_id, video_path, work_dir)
            logger.info("[%s] Transcript: %s, %d segments", job_id, transcript.source, len(transcript.segments))
        except Exception:
            logger.exception("[%s] Transcript extraction failed", job_id)

        # Step 3: Keyframes
        await _update_job(job_id, current_step="extracting_keyframes")
        if video_path and video_path.exists():
            try:
                from app.services.keyframes import extract_keyframes

                keyframes = await extract_keyframes(video_path, work_dir)
                logger.info("[%s] Keyframes: %d extracted", job_id, len(keyframes))
            except Exception:
                logger.exception("[%s] Keyframe extraction failed", job_id)
        else:
            logger.warning("[%s] No video file, skipping keyframes", job_id)

        # Check if we have anything to summarize
        if not transcript and not keyframes:
            await _update_job(job_id, status="failed", error="Both transcript and keyframe extraction failed")
            return

        # Step 4: Summarize
        await _update_job(job_id, current_step="summarizing")
        try:
            from app.services.llm import summarize
            from app.services.transcript import TranscriptResult

            # If transcript failed, create a minimal one
            if not transcript:
                transcript = TranscriptResult(text="[No transcript available]", segments=[], source="none")

            video_meta = {
                "title": job["title"] or "Unknown",
                "channel": job["channel"] or "Unknown",
                "duration": job["duration"],
            }

            result = await summarize(
                transcript=transcript,
                keyframes=keyframes,
                video_meta=video_meta,
            )
            logger.info("[%s] Summary generated: %d chars", job_id, len(result.raw_response))

            # Save to database
            summary_id = str(uuid.uuid4())
            structured = json.dumps({
                "title": result.title,
                "tldr": result.tldr,
                "summary": result.summary,
            }, ensure_ascii=False)

            db = await get_db()
            try:
                await db.execute(
                    """INSERT INTO summaries (id, job_id, transcript, raw_response, structured_summary)
                       VALUES (?, ?, ?, ?, ?)""",
                    (summary_id, job_id, transcript.text, result.raw_response, structured),
                )
                await db.commit()
            finally:
                await db.close()

        except Exception:
            logger.exception("[%s] Summarization failed", job_id)
            await _update_job(job_id, status="failed", error="Summarization failed")
            return

        # Step 5: Cleanup
        await _update_job(job_id, current_step="cleanup")
        _cleanup(work_dir)

        status = "done" if transcript and transcript.source != "none" else "partial"
        await _update_job(job_id, status=status, current_step=None)
        logger.info("[%s] Pipeline complete: %s", job_id, status)

    except Exception:
        logger.exception("[%s] Pipeline failed unexpectedly", job_id)
        await _update_job(job_id, status="failed", error="Unexpected pipeline error")
        _cleanup(work_dir)


def _cleanup(work_dir: Path):
    """Remove temporary working directory."""
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
            logger.info("Cleaned up %s", work_dir)
    except Exception:
        logger.exception("Failed to clean up %s", work_dir)

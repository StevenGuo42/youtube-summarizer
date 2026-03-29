import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import TMP_DIR
from app.database import get_db
from app.services.keyframes import KeyFrame, extract_keyframes, deduplicate_keyframes
from app.services.llm import summarize, KeyframeMode
from app.services.ocr import extract_text, save_ocr_results
from app.services.transcript import extract_transcript, TranscriptResult
from app.services.ytdlp import download_video

logger = logging.getLogger(__name__)

STEPS = ["downloading", "transcribing", "extracting_keyframes", "deduplicating", "ocr", "summarizing", "cleanup"]

# Keyframe modes that require OCR
_OCR_MODES = {"ocr", "ocr+image", "ocr-inline", "ocr-inline+image"}


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


async def _add_warning(job_id: str, warning: str):
    """Append a warning to the job's warnings JSON array."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT warnings FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        warnings = json.loads(row["warnings"]) if row and row["warnings"] else []
        warnings.append(warning)
        await db.execute(
            "UPDATE jobs SET warnings = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(warnings), job_id),
        )
        await db.commit()
    finally:
        await db.close()
    logger.warning("[%s] %s", job_id, warning)


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
    dedup_mode = job["dedup_mode"] or "regular"
    keyframe_mode_str = job["keyframe_mode"] or "image"
    work_dir = TMP_DIR / job_id
    work_dir.mkdir(exist_ok=True)

    transcript = None
    keyframes: list[KeyFrame] = []
    ocr_results = None
    ocr_paths = None
    video_path = None

    try:
        # Step 1: Download
        await _update_job(job_id, status="processing", current_step="downloading")
        try:
            video_path = await download_video(video_id, work_dir)
            logger.info("[%s] Downloaded: %s", job_id, video_path)
        except Exception:
            logger.exception("[%s] Download failed", job_id)
            await _add_warning(job_id, "Download failed, attempting transcript via captions")

        # Step 2: Transcript
        await _update_job(job_id, current_step="transcribing")
        try:
            transcript = await extract_transcript(video_id, video_path, work_dir)
            logger.info("[%s] Transcript: %s, %d segments", job_id, transcript.source, len(transcript.segments))
        except Exception:
            logger.exception("[%s] Transcript extraction failed", job_id)
            await _add_warning(job_id, "Transcript extraction failed, using keyframes only")

        # Step 3: Keyframes
        await _update_job(job_id, current_step="extracting_keyframes")
        if video_path and video_path.exists():
            try:
                keyframes = await extract_keyframes(video_path, work_dir)
                logger.info("[%s] Keyframes: %d extracted", job_id, len(keyframes))
            except Exception:
                logger.exception("[%s] Keyframe extraction failed", job_id)
                await _add_warning(job_id, "Keyframe extraction failed, using transcript only")
        else:
            logger.warning("[%s] No video file, skipping keyframes", job_id)
            if not transcript:
                await _update_job(job_id, status="failed", error="No video file and no transcript")
                return

        # Check if we have anything to summarize
        if not transcript and not keyframes:
            await _update_job(job_id, status="failed", error="Both transcript and keyframe extraction failed")
            return

        # Determine if OCR is needed
        needs_ocr = keyframe_mode_str in _OCR_MODES and bool(keyframes)

        # Handle dedup_mode=ocr special case: need OCR before dedup
        effective_dedup_mode = dedup_mode
        if dedup_mode == "ocr" and not needs_ocr:
            effective_dedup_mode = "regular"
            await _add_warning(job_id, "OCR dedup requested but keyframe mode doesn't use OCR, falling back to regular dedup")

        # Step 4/5: Dedup and OCR (order depends on dedup mode)
        if effective_dedup_mode == "ocr" and keyframes:
            # OCR first, then dedup by text
            await _update_job(job_id, current_step="ocr")
            try:
                ocr_results = await extract_text(keyframes)
                ocr_paths = save_ocr_results(ocr_results, work_dir)
                logger.info("[%s] OCR: %d results", job_id, len(ocr_results))
            except Exception:
                logger.exception("[%s] OCR failed", job_id)
                await _add_warning(job_id, "OCR failed, falling back to image-only mode")
                keyframe_mode_str = "image"
                needs_ocr = False

            await _update_job(job_id, current_step="deduplicating")
            try:
                keyframes, ocr_results = deduplicate_keyframes(
                    keyframes, ocr_results=ocr_results, mode="ocr",
                )
                if ocr_results:
                    ocr_paths = save_ocr_results(ocr_results, work_dir)
                logger.info("[%s] Dedup (ocr): %d keyframes remaining", job_id, len(keyframes))
            except Exception:
                logger.exception("[%s] Dedup failed", job_id)
                await _add_warning(job_id, "Dedup failed, using all keyframes")
        else:
            # Dedup first, then OCR on deduped frames
            await _update_job(job_id, current_step="deduplicating")
            if keyframes:
                try:
                    keyframes, _ = deduplicate_keyframes(keyframes, mode=effective_dedup_mode)
                    logger.info("[%s] Dedup (%s): %d keyframes remaining", job_id, effective_dedup_mode, len(keyframes))
                except Exception:
                    logger.exception("[%s] Dedup failed", job_id)
                    await _add_warning(job_id, "Dedup failed, using all keyframes")

            await _update_job(job_id, current_step="ocr")
            if needs_ocr and keyframes:
                try:
                    ocr_results = await extract_text(keyframes)
                    ocr_paths = save_ocr_results(ocr_results, work_dir)
                    logger.info("[%s] OCR: %d results", job_id, len(ocr_results))
                except Exception:
                    logger.exception("[%s] OCR failed", job_id)
                    await _add_warning(job_id, "OCR failed, falling back to image-only mode")
                    keyframe_mode_str = "image"

        # Step 6: Summarize
        await _update_job(job_id, current_step="summarizing")
        try:
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
                keyframe_mode=KeyframeMode(keyframe_mode_str),
                ocr_paths=ocr_paths,
                ocr_results=ocr_results,
            )
            logger.info("[%s] Summary generated: %d chars", job_id, len(result.raw_response))

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

        # Step 7: Cleanup
        await _update_job(job_id, current_step="cleanup")
        _cleanup(work_dir)

        await _update_job(job_id, status="done", current_step=None)
        logger.info("[%s] Pipeline complete", job_id)

    except Exception:
        logger.exception("[%s] Pipeline failed unexpectedly", job_id)
        await _update_job(job_id, status="failed", error="Unexpected pipeline error")
        _cleanup(work_dir)


@dataclass
class _BatchJob:
    """Tracks per-job state within a batch."""
    job_id: str
    video_id: str
    dedup_mode: str
    keyframe_mode_str: str
    work_dir: Path
    video_path: Path | None = None
    transcript: object = None  # TranscriptResult
    keyframes: list = field(default_factory=list)
    ocr_results: list | None = None
    ocr_paths: list | None = None
    failed: bool = False


def _active(batch: list[_BatchJob]) -> list[_BatchJob]:
    """Return non-failed jobs in the batch."""
    return [bj for bj in batch if not bj.failed]


async def process_batch(job_ids: list[str]) -> None:
    """Run the pipeline in batch mode: each step across all jobs before moving to next step."""
    # Load job data
    batch: list[_BatchJob] = []
    for job_id in job_ids:
        db = await get_db()
        try:
            row = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            job = await row.fetchone()
        finally:
            await db.close()

        if not job:
            logger.error("Job %s not found", job_id)
            continue

        work_dir = TMP_DIR / job_id
        work_dir.mkdir(exist_ok=True)
        batch.append(_BatchJob(
            job_id=job_id,
            video_id=job["video_id"],
            dedup_mode=job["dedup_mode"] or "regular",
            keyframe_mode_str=job["keyframe_mode"] or "image",
            work_dir=work_dir,
        ))

    if not batch:
        return

    # Step 1: Download all
    for bj in _active(batch):
        await _update_job(bj.job_id, status="processing", current_step="downloading")
        try:
            bj.video_path = await download_video(bj.video_id, bj.work_dir)
            logger.info("[%s] Downloaded: %s", bj.job_id, bj.video_path)
        except Exception:
            logger.exception("[%s] Download failed", bj.job_id)
            await _add_warning(bj.job_id, "Download failed, attempting transcript via captions")

    # Step 2: Transcribe all
    for bj in _active(batch):
        await _update_job(bj.job_id, current_step="transcribing")
        try:
            bj.transcript = await extract_transcript(bj.video_id, bj.video_path, bj.work_dir)
            logger.info("[%s] Transcript: %s, %d segments", bj.job_id, bj.transcript.source, len(bj.transcript.segments))
        except Exception:
            logger.exception("[%s] Transcript extraction failed", bj.job_id)
            await _add_warning(bj.job_id, "Transcript extraction failed, using keyframes only")

    # Step 3: Extract keyframes all
    for bj in _active(batch):
        await _update_job(bj.job_id, current_step="extracting_keyframes")
        if bj.video_path and bj.video_path.exists():
            try:
                bj.keyframes = await extract_keyframes(bj.video_path, bj.work_dir)
                logger.info("[%s] Keyframes: %d extracted", bj.job_id, len(bj.keyframes))
            except Exception:
                logger.exception("[%s] Keyframe extraction failed", bj.job_id)
                await _add_warning(bj.job_id, "Keyframe extraction failed, using transcript only")

        # Check if job has anything to work with
        if not bj.transcript and not bj.keyframes:
            await _update_job(bj.job_id, status="failed", error="Both transcript and keyframe extraction failed")
            bj.failed = True

    # Step 4/5: Dedup and OCR
    # Split batch by dedup strategy
    ocr_first = [bj for bj in _active(batch) if bj.dedup_mode == "ocr" and bj.keyframe_mode_str in _OCR_MODES and bj.keyframes]
    dedup_first = [bj for bj in _active(batch) if bj not in ocr_first]

    # Handle ocr-first jobs: OCR then dedup
    if ocr_first:
        for bj in ocr_first:
            await _update_job(bj.job_id, current_step="ocr")
            try:
                bj.ocr_results = await extract_text(bj.keyframes)
                bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
            except Exception:
                logger.exception("[%s] OCR failed", bj.job_id)
                await _add_warning(bj.job_id, "OCR failed, falling back to image-only mode")
                bj.keyframe_mode_str = "image"

        for bj in ocr_first:
            await _update_job(bj.job_id, current_step="deduplicating")
            try:
                bj.keyframes, bj.ocr_results = deduplicate_keyframes(
                    bj.keyframes, ocr_results=bj.ocr_results, mode="ocr",
                )
                if bj.ocr_results:
                    bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
            except Exception:
                logger.exception("[%s] Dedup failed", bj.job_id)
                await _add_warning(bj.job_id, "Dedup failed, using all keyframes")

    # Handle dedup-first jobs: dedup then OCR
    for bj in dedup_first:
        await _update_job(bj.job_id, current_step="deduplicating")
        if bj.keyframes:
            effective_mode = bj.dedup_mode
            if effective_mode == "ocr":
                effective_mode = "regular"
                await _add_warning(bj.job_id, "OCR dedup requested but keyframe mode doesn't use OCR, falling back to regular dedup")
            try:
                bj.keyframes, _ = deduplicate_keyframes(bj.keyframes, mode=effective_mode)
            except Exception:
                logger.exception("[%s] Dedup failed", bj.job_id)
                await _add_warning(bj.job_id, "Dedup failed, using all keyframes")

    needs_ocr = [bj for bj in dedup_first if bj.keyframe_mode_str in _OCR_MODES and bj.keyframes]
    if needs_ocr:
        for bj in needs_ocr:
            await _update_job(bj.job_id, current_step="ocr")
            try:
                bj.ocr_results = await extract_text(bj.keyframes)
                bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
            except Exception:
                logger.exception("[%s] OCR failed", bj.job_id)
                await _add_warning(bj.job_id, "OCR failed, falling back to image-only mode")
                bj.keyframe_mode_str = "image"

    # Step 6: Summarize all
    for bj in _active(batch):
        await _update_job(bj.job_id, current_step="summarizing")
        try:
            transcript = bj.transcript
            if not transcript:
                transcript = TranscriptResult(text="[No transcript available]", segments=[], source="none")

            db = await get_db()
            try:
                row = await db.execute("SELECT * FROM jobs WHERE id = ?", (bj.job_id,))
                job = await row.fetchone()
            finally:
                await db.close()

            video_meta = {
                "title": job["title"] or "Unknown",
                "channel": job["channel"] or "Unknown",
                "duration": job["duration"],
            }

            result = await summarize(
                transcript=transcript,
                keyframes=bj.keyframes,
                video_meta=video_meta,
                keyframe_mode=KeyframeMode(bj.keyframe_mode_str),
                ocr_paths=bj.ocr_paths,
                ocr_results=bj.ocr_results,
            )

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
                    (summary_id, bj.job_id, transcript.text, result.raw_response, structured),
                )
                await db.commit()
            finally:
                await db.close()

        except Exception:
            logger.exception("[%s] Summarization failed", bj.job_id)
            await _update_job(bj.job_id, status="failed", error="Summarization failed")
            bj.failed = True

    # Step 7: Cleanup all
    for bj in batch:
        if not bj.failed:
            await _update_job(bj.job_id, current_step="cleanup")
        _cleanup(bj.work_dir)
        if not bj.failed:
            await _update_job(bj.job_id, status="done", current_step=None)
            logger.info("[%s] Pipeline complete", bj.job_id)


def _cleanup(work_dir: Path):
    """Remove temporary working directory."""
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
            logger.info("Cleaned up %s", work_dir)
    except Exception:
        logger.exception("Failed to clean up %s", work_dir)

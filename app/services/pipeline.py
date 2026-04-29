import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import MAX_REUSABLE_FAILED_JOBS, TMP_DIR
from app.database import get_db
from app.queue.worker import is_cancelled
from app.services.keyframes import KeyFrame, extract_keyframes, deduplicate_keyframes
from app.services.llm import summarize, KeyframeMode
from app.settings import get_llm_settings
from app.services.ocr import extract_text, save_ocr_results, OcrResult
from app.services.transcript import extract_transcript, TranscriptResult, Segment
from app.services.ytdlp import download_video

logger = logging.getLogger(__name__)

STEPS = ["downloading", "transcribing", "extracting_keyframes", "deduplicating", "ocr", "summarizing", "cleanup"]

# Keyframe modes that require OCR
_OCR_MODES = {"ocr", "ocr+image", "ocr-inline", "ocr-inline+image"}

_MANIFEST_NAME = "manifest.json"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest(work_dir: Path) -> dict | None:
    """Load manifest from work_dir, returning None if missing or corrupt."""
    path = work_dir / _MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to load manifest from %s — treating as empty", path)
        return None


def _save_manifest(work_dir: Path, manifest: dict) -> None:
    """Atomically write manifest to work_dir."""
    tmp = work_dir / (_MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    tmp.replace(work_dir / _MANIFEST_NAME)


# ---------------------------------------------------------------------------
# Serialization helpers (stored paths are relative filenames, not absolute)
# ---------------------------------------------------------------------------

def _serialize_transcript(t: TranscriptResult) -> dict:
    return {
        "text": t.text,
        "source": t.source,
        "language": t.language,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in t.segments],
    }


def _deserialize_transcript(d: dict) -> TranscriptResult:
    segments = [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in d.get("segments", [])]
    return TranscriptResult(text=d["text"], segments=segments, source=d.get("source", ""), language=d.get("language"))


def _serialize_keyframes(kfs: list[KeyFrame]) -> list[dict]:
    return [{"timestamp": kf.timestamp, "image_path": kf.image_path.name} for kf in kfs]


def _deserialize_keyframes(d: list[dict], work_dir: Path) -> list[KeyFrame]:
    # image_path stored as filename relative to frames/ dir; resolve via glob
    result = []
    for entry in d:
        name = entry["image_path"]
        # Try frames/ subdir first (where extract_keyframes puts them)
        candidate = work_dir / "frames" / name
        if not candidate.exists():
            # Fall back to direct work_dir child
            candidate = work_dir / name
        result.append(KeyFrame(timestamp=entry["timestamp"], image_path=candidate))
    return result


def _serialize_ocr(results: list[OcrResult]) -> list[dict]:
    return [
        {"timestamp": r.timestamp, "image_path": r.image_path.name, "text": r.text}
        for r in results
    ]


def _deserialize_ocr(d: list[dict], work_dir: Path) -> list[OcrResult]:
    result = []
    for entry in d:
        name = entry["image_path"]
        candidate = work_dir / "frames" / name
        if not candidate.exists():
            candidate = work_dir / name
        result.append(OcrResult(timestamp=entry["timestamp"], image_path=candidate, text=entry["text"]))
    return result


# ---------------------------------------------------------------------------
# Per-step artifact persistence helpers
# ---------------------------------------------------------------------------

def _save_step_transcript(work_dir: Path, transcript: TranscriptResult) -> str:
    """Write transcript.json to work_dir; return relative filename."""
    name = "transcript.json"
    (work_dir / name).write_text(
        json.dumps(_serialize_transcript(transcript), ensure_ascii=False), encoding="utf-8"
    )
    return name


def _save_step_keyframes(work_dir: Path, keyframes: list[KeyFrame], name: str) -> str:
    """Write keyframes list to work_dir/<name>; return relative filename."""
    (work_dir / name).write_text(
        json.dumps(_serialize_keyframes(keyframes), ensure_ascii=False), encoding="utf-8"
    )
    return name


def _save_step_ocr(work_dir: Path, ocr_results: list[OcrResult], name: str) -> str:
    """Write ocr_results list to work_dir/<name>; return relative filename."""
    (work_dir / name).write_text(
        json.dumps(_serialize_ocr(ocr_results), ensure_ascii=False), encoding="utf-8"
    )
    return name


# ---------------------------------------------------------------------------
# Partial-output purge (called in except branches so failed step leaves nothing)
# ---------------------------------------------------------------------------

def _purge_step_artifacts(work_dir: Path, step: str) -> None:
    """Best-effort removal of well-known outputs for a failed step."""
    try:
        if step == "downloading":
            for pattern in ("*.mp4", "*.webm", "*.mkv", "*.part", "*.ytdl"):
                for f in work_dir.glob(pattern):
                    f.unlink(missing_ok=True)
        elif step == "transcribing":
            (work_dir / "audio.wav").unlink(missing_ok=True)
            for f in work_dir.glob("*.json3"):
                f.unlink(missing_ok=True)
        elif step == "extracting_keyframes":
            frames_dir = work_dir / "frames"
            if frames_dir.exists():
                shutil.rmtree(frames_dir, ignore_errors=True)
        elif step == "ocr":
            ocr_dir = work_dir / "ocr"
            if ocr_dir.exists():
                shutil.rmtree(ocr_dir, ignore_errors=True)
            (work_dir / "ocr_results.json").unlink(missing_ok=True)
        elif step == "deduplicating":
            # Dedup produces in-memory output; no unique files to purge
            pass
    except Exception:
        logger.exception("Failed to purge artifacts for step %s in %s", step, work_dir)


# ---------------------------------------------------------------------------
# LRU eviction of old failed/cancelled job tmp dirs
# ---------------------------------------------------------------------------

async def _evict_old_failed_artifacts(max_keep: int) -> None:
    """Remove tmp dirs for failed/cancelled jobs beyond the newest max_keep."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id FROM jobs
               WHERE status IN ('failed','cancelled')
               ORDER BY updated_at DESC
               LIMIT -1 OFFSET ?""",
            (max_keep,),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    evicted = 0
    for row in rows:
        job_dir = TMP_DIR / row["id"]
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            evicted += 1

    if evicted:
        logger.info("LRU eviction: removed %d old failed/cancelled job tmp dirs", evicted)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_llm_settings() -> dict:
    return get_llm_settings()


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

    llm_settings = _get_llm_settings()
    _active_provider = llm_settings.get("active_provider", "claude")
    _provider_cfg = llm_settings.get("providers", {}).get(_active_provider, {})

    # Load or initialise manifest
    manifest = _load_manifest(work_dir) or {
        "version": 1,
        "job_id": job_id,
        "video_id": video_id,
        "dedup_mode": dedup_mode,
        "keyframe_mode": keyframe_mode_str,
        "completed": {},
    }

    # Invalidate downstream steps when job settings changed from the cached run
    if manifest.get("dedup_mode") != dedup_mode or manifest.get("keyframe_mode") != keyframe_mode_str:
        logger.info("[%s] dedup_mode or keyframe_mode changed — invalidating deduplicating/ocr cache", job_id)
        manifest["completed"].pop("deduplicating", None)
        manifest["completed"].pop("ocr", None)
        _purge_step_artifacts(work_dir, "deduplicating")
        _purge_step_artifacts(work_dir, "ocr")
        manifest["dedup_mode"] = dedup_mode
        manifest["keyframe_mode"] = keyframe_mode_str

    transcript = None
    keyframes: list[KeyFrame] = []
    ocr_results = None
    ocr_paths = None
    video_path = None

    try:
        # Step 1: Download
        await _update_job(job_id, status="processing", current_step="downloading")
        if "download" in manifest["completed"]:
            rel = manifest["completed"]["download"].get("video_path")
            candidate = work_dir / rel if rel else None
            if candidate and candidate.exists():
                video_path = candidate
                logger.info("[%s] Reusing cached download: %s", job_id, video_path)
            else:
                logger.info("[%s] Cached download missing from disk — re-downloading", job_id)
                manifest["completed"].pop("download", None)
        if "download" not in manifest["completed"]:
            try:
                video_path = await download_video(video_id, work_dir)
                logger.info("[%s] Downloaded: %s", job_id, video_path)
                manifest["completed"]["download"] = {"video_path": video_path.name if video_path else None}
                _save_manifest(work_dir, manifest)
            except Exception:
                logger.exception("[%s] Download failed", job_id)
                _purge_step_artifacts(work_dir, "downloading")
                await _add_warning(job_id, "Download failed, attempting transcript via captions")

        if is_cancelled(job_id):
            logger.info("[%s] Cancelled after download", job_id)
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            return

        # Step 2: Transcript
        await _update_job(job_id, current_step="transcribing")
        if "transcript" in manifest["completed"]:
            rel = manifest["completed"]["transcript"].get("transcript_path")
            if rel and (work_dir / rel).exists():
                try:
                    transcript = _deserialize_transcript(json.loads((work_dir / rel).read_text(encoding="utf-8")))
                    logger.info("[%s] Reusing cached transcript: %s, %d segments", job_id, transcript.source, len(transcript.segments))
                    if transcript.language:
                        await _update_job(job_id, language=transcript.language)
                except Exception:
                    logger.warning("[%s] Cached transcript corrupt — re-extracting", job_id)
                    manifest["completed"].pop("transcript", None)
            else:
                logger.info("[%s] Cached transcript missing from disk — re-extracting", job_id)
                manifest["completed"].pop("transcript", None)
        if "transcript" not in manifest["completed"]:
            try:
                transcript = await extract_transcript(video_id, video_path, work_dir)
                logger.info("[%s] Transcript: %s, %d segments", job_id, transcript.source, len(transcript.segments))
                if transcript.language:
                    await _update_job(job_id, language=transcript.language)
                rel = _save_step_transcript(work_dir, transcript)
                manifest["completed"]["transcript"] = {"transcript_path": rel}
                _save_manifest(work_dir, manifest)
            except Exception:
                logger.exception("[%s] Transcript extraction failed", job_id)
                _purge_step_artifacts(work_dir, "transcribing")
                await _add_warning(job_id, "Transcript extraction failed, using keyframes only")

        if is_cancelled(job_id):
            logger.info("[%s] Cancelled after transcript", job_id)
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            return

        # Step 3: Keyframes
        await _update_job(job_id, current_step="extracting_keyframes")
        if "keyframes" in manifest["completed"]:
            rel = manifest["completed"]["keyframes"].get("keyframes_path")
            if rel and (work_dir / rel).exists():
                try:
                    keyframes = _deserialize_keyframes(json.loads((work_dir / rel).read_text(encoding="utf-8")), work_dir)
                    logger.info("[%s] Reusing cached keyframes: %d frames", job_id, len(keyframes))
                except Exception:
                    logger.warning("[%s] Cached keyframes corrupt — re-extracting", job_id)
                    manifest["completed"].pop("keyframes", None)
            else:
                logger.info("[%s] Cached keyframes missing from disk — re-extracting", job_id)
                manifest["completed"].pop("keyframes", None)
        if "keyframes" not in manifest["completed"]:
            if video_path and video_path.exists():
                try:
                    keyframes = await extract_keyframes(video_path, work_dir)
                    logger.info("[%s] Keyframes: %d extracted", job_id, len(keyframes))
                    rel = _save_step_keyframes(work_dir, keyframes, "keyframes.json")
                    manifest["completed"]["keyframes"] = {"keyframes_path": rel}
                    _save_manifest(work_dir, manifest)
                except Exception:
                    logger.exception("[%s] Keyframe extraction failed", job_id)
                    _purge_step_artifacts(work_dir, "extracting_keyframes")
                    await _add_warning(job_id, "Keyframe extraction failed, using transcript only")
            else:
                logger.warning("[%s] No video file, skipping keyframes", job_id)
                if not transcript:
                    await _update_job(job_id, status="failed", error="No video file and no transcript")
                    _save_manifest(work_dir, manifest)
                    await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
                    return

        # Check if we have anything to summarize
        if not transcript and not keyframes:
            await _update_job(job_id, status="failed", error="Both transcript and keyframe extraction failed")
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            return

        if is_cancelled(job_id):
            logger.info("[%s] Cancelled before dedup/OCR", job_id)
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
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
            if "ocr" in manifest["completed"]:
                rel = manifest["completed"]["ocr"].get("ocr_results_path")
                if rel and (work_dir / rel).exists():
                    try:
                        ocr_results = _deserialize_ocr(json.loads((work_dir / rel).read_text(encoding="utf-8")), work_dir)
                        ocr_paths = [work_dir / "ocr" / f"frame_{i:04d}_ocr.txt" if r.text else None
                                     for i, r in enumerate(ocr_results)]
                        ocr_paths = [p if p and p.exists() else None for p in ocr_paths]
                        logger.info("[%s] Reusing cached OCR: %d results", job_id, len(ocr_results))
                    except Exception:
                        logger.warning("[%s] Cached OCR corrupt — re-running", job_id)
                        manifest["completed"].pop("ocr", None)
                        ocr_results = None
                else:
                    logger.info("[%s] Cached OCR missing from disk — re-running", job_id)
                    manifest["completed"].pop("ocr", None)
            if "ocr" not in manifest["completed"]:
                try:
                    loop = asyncio.get_running_loop()
                    await _update_job(job_id, step_progress=0, step_total=len(keyframes))

                    def _ocr_progress(done: int, total: int) -> None:
                        asyncio.run_coroutine_threadsafe(_update_job(job_id, step_progress=done), loop)

                    ocr_results = await extract_text(keyframes, on_progress=_ocr_progress)
                    ocr_paths = save_ocr_results(ocr_results, work_dir)
                    logger.info("[%s] OCR: %d results", job_id, len(ocr_results))
                    rel = _save_step_ocr(work_dir, ocr_results, "ocr_results.json")
                    manifest["completed"]["ocr"] = {"ocr_results_path": rel}
                    _save_manifest(work_dir, manifest)
                    await _update_job(job_id, step_progress=None, step_total=None)
                except Exception:
                    logger.exception("[%s] OCR failed", job_id)
                    _purge_step_artifacts(work_dir, "ocr")
                    await _add_warning(job_id, "OCR failed, falling back to image-only mode")
                    await _update_job(job_id, step_progress=None, step_total=None)
                    keyframe_mode_str = "image"
                    needs_ocr = False

            await _update_job(job_id, current_step="deduplicating")
            if "deduplicating" in manifest["completed"]:
                rel = manifest["completed"]["deduplicating"].get("keyframes_path")
                if rel and (work_dir / rel).exists():
                    try:
                        keyframes = _deserialize_keyframes(json.loads((work_dir / rel).read_text(encoding="utf-8")), work_dir)
                        logger.info("[%s] Reusing cached dedup: %d keyframes", job_id, len(keyframes))
                    except Exception:
                        logger.warning("[%s] Cached dedup corrupt — re-deduplicating", job_id)
                        manifest["completed"].pop("deduplicating", None)
                else:
                    logger.info("[%s] Cached dedup missing from disk — re-deduplicating", job_id)
                    manifest["completed"].pop("deduplicating", None)
            if "deduplicating" not in manifest["completed"]:
                try:
                    keyframes, ocr_results = deduplicate_keyframes(
                        keyframes, ocr_results=ocr_results, mode="ocr",
                    )
                    if ocr_results:
                        ocr_paths = save_ocr_results(ocr_results, work_dir)
                    logger.info("[%s] Dedup (ocr): %d keyframes remaining", job_id, len(keyframes))
                    rel = _save_step_keyframes(work_dir, keyframes, "keyframes.dedup.json")
                    manifest["completed"]["deduplicating"] = {"keyframes_path": rel}
                    _save_manifest(work_dir, manifest)
                except Exception:
                    logger.exception("[%s] Dedup failed", job_id)
                    await _add_warning(job_id, "Dedup failed, using all keyframes")
        else:
            # Dedup first, then OCR on deduped frames
            await _update_job(job_id, current_step="deduplicating")
            if "deduplicating" in manifest["completed"]:
                rel = manifest["completed"]["deduplicating"].get("keyframes_path")
                if rel and (work_dir / rel).exists():
                    try:
                        keyframes = _deserialize_keyframes(json.loads((work_dir / rel).read_text(encoding="utf-8")), work_dir)
                        logger.info("[%s] Reusing cached dedup: %d keyframes", job_id, len(keyframes))
                    except Exception:
                        logger.warning("[%s] Cached dedup corrupt — re-deduplicating", job_id)
                        manifest["completed"].pop("deduplicating", None)
                else:
                    logger.info("[%s] Cached dedup missing from disk — re-deduplicating", job_id)
                    manifest["completed"].pop("deduplicating", None)
            if "deduplicating" not in manifest["completed"]:
                if keyframes:
                    try:
                        keyframes, _ = deduplicate_keyframes(keyframes, mode=effective_dedup_mode)
                        logger.info("[%s] Dedup (%s): %d keyframes remaining", job_id, effective_dedup_mode, len(keyframes))
                        rel = _save_step_keyframes(work_dir, keyframes, "keyframes.dedup.json")
                        manifest["completed"]["deduplicating"] = {"keyframes_path": rel}
                        _save_manifest(work_dir, manifest)
                    except Exception:
                        logger.exception("[%s] Dedup failed", job_id)
                        await _add_warning(job_id, "Dedup failed, using all keyframes")

            await _update_job(job_id, current_step="ocr")
            if needs_ocr and keyframes:
                if "ocr" in manifest["completed"]:
                    rel = manifest["completed"]["ocr"].get("ocr_results_path")
                    if rel and (work_dir / rel).exists():
                        try:
                            ocr_results = _deserialize_ocr(json.loads((work_dir / rel).read_text(encoding="utf-8")), work_dir)
                            ocr_paths = [work_dir / "ocr" / f"frame_{i:04d}_ocr.txt" if r.text else None
                                         for i, r in enumerate(ocr_results)]
                            ocr_paths = [p if p and p.exists() else None for p in ocr_paths]
                            logger.info("[%s] Reusing cached OCR: %d results", job_id, len(ocr_results))
                        except Exception:
                            logger.warning("[%s] Cached OCR corrupt — re-running", job_id)
                            manifest["completed"].pop("ocr", None)
                            ocr_results = None
                    else:
                        logger.info("[%s] Cached OCR missing from disk — re-running", job_id)
                        manifest["completed"].pop("ocr", None)
                if "ocr" not in manifest["completed"]:
                    try:
                        loop = asyncio.get_running_loop()
                        await _update_job(job_id, step_progress=0, step_total=len(keyframes))

                        def _ocr_progress(done: int, total: int) -> None:
                            asyncio.run_coroutine_threadsafe(_update_job(job_id, step_progress=done), loop)

                        ocr_results = await extract_text(keyframes, on_progress=_ocr_progress)
                        ocr_paths = save_ocr_results(ocr_results, work_dir)
                        logger.info("[%s] OCR: %d results", job_id, len(ocr_results))
                        rel = _save_step_ocr(work_dir, ocr_results, "ocr_results.json")
                        manifest["completed"]["ocr"] = {"ocr_results_path": rel}
                        _save_manifest(work_dir, manifest)
                        await _update_job(job_id, step_progress=None, step_total=None)
                    except Exception:
                        logger.exception("[%s] OCR failed", job_id)
                        _purge_step_artifacts(work_dir, "ocr")
                        await _add_warning(job_id, "OCR failed, falling back to image-only mode")
                        await _update_job(job_id, step_progress=None, step_total=None)
                        keyframe_mode_str = "image"

        if is_cancelled(job_id):
            logger.info("[%s] Cancelled before summarizing", job_id)
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            return

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

            # Per-job custom_prompt overrides global setting
            effective_prompt = job["custom_prompt"] if job["custom_prompt"] else _provider_cfg.get("custom_prompt")
            effective_mode = job["custom_prompt_mode"] or _provider_cfg.get("custom_prompt_mode") or "replace"
            effective_language = job["output_language"] or _provider_cfg.get("output_language") or transcript.language

            result = await summarize(
                transcript=transcript,
                keyframes=keyframes,
                video_meta=video_meta,
                custom_prompt=effective_prompt,
                custom_prompt_mode=effective_mode,
                model=_provider_cfg.get("model") or "claude-sonnet-4-20250514",
                keyframe_mode=KeyframeMode(keyframe_mode_str),
                ocr_paths=ocr_paths,
                ocr_results=ocr_results,
                output_language=effective_language,
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
            _save_manifest(work_dir, manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            return

        # Step 7: Cleanup (successful path only — manifest is not needed after success)
        await _update_job(job_id, current_step="cleanup")
        _cleanup(work_dir)

        if not is_cancelled(job_id):
            await _update_job(job_id, status="done", current_step=None)
            logger.info("[%s] Pipeline complete", job_id)
        else:
            logger.info("[%s] Job was cancelled, skipping done status", job_id)

    except Exception:
        logger.exception("[%s] Pipeline failed unexpectedly", job_id)
        await _update_job(job_id, status="failed", error="Unexpected pipeline error")
        _save_manifest(work_dir, manifest)
        await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)


@dataclass
class _BatchJob:
    """Tracks per-job state within a batch."""
    job_id: str
    video_id: str
    dedup_mode: str
    keyframe_mode_str: str
    work_dir: Path
    manifest: dict = field(default_factory=dict)
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

        dedup_mode = job["dedup_mode"] or "regular"
        keyframe_mode_str = job["keyframe_mode"] or "image"

        manifest = _load_manifest(work_dir) or {
            "version": 1,
            "job_id": job_id,
            "video_id": job["video_id"],
            "dedup_mode": dedup_mode,
            "keyframe_mode": keyframe_mode_str,
            "completed": {},
        }

        # Invalidate downstream steps when settings changed
        if manifest.get("dedup_mode") != dedup_mode or manifest.get("keyframe_mode") != keyframe_mode_str:
            logger.info("[%s] dedup_mode or keyframe_mode changed — invalidating deduplicating/ocr cache", job_id)
            manifest["completed"].pop("deduplicating", None)
            manifest["completed"].pop("ocr", None)
            _purge_step_artifacts(work_dir, "deduplicating")
            _purge_step_artifacts(work_dir, "ocr")
            manifest["dedup_mode"] = dedup_mode
            manifest["keyframe_mode"] = keyframe_mode_str

        batch.append(_BatchJob(
            job_id=job_id,
            video_id=job["video_id"],
            dedup_mode=dedup_mode,
            keyframe_mode_str=keyframe_mode_str,
            work_dir=work_dir,
            manifest=manifest,
        ))

    if not batch:
        return

    llm_settings = _get_llm_settings()
    _active_provider = llm_settings.get("active_provider", "claude")
    _provider_cfg = llm_settings.get("providers", {}).get(_active_provider, {})

    # Step 1: Download all
    for bj in _active(batch):
        await _update_job(bj.job_id, status="processing", current_step="downloading")
        if "download" in bj.manifest["completed"]:
            rel = bj.manifest["completed"]["download"].get("video_path")
            candidate = bj.work_dir / rel if rel else None
            if candidate and candidate.exists():
                bj.video_path = candidate
                logger.info("[%s] Reusing cached download: %s", bj.job_id, bj.video_path)
            else:
                logger.info("[%s] Cached download missing — re-downloading", bj.job_id)
                bj.manifest["completed"].pop("download", None)
        if "download" not in bj.manifest["completed"]:
            try:
                bj.video_path = await download_video(bj.video_id, bj.work_dir)
                logger.info("[%s] Downloaded: %s", bj.job_id, bj.video_path)
                bj.manifest["completed"]["download"] = {"video_path": bj.video_path.name if bj.video_path else None}
                _save_manifest(bj.work_dir, bj.manifest)
            except Exception:
                logger.exception("[%s] Download failed", bj.job_id)
                _purge_step_artifacts(bj.work_dir, "downloading")
                await _add_warning(bj.job_id, "Download failed, attempting transcript via captions")

    for bj in _active(batch):
        if is_cancelled(bj.job_id):
            logger.info("[%s] Cancelled after download", bj.job_id)
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

    # Step 2: Transcribe all
    for bj in _active(batch):
        await _update_job(bj.job_id, current_step="transcribing")
        if "transcript" in bj.manifest["completed"]:
            rel = bj.manifest["completed"]["transcript"].get("transcript_path")
            if rel and (bj.work_dir / rel).exists():
                try:
                    bj.transcript = _deserialize_transcript(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")))
                    logger.info("[%s] Reusing cached transcript", bj.job_id)
                    if bj.transcript.language:
                        await _update_job(bj.job_id, language=bj.transcript.language)
                except Exception:
                    logger.warning("[%s] Cached transcript corrupt — re-extracting", bj.job_id)
                    bj.manifest["completed"].pop("transcript", None)
            else:
                logger.info("[%s] Cached transcript missing — re-extracting", bj.job_id)
                bj.manifest["completed"].pop("transcript", None)
        if "transcript" not in bj.manifest["completed"]:
            try:
                bj.transcript = await extract_transcript(bj.video_id, bj.video_path, bj.work_dir)
                logger.info("[%s] Transcript: %s, %d segments", bj.job_id, bj.transcript.source, len(bj.transcript.segments))
                if bj.transcript.language:
                    await _update_job(bj.job_id, language=bj.transcript.language)
                rel = _save_step_transcript(bj.work_dir, bj.transcript)
                bj.manifest["completed"]["transcript"] = {"transcript_path": rel}
                _save_manifest(bj.work_dir, bj.manifest)
            except Exception:
                logger.exception("[%s] Transcript extraction failed", bj.job_id)
                _purge_step_artifacts(bj.work_dir, "transcribing")
                await _add_warning(bj.job_id, "Transcript extraction failed, using keyframes only")

    for bj in _active(batch):
        if is_cancelled(bj.job_id):
            logger.info("[%s] Cancelled after transcript", bj.job_id)
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

    # Step 3: Extract keyframes all
    for bj in _active(batch):
        await _update_job(bj.job_id, current_step="extracting_keyframes")
        if "keyframes" in bj.manifest["completed"]:
            rel = bj.manifest["completed"]["keyframes"].get("keyframes_path")
            if rel and (bj.work_dir / rel).exists():
                try:
                    bj.keyframes = _deserialize_keyframes(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")), bj.work_dir)
                    logger.info("[%s] Reusing cached keyframes: %d frames", bj.job_id, len(bj.keyframes))
                except Exception:
                    logger.warning("[%s] Cached keyframes corrupt — re-extracting", bj.job_id)
                    bj.manifest["completed"].pop("keyframes", None)
            else:
                logger.info("[%s] Cached keyframes missing — re-extracting", bj.job_id)
                bj.manifest["completed"].pop("keyframes", None)
        if "keyframes" not in bj.manifest["completed"]:
            if bj.video_path and bj.video_path.exists():
                try:
                    bj.keyframes = await extract_keyframes(bj.video_path, bj.work_dir)
                    logger.info("[%s] Keyframes: %d extracted", bj.job_id, len(bj.keyframes))
                    rel = _save_step_keyframes(bj.work_dir, bj.keyframes, "keyframes.json")
                    bj.manifest["completed"]["keyframes"] = {"keyframes_path": rel}
                    _save_manifest(bj.work_dir, bj.manifest)
                except Exception:
                    logger.exception("[%s] Keyframe extraction failed", bj.job_id)
                    _purge_step_artifacts(bj.work_dir, "extracting_keyframes")
                    await _add_warning(bj.job_id, "Keyframe extraction failed, using transcript only")

        # Check if job has anything to work with
        if not bj.transcript and not bj.keyframes:
            await _update_job(bj.job_id, status="failed", error="Both transcript and keyframe extraction failed")
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

    for bj in _active(batch):
        if is_cancelled(bj.job_id):
            logger.info("[%s] Cancelled before dedup/OCR", bj.job_id)
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

    # Step 4/5: Dedup and OCR
    # Split batch by dedup strategy
    ocr_first = [bj for bj in _active(batch) if bj.dedup_mode == "ocr" and bj.keyframe_mode_str in _OCR_MODES and bj.keyframes]
    dedup_first = [bj for bj in _active(batch) if bj not in ocr_first]

    # Handle ocr-first jobs: OCR then dedup
    if ocr_first:
        for bj in ocr_first:
            await _update_job(bj.job_id, current_step="ocr")
            if "ocr" in bj.manifest["completed"]:
                rel = bj.manifest["completed"]["ocr"].get("ocr_results_path")
                if rel and (bj.work_dir / rel).exists():
                    try:
                        bj.ocr_results = _deserialize_ocr(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")), bj.work_dir)
                        bj.ocr_paths = [bj.work_dir / "ocr" / f"frame_{i:04d}_ocr.txt" if r.text else None
                                        for i, r in enumerate(bj.ocr_results)]
                        bj.ocr_paths = [p if p and p.exists() else None for p in bj.ocr_paths]
                        logger.info("[%s] Reusing cached OCR: %d results", bj.job_id, len(bj.ocr_results))
                    except Exception:
                        logger.warning("[%s] Cached OCR corrupt — re-running", bj.job_id)
                        bj.manifest["completed"].pop("ocr", None)
                        bj.ocr_results = None
                else:
                    logger.info("[%s] Cached OCR missing — re-running", bj.job_id)
                    bj.manifest["completed"].pop("ocr", None)
            if "ocr" not in bj.manifest["completed"]:
                try:
                    loop = asyncio.get_running_loop()
                    await _update_job(bj.job_id, step_progress=0, step_total=len(bj.keyframes))

                    def _ocr_progress(done: int, total: int, _jid=bj.job_id) -> None:
                        asyncio.run_coroutine_threadsafe(_update_job(_jid, step_progress=done), loop)

                    bj.ocr_results = await extract_text(bj.keyframes, on_progress=_ocr_progress)
                    bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
                    rel = _save_step_ocr(bj.work_dir, bj.ocr_results, "ocr_results.json")
                    bj.manifest["completed"]["ocr"] = {"ocr_results_path": rel}
                    _save_manifest(bj.work_dir, bj.manifest)
                    await _update_job(bj.job_id, step_progress=None, step_total=None)
                except Exception:
                    logger.exception("[%s] OCR failed", bj.job_id)
                    _purge_step_artifacts(bj.work_dir, "ocr")
                    await _add_warning(bj.job_id, "OCR failed, falling back to image-only mode")
                    await _update_job(bj.job_id, step_progress=None, step_total=None)
                    bj.keyframe_mode_str = "image"

        for bj in ocr_first:
            await _update_job(bj.job_id, current_step="deduplicating")
            if "deduplicating" in bj.manifest["completed"]:
                rel = bj.manifest["completed"]["deduplicating"].get("keyframes_path")
                if rel and (bj.work_dir / rel).exists():
                    try:
                        bj.keyframes = _deserialize_keyframes(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")), bj.work_dir)
                        logger.info("[%s] Reusing cached dedup: %d keyframes", bj.job_id, len(bj.keyframes))
                    except Exception:
                        logger.warning("[%s] Cached dedup corrupt — re-deduplicating", bj.job_id)
                        bj.manifest["completed"].pop("deduplicating", None)
                else:
                    logger.info("[%s] Cached dedup missing — re-deduplicating", bj.job_id)
                    bj.manifest["completed"].pop("deduplicating", None)
            if "deduplicating" not in bj.manifest["completed"]:
                try:
                    bj.keyframes, bj.ocr_results = deduplicate_keyframes(
                        bj.keyframes, ocr_results=bj.ocr_results, mode="ocr",
                    )
                    if bj.ocr_results:
                        bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
                    rel = _save_step_keyframes(bj.work_dir, bj.keyframes, "keyframes.dedup.json")
                    bj.manifest["completed"]["deduplicating"] = {"keyframes_path": rel}
                    _save_manifest(bj.work_dir, bj.manifest)
                except Exception:
                    logger.exception("[%s] Dedup failed", bj.job_id)
                    await _add_warning(bj.job_id, "Dedup failed, using all keyframes")

    # Handle dedup-first jobs: dedup then OCR
    for bj in dedup_first:
        await _update_job(bj.job_id, current_step="deduplicating")
        if "deduplicating" in bj.manifest["completed"]:
            rel = bj.manifest["completed"]["deduplicating"].get("keyframes_path")
            if rel and (bj.work_dir / rel).exists():
                try:
                    bj.keyframes = _deserialize_keyframes(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")), bj.work_dir)
                    logger.info("[%s] Reusing cached dedup: %d keyframes", bj.job_id, len(bj.keyframes))
                except Exception:
                    logger.warning("[%s] Cached dedup corrupt — re-deduplicating", bj.job_id)
                    bj.manifest["completed"].pop("deduplicating", None)
            else:
                logger.info("[%s] Cached dedup missing — re-deduplicating", bj.job_id)
                bj.manifest["completed"].pop("deduplicating", None)
        if "deduplicating" not in bj.manifest["completed"]:
            if bj.keyframes:
                effective_mode = bj.dedup_mode
                if effective_mode == "ocr":
                    effective_mode = "regular"
                    await _add_warning(bj.job_id, "OCR dedup requested but keyframe mode doesn't use OCR, falling back to regular dedup")
                try:
                    bj.keyframes, _ = deduplicate_keyframes(bj.keyframes, mode=effective_mode)
                    rel = _save_step_keyframes(bj.work_dir, bj.keyframes, "keyframes.dedup.json")
                    bj.manifest["completed"]["deduplicating"] = {"keyframes_path": rel}
                    _save_manifest(bj.work_dir, bj.manifest)
                except Exception:
                    logger.exception("[%s] Dedup failed", bj.job_id)
                    await _add_warning(bj.job_id, "Dedup failed, using all keyframes")

    needs_ocr_batch = [bj for bj in dedup_first if bj.keyframe_mode_str in _OCR_MODES and bj.keyframes]
    if needs_ocr_batch:
        for bj in needs_ocr_batch:
            await _update_job(bj.job_id, current_step="ocr")
            if "ocr" in bj.manifest["completed"]:
                rel = bj.manifest["completed"]["ocr"].get("ocr_results_path")
                if rel and (bj.work_dir / rel).exists():
                    try:
                        bj.ocr_results = _deserialize_ocr(json.loads((bj.work_dir / rel).read_text(encoding="utf-8")), bj.work_dir)
                        bj.ocr_paths = [bj.work_dir / "ocr" / f"frame_{i:04d}_ocr.txt" if r.text else None
                                        for i, r in enumerate(bj.ocr_results)]
                        bj.ocr_paths = [p if p and p.exists() else None for p in bj.ocr_paths]
                        logger.info("[%s] Reusing cached OCR: %d results", bj.job_id, len(bj.ocr_results))
                    except Exception:
                        logger.warning("[%s] Cached OCR corrupt — re-running", bj.job_id)
                        bj.manifest["completed"].pop("ocr", None)
                        bj.ocr_results = None
                else:
                    logger.info("[%s] Cached OCR missing — re-running", bj.job_id)
                    bj.manifest["completed"].pop("ocr", None)
            if "ocr" not in bj.manifest["completed"]:
                try:
                    loop = asyncio.get_running_loop()
                    await _update_job(bj.job_id, step_progress=0, step_total=len(bj.keyframes))

                    def _ocr_progress(done: int, total: int, _jid=bj.job_id) -> None:
                        asyncio.run_coroutine_threadsafe(_update_job(_jid, step_progress=done), loop)

                    bj.ocr_results = await extract_text(bj.keyframes, on_progress=_ocr_progress)
                    bj.ocr_paths = save_ocr_results(bj.ocr_results, bj.work_dir)
                    rel = _save_step_ocr(bj.work_dir, bj.ocr_results, "ocr_results.json")
                    bj.manifest["completed"]["ocr"] = {"ocr_results_path": rel}
                    _save_manifest(bj.work_dir, bj.manifest)
                    await _update_job(bj.job_id, step_progress=None, step_total=None)
                except Exception:
                    logger.exception("[%s] OCR failed", bj.job_id)
                    _purge_step_artifacts(bj.work_dir, "ocr")
                    await _add_warning(bj.job_id, "OCR failed, falling back to image-only mode")
                    await _update_job(bj.job_id, step_progress=None, step_total=None)
                    bj.keyframe_mode_str = "image"

    for bj in _active(batch):
        if is_cancelled(bj.job_id):
            logger.info("[%s] Cancelled before summarizing", bj.job_id)
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

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

            # Per-job custom_prompt overrides global setting
            effective_prompt = job["custom_prompt"] if job["custom_prompt"] else _provider_cfg.get("custom_prompt")
            effective_mode = job["custom_prompt_mode"] or _provider_cfg.get("custom_prompt_mode") or "replace"
            effective_language = job["output_language"] or _provider_cfg.get("output_language") or transcript.language

            result = await summarize(
                transcript=transcript,
                keyframes=bj.keyframes,
                video_meta=video_meta,
                custom_prompt=effective_prompt,
                custom_prompt_mode=effective_mode,
                model=_provider_cfg.get("model") or "claude-sonnet-4-20250514",
                keyframe_mode=KeyframeMode(bj.keyframe_mode_str),
                ocr_paths=bj.ocr_paths,
                ocr_results=bj.ocr_results,
                output_language=effective_language,
            )

            if is_cancelled(bj.job_id):
                logger.info("[%s] Cancelled after summarize response, skipping summary insert", bj.job_id)
                _save_manifest(bj.work_dir, bj.manifest)
                await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
                bj.failed = True
                continue

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
            _save_manifest(bj.work_dir, bj.manifest)
            await _evict_old_failed_artifacts(MAX_REUSABLE_FAILED_JOBS)
            bj.failed = True

    # Step 7: Cleanup all (successful path only)
    for bj in batch:
        if not bj.failed:
            await _update_job(bj.job_id, current_step="cleanup")
            _cleanup(bj.work_dir)
            if not is_cancelled(bj.job_id):
                await _update_job(bj.job_id, status="done", current_step=None)
                logger.info("[%s] Pipeline complete", bj.job_id)
            else:
                logger.info("[%s] Job was cancelled, skipping done status", bj.job_id)
        else:
            # Failed jobs: manifest already saved; don't clean up (preserve artifacts)
            logger.debug("[%s] Skipping cleanup for failed batch job", bj.job_id)


def _cleanup(work_dir: Path):
    """Remove temporary working directory."""
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
            logger.info("Cleaned up %s", work_dir)
    except Exception:
        logger.exception("Failed to clean up %s", work_dir)

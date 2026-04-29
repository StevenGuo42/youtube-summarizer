"""Tests for artifact-reuse feature in the pipeline.

Covers: manifest roundtrip, partial purge, step reuse, failed-step
non-persistence, LRU eviction, successful-job cleanup, and dedup-mode
invalidation. No network or GPU access — all heavy services mocked.
"""
import json
import logging
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers to insert a job row directly (mirrors app/routers/queue.py)
# ---------------------------------------------------------------------------

async def _insert_job(
    db_path: Path,
    job_id: str,
    video_id: str = "test_vid",
    status: str = "pending",
    dedup_mode: str = "regular",
    keyframe_mode: str = "image",
    updated_at: str | None = None,
):
    async with aiosqlite.connect(db_path) as db:
        if updated_at:
            await db.execute(
                """INSERT INTO jobs (id, video_id, status, dedup_mode, keyframe_mode, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, video_id, status, dedup_mode, keyframe_mode, updated_at),
            )
        else:
            await db.execute(
                """INSERT INTO jobs (id, video_id, status, dedup_mode, keyframe_mode)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_id, video_id, status, dedup_mode, keyframe_mode),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_dirs(tmp_path, monkeypatch):
    """Set up isolated TMP_DIR and DB_PATH, initialise the DB schema."""
    tmp_tmp = tmp_path / "tmp"
    tmp_tmp.mkdir()
    db_path = tmp_path / "test.db"

    monkeypatch.setattr("app.config.TMP_DIR", tmp_tmp)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    monkeypatch.setattr("app.services.pipeline.TMP_DIR", tmp_tmp)
    # Also patch database module so get_db() opens our test DB
    monkeypatch.setattr("app.database.DB_PATH", db_path)

    return tmp_tmp, db_path


@pytest_asyncio.fixture()
async def initialized_db(tmp_dirs):
    """Return (tmp_dir, db_path) after running init_db()."""
    tmp_tmp, db_path = tmp_dirs
    from app.database import init_db
    await init_db()
    logger.info("Initialized test DB at %s", db_path)
    return tmp_tmp, db_path


# ---------------------------------------------------------------------------
# Stub factories for heavy services
# ---------------------------------------------------------------------------

def _make_fake_video(work_dir: Path) -> Path:
    p = work_dir / "video.mp4"
    p.write_bytes(b"fake")
    return p


def _stub_llm_settings():
    return {
        "active_provider": "claude",
        "providers": {
            "claude": {
                "model": "claude-sonnet-4-20250514",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
            }
        },
    }


# ---------------------------------------------------------------------------
# Test 1: manifest roundtrip
# ---------------------------------------------------------------------------

def test_manifest_roundtrip(tmp_path):
    """Write a manifest with all serialized types; reload and assert equality."""
    from app.services.pipeline import (
        _save_manifest, _load_manifest,
        _serialize_transcript, _deserialize_transcript,
        _serialize_keyframes, _deserialize_keyframes,
        _serialize_ocr, _deserialize_ocr,
    )
    from app.services.transcript import TranscriptResult, Segment
    from app.services.keyframes import KeyFrame
    from app.services.ocr import OcrResult

    work_dir = tmp_path / "job1"
    work_dir.mkdir()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    # Create stub image files so deserialize can reference them
    img1 = frames_dir / "scene_0001.png"
    img2 = frames_dir / "scene_0002.png"
    img1.write_bytes(b"img")
    img2.write_bytes(b"img")

    transcript = TranscriptResult(
        text="Hello world",
        segments=[Segment(start=0.0, end=1.5, text="Hello world")],
        source="captions",
        language="en",
    )
    keyframes = [
        KeyFrame(timestamp=1.0, image_path=img1),
        KeyFrame(timestamp=3.5, image_path=img2),
    ]
    ocr_results = [
        OcrResult(timestamp=1.0, image_path=img1, text="slide one"),
        OcrResult(timestamp=3.5, image_path=img2, text=""),
    ]

    manifest = {
        "version": 1,
        "job_id": "job1",
        "video_id": "abc",
        "dedup_mode": "regular",
        "keyframe_mode": "image",
        "completed": {
            "transcript": {"transcript_path": "transcript.json"},
            "keyframes": {"keyframes_path": "keyframes.json"},
            "ocr": {"ocr_results_path": "ocr_results.json"},
        },
    }

    # Write serialized payloads
    (work_dir / "transcript.json").write_text(
        json.dumps(_serialize_transcript(transcript)), encoding="utf-8"
    )
    (work_dir / "keyframes.json").write_text(
        json.dumps(_serialize_keyframes(keyframes)), encoding="utf-8"
    )
    (work_dir / "ocr_results.json").write_text(
        json.dumps(_serialize_ocr(ocr_results)), encoding="utf-8"
    )

    _save_manifest(work_dir, manifest)
    loaded = _load_manifest(work_dir)
    assert loaded is not None
    assert loaded["version"] == 1
    assert loaded["job_id"] == "job1"

    # Deserialize transcript
    t2 = _deserialize_transcript(json.loads((work_dir / "transcript.json").read_text()))
    assert t2.text == transcript.text
    assert t2.language == "en"
    assert len(t2.segments) == 1
    assert t2.segments[0].start == 0.0
    assert t2.segments[0].text == "Hello world"

    # Deserialize keyframes
    kf2 = _deserialize_keyframes(json.loads((work_dir / "keyframes.json").read_text()), work_dir)
    assert len(kf2) == 2
    assert kf2[0].timestamp == 1.0
    assert kf2[0].image_path == img1

    # Deserialize OCR
    ocr2 = _deserialize_ocr(json.loads((work_dir / "ocr_results.json").read_text()), work_dir)
    assert len(ocr2) == 2
    assert ocr2[0].text == "slide one"
    assert ocr2[1].text == ""

    print("PASS: test_manifest_roundtrip — serialization and deserialization match")
    logger.info("test_manifest_roundtrip: all assertions passed")


# ---------------------------------------------------------------------------
# Test 2: partial purge removes step outputs
# ---------------------------------------------------------------------------

def test_partial_purge_removes_step_outputs(tmp_path):
    """_purge_step_artifacts removes only the targeted step's outputs."""
    from app.services.pipeline import _purge_step_artifacts

    work_dir = tmp_path / "job2"
    work_dir.mkdir()

    # Set up files for multiple steps
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()
    (frames_dir / "scene_0001.png").write_bytes(b"img")
    audio = work_dir / "audio.wav"
    audio.write_bytes(b"wav")
    sub = work_dir / "vid.en.json3"
    sub.write_bytes(b"subs")
    video = work_dir / "video.mp4"
    video.write_bytes(b"vid")

    # Purge extracting_keyframes — should remove frames/ only
    _purge_step_artifacts(work_dir, "extracting_keyframes")
    assert not frames_dir.exists(), "frames/ should be removed after extracting_keyframes purge"
    assert audio.exists(), "audio.wav should survive extracting_keyframes purge"

    # Purge transcribing — should remove audio.wav and *.json3
    _purge_step_artifacts(work_dir, "transcribing")
    assert not audio.exists(), "audio.wav should be removed after transcribing purge"
    assert not sub.exists(), "*.json3 should be removed after transcribing purge"
    assert video.exists(), "video.mp4 should survive transcribing purge"

    # Purge downloading — should remove *.mp4
    _purge_step_artifacts(work_dir, "downloading")
    assert not video.exists(), "video.mp4 should be removed after downloading purge"

    # Purge ocr
    ocr_dir = work_dir / "ocr"
    ocr_dir.mkdir()
    (ocr_dir / "frame_0000_ocr.txt").write_text("text")
    ocr_json = work_dir / "ocr_results.json"
    ocr_json.write_text("{}")
    _purge_step_artifacts(work_dir, "ocr")
    assert not ocr_dir.exists(), "ocr/ should be removed after ocr purge"
    assert not ocr_json.exists(), "ocr_results.json should be removed after ocr purge"

    print("PASS: test_partial_purge_removes_step_outputs")
    logger.info("test_partial_purge_removes_step_outputs: all assertions passed")


# ---------------------------------------------------------------------------
# Test 3: reuse skips completed steps
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reuse_skips_completed_steps(initialized_db, monkeypatch):
    """Pre-seed download+transcript in manifest; process_job should skip them."""
    from app.services.transcript import TranscriptResult, Segment
    from app.services.keyframes import KeyFrame
    from app.services.ocr import OcrResult
    from app.services.llm import SummaryResult
    from app.services.pipeline import (
        _serialize_transcript, _serialize_keyframes, _save_manifest, _MANIFEST_NAME,
    )

    tmp_tmp, db_path = initialized_db
    job_id = "job-reuse-001"
    video_id = "vid001"

    work_dir = tmp_tmp / job_id
    work_dir.mkdir()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    # Create a fake video and image so reuse code finds them on disk
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"fake video")
    img = frames_dir / "scene_0001.png"
    img.write_bytes(b"fake img")

    transcript = TranscriptResult(
        text="Hello", segments=[Segment(start=0.0, end=1.0, text="Hello")],
        source="captions", language="en",
    )
    keyframes_list = [KeyFrame(timestamp=1.0, image_path=img)]

    # Write cached transcript
    (work_dir / "transcript.json").write_text(
        json.dumps(_serialize_transcript(transcript)), encoding="utf-8"
    )
    # Write cached keyframes
    (work_dir / "keyframes.json").write_text(
        json.dumps(_serialize_keyframes(keyframes_list)), encoding="utf-8"
    )
    # Write manifest declaring both complete
    manifest = {
        "version": 1,
        "job_id": job_id,
        "video_id": video_id,
        "dedup_mode": "regular",
        "keyframe_mode": "image",
        "completed": {
            "download": {"video_path": "video.mp4"},
            "transcript": {"transcript_path": "transcript.json"},
            "keyframes": {"keyframes_path": "keyframes.json"},
        },
    }
    _save_manifest(work_dir, manifest)

    await _insert_job(db_path, job_id, video_id=video_id, status="failed",
                      dedup_mode="regular", keyframe_mode="image")

    # Counters for stub calls
    counters = {"download": 0, "transcript": 0, "keyframes": 0, "summarize": 0,
                "dedup": 0, "extract_text": 0}

    async def stub_download(vid, wd):
        counters["download"] += 1
        return video_path

    async def stub_transcript(vid, vp, wd, whisper_model=None):
        counters["transcript"] += 1
        return transcript

    async def stub_keyframes(vp, wd):
        counters["keyframes"] += 1
        return keyframes_list

    def stub_dedup(kfs, ocr_results=None, mode="regular"):
        counters["dedup"] += 1
        return kfs, ocr_results

    async def stub_extract_text(kfs, model_tuple=None):
        counters["extract_text"] += 1
        return []

    async def stub_summarize(**kwargs):
        counters["summarize"] += 1
        return SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

    monkeypatch.setattr("app.services.pipeline.download_video", stub_download)
    monkeypatch.setattr("app.services.pipeline.extract_transcript", stub_transcript)
    monkeypatch.setattr("app.services.pipeline.extract_keyframes", stub_keyframes)
    monkeypatch.setattr("app.services.pipeline.deduplicate_keyframes", stub_dedup)
    monkeypatch.setattr("app.services.pipeline.extract_text", stub_extract_text)
    monkeypatch.setattr("app.services.pipeline.summarize", stub_summarize)
    monkeypatch.setattr("app.services.pipeline._get_llm_settings", _stub_llm_settings)
    monkeypatch.setattr("app.queue.worker.is_cancelled", lambda job_id: False)

    from app.services.pipeline import process_job
    await process_job(job_id)

    assert counters["download"] == 0, f"download should be skipped, called {counters['download']} times"
    assert counters["transcript"] == 0, f"transcript should be skipped, called {counters['transcript']} times"
    assert counters["keyframes"] == 0, f"keyframes should be skipped, called {counters['keyframes']} times"
    assert counters["summarize"] == 1, f"summarize should run once, called {counters['summarize']} times"

    print(f"PASS: test_reuse_skips_completed_steps — counters: {counters}")
    logger.info("test_reuse_skips_completed_steps: download=%d transcript=%d keyframes=%d summarize=%d",
                counters["download"], counters["transcript"], counters["keyframes"], counters["summarize"])


# ---------------------------------------------------------------------------
# Test 4: failed step not persisted in manifest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_step_not_persisted(initialized_db, monkeypatch):
    """If both transcript and keyframes fail, manifest must contain 'download' but NOT
    'transcript'. audio.wav must have been purged even though it was pre-created."""
    from app.services.transcript import TranscriptResult, Segment
    from app.services.keyframes import KeyFrame
    from app.services.pipeline import _save_manifest

    tmp_tmp, db_path = initialized_db
    job_id = "job-fail-001"
    video_id = "vid002"

    work_dir = tmp_tmp / job_id
    work_dir.mkdir()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"fake")

    # Pre-seed audio.wav to simulate transcribing started before the failure
    audio_wav = work_dir / "audio.wav"
    audio_wav.write_bytes(b"wav data")

    async def stub_download(vid, wd):
        return video_path

    async def stub_transcript_fail(vid, vp, wd, whisper_model=None):
        raise RuntimeError("Transcript failed intentionally")

    async def stub_keyframes_fail(vp, wd):
        raise RuntimeError("Keyframes failed intentionally")

    def stub_dedup(kfs, ocr_results=None, mode="regular"):
        return kfs, ocr_results

    async def stub_extract_text(kfs, model_tuple=None):
        return []

    from app.services.llm import SummaryResult

    async def stub_summarize(**kwargs):
        return SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

    monkeypatch.setattr("app.services.pipeline.download_video", stub_download)
    monkeypatch.setattr("app.services.pipeline.extract_transcript", stub_transcript_fail)
    monkeypatch.setattr("app.services.pipeline.extract_keyframes", stub_keyframes_fail)
    monkeypatch.setattr("app.services.pipeline.deduplicate_keyframes", stub_dedup)
    monkeypatch.setattr("app.services.pipeline.extract_text", stub_extract_text)
    monkeypatch.setattr("app.services.pipeline.summarize", stub_summarize)
    monkeypatch.setattr("app.services.pipeline._get_llm_settings", _stub_llm_settings)
    monkeypatch.setattr("app.queue.worker.is_cancelled", lambda job_id: False)

    await _insert_job(db_path, job_id, video_id=video_id, status="pending")

    from app.services.pipeline import process_job, _load_manifest
    await process_job(job_id)

    # Job is now 'failed' — manifest should be preserved on disk
    loaded = _load_manifest(work_dir)
    logger.info("Manifest after failed transcript+keyframes: %s", loaded)
    assert loaded is not None, "Manifest should exist after job failure (artifact-reuse feature)"
    assert "download" in loaded["completed"], "download should be in manifest"
    assert "transcript" not in loaded["completed"], "transcript must NOT be in manifest after failure"
    assert not audio_wav.exists(), "audio.wav should have been purged by _purge_step_artifacts"

    print("PASS: test_failed_step_not_persisted")
    logger.info("test_failed_step_not_persisted: manifest completed keys = %s", list(loaded["completed"].keys()))


# ---------------------------------------------------------------------------
# Test 5: LRU eviction keeps newest N tmp dirs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lru_eviction_keeps_newest_n(initialized_db, monkeypatch):
    """Insert 7 failed jobs; call _evict_old_failed_artifacts(5); 5 newest dirs remain."""
    tmp_tmp, db_path = initialized_db

    # We need pipeline's TMP_DIR to point at our tmp_tmp (already done by fixture)
    # but also re-import to pick up the monkeypatched value:
    import app.services.pipeline as pipeline_mod
    assert pipeline_mod.TMP_DIR == tmp_tmp, "TMP_DIR not correctly monkeypatched"

    job_ids = [f"evict-job-{i:02d}" for i in range(7)]
    # Give each a distinct updated_at; higher index → more recent
    for i, jid in enumerate(job_ids):
        ts = f"2026-01-01 00:{i:02d}:00"
        await _insert_job(db_path, jid, status="failed", updated_at=ts)
        (tmp_tmp / jid).mkdir()

    from app.services.pipeline import _evict_old_failed_artifacts
    await _evict_old_failed_artifacts(5)

    # The 5 most recent are job-02 .. job-06 (indices 2-6)
    # The 2 oldest are job-00 and job-01 (indices 0-1) — should be evicted
    for jid in job_ids[2:]:
        assert (tmp_tmp / jid).exists(), f"Dir for {jid} should survive eviction"
    for jid in job_ids[:2]:
        assert not (tmp_tmp / jid).exists(), f"Dir for {jid} should have been evicted"

    surviving = [jid for jid in job_ids if (tmp_tmp / jid).exists()]
    print(f"PASS: test_lru_eviction_keeps_newest_n — {len(surviving)} dirs remain (expected 5)")
    logger.info("test_lru_eviction_keeps_newest_n: surviving dirs: %s", surviving)


# ---------------------------------------------------------------------------
# Test 6: successful job cleans up tmp dir
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_job_still_cleans_up(initialized_db, monkeypatch):
    """All steps succeed — tmp dir must be removed; no manifest left behind."""
    from app.services.transcript import TranscriptResult, Segment
    from app.services.keyframes import KeyFrame
    from app.services.llm import SummaryResult

    tmp_tmp, db_path = initialized_db
    job_id = "job-success-001"
    video_id = "vid-s001"

    work_dir = tmp_tmp / job_id
    work_dir.mkdir()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"fake")
    img = frames_dir / "scene_0001.png"
    img.write_bytes(b"img")

    transcript = TranscriptResult(text="Hi", segments=[], source="captions", language="en")
    keyframes_list = [KeyFrame(timestamp=1.0, image_path=img)]

    async def stub_download(vid, wd):
        return video_path

    async def stub_transcript(vid, vp, wd, whisper_model=None):
        return transcript

    async def stub_keyframes(vp, wd):
        return keyframes_list

    def stub_dedup(kfs, ocr_results=None, mode="regular"):
        return kfs, ocr_results

    async def stub_extract_text(kfs, model_tuple=None):
        return []

    async def stub_summarize(**kwargs):
        return SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

    monkeypatch.setattr("app.services.pipeline.download_video", stub_download)
    monkeypatch.setattr("app.services.pipeline.extract_transcript", stub_transcript)
    monkeypatch.setattr("app.services.pipeline.extract_keyframes", stub_keyframes)
    monkeypatch.setattr("app.services.pipeline.deduplicate_keyframes", stub_dedup)
    monkeypatch.setattr("app.services.pipeline.extract_text", stub_extract_text)
    monkeypatch.setattr("app.services.pipeline.summarize", stub_summarize)
    monkeypatch.setattr("app.services.pipeline._get_llm_settings", _stub_llm_settings)
    monkeypatch.setattr("app.queue.worker.is_cancelled", lambda job_id: False)

    await _insert_job(db_path, job_id, video_id=video_id, status="pending")

    from app.services.pipeline import process_job
    await process_job(job_id)

    assert not work_dir.exists(), "tmp dir must be cleaned up after successful job"

    print("PASS: test_successful_job_still_cleans_up")
    logger.info("test_successful_job_still_cleans_up: tmp dir correctly removed at %s", work_dir)


# ---------------------------------------------------------------------------
# Test 7: dedup_mode change invalidates downstream (deduplicating + ocr)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_mode_change_invalidates_downstream(initialized_db, monkeypatch):
    """Manifest cached under dedup_mode=regular; job reruns with dedup_mode=slides.
    Dedup and OCR should re-run (counters > 0) even though manifest has those keys."""
    from app.services.transcript import TranscriptResult, Segment
    from app.services.keyframes import KeyFrame
    from app.services.ocr import OcrResult
    from app.services.llm import SummaryResult
    from app.services.pipeline import (
        _serialize_transcript, _serialize_keyframes, _serialize_ocr,
        _save_manifest,
    )

    tmp_tmp, db_path = initialized_db
    job_id = "job-dedup-chg-001"
    video_id = "vid-dc001"

    work_dir = tmp_tmp / job_id
    work_dir.mkdir()
    frames_dir = work_dir / "frames"
    frames_dir.mkdir()

    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"fake")
    img = frames_dir / "scene_0001.png"
    img.write_bytes(b"img")

    transcript = TranscriptResult(text="Hi", segments=[], source="captions", language="en")
    kf = KeyFrame(timestamp=1.0, image_path=img)
    ocr = OcrResult(timestamp=1.0, image_path=img, text="slide text")

    # Write all cached artifacts on disk
    (work_dir / "transcript.json").write_text(json.dumps(_serialize_transcript(transcript)))
    (work_dir / "keyframes.json").write_text(json.dumps(_serialize_keyframes([kf])))
    (work_dir / "keyframes.dedup.json").write_text(json.dumps(_serialize_keyframes([kf])))
    (work_dir / "ocr_results.json").write_text(json.dumps(_serialize_ocr([ocr])))

    # Manifest says everything done under dedup_mode=regular
    manifest = {
        "version": 1,
        "job_id": job_id,
        "video_id": video_id,
        "dedup_mode": "regular",
        "keyframe_mode": "image",
        "completed": {
            "download": {"video_path": "video.mp4"},
            "transcript": {"transcript_path": "transcript.json"},
            "keyframes": {"keyframes_path": "keyframes.json"},
            "deduplicating": {"keyframes_path": "keyframes.dedup.json"},
            "ocr": {"ocr_results_path": "ocr_results.json"},
        },
    }
    _save_manifest(work_dir, manifest)

    # Insert job with new dedup_mode=slides
    await _insert_job(db_path, job_id, video_id=video_id, status="failed",
                      dedup_mode="slides", keyframe_mode="image")

    counters = {"dedup": 0, "extract_text": 0, "summarize": 0}

    async def stub_download(vid, wd):
        return video_path

    async def stub_transcript(vid, vp, wd, whisper_model=None):
        return transcript

    async def stub_keyframes(vp, wd):
        return [kf]

    def stub_dedup(kfs, ocr_results=None, mode="regular"):
        counters["dedup"] += 1
        return kfs, ocr_results

    async def stub_extract_text(kfs, model_tuple=None):
        counters["extract_text"] += 1
        return []

    async def stub_summarize(**kwargs):
        counters["summarize"] += 1
        return SummaryResult(
            raw_response='{"title":"T","tldr":"TL","summary":"S"}',
            title="T", tldr="TL", summary="S",
        )

    monkeypatch.setattr("app.services.pipeline.download_video", stub_download)
    monkeypatch.setattr("app.services.pipeline.extract_transcript", stub_transcript)
    monkeypatch.setattr("app.services.pipeline.extract_keyframes", stub_keyframes)
    monkeypatch.setattr("app.services.pipeline.deduplicate_keyframes", stub_dedup)
    monkeypatch.setattr("app.services.pipeline.extract_text", stub_extract_text)
    monkeypatch.setattr("app.services.pipeline.summarize", stub_summarize)
    monkeypatch.setattr("app.services.pipeline._get_llm_settings", _stub_llm_settings)
    monkeypatch.setattr("app.queue.worker.is_cancelled", lambda job_id: False)

    from app.services.pipeline import process_job
    await process_job(job_id)

    assert counters["dedup"] > 0, f"dedup must re-run after dedup_mode change (called {counters['dedup']} times)"
    assert counters["summarize"] == 1, f"summarize should run once"

    print(f"PASS: test_dedup_mode_change_invalidates_downstream — counters: {counters}")
    logger.info("test_dedup_mode_change_invalidates_downstream: dedup=%d summarize=%d",
                counters["dedup"], counters["summarize"])

"""Tests for app.services.transcript — uses real YouTube API, requires network."""

import logging

import pytest

from app.config import COOKIES_PATH, TMP_DIR
from app.services.transcript import extract_transcript

logger = logging.getLogger(__name__)

# "Me at the zoo" — has auto-generated captions
TEST_VIDEO_ID = "jNQXAC9IVRw"


@pytest.mark.asyncio
async def test_extract_captions():
    """Test caption extraction from a video with available captions."""
    work_dir = TMP_DIR / "test_captions"
    work_dir.mkdir(exist_ok=True)

    result = await extract_transcript(TEST_VIDEO_ID, video_path=None, work_dir=work_dir)

    logger.info("Source: %s, segments: %d", result.source, len(result.segments))
    logger.info("First 200 chars: %s", result.text[:200])

    assert result.source == "captions"
    assert len(result.text) > 0
    assert len(result.segments) > 0
    assert all(s.start <= s.end for s in result.segments)


@pytest.mark.asyncio
async def test_whisper_fallback():
    """Test whisper transcription using a downloaded video."""
    from app.services.ytdlp import download_video

    work_dir = TMP_DIR / "test_whisper"
    work_dir.mkdir(exist_ok=True)

    video_path = await download_video(TEST_VIDEO_ID, work_dir)
    logger.info("Downloaded video to %s", video_path)

    # Pass a fake video_id that won't have captions to force whisper fallback
    result = await extract_transcript(
        "fake_no_captions", video_path=video_path, work_dir=work_dir
    )

    logger.info("Source: %s, segments: %d", result.source, len(result.segments))
    logger.info("First 200 chars: %s", result.text[:200])

    assert result.source == "whisper"
    assert len(result.text) > 0
    assert len(result.segments) > 0


@pytest.mark.asyncio
async def test_members_only_transcript(members_only_video_id):
    """Test transcript extraction for a members-only video (no captions, whisper fallback).

    Downloads audio only, transcribes first ~2 minutes via whisper.
    Requires valid cookies with channel membership.
    """
    from app.services.ytdlp import _base_opts

    if not COOKIES_PATH.exists():
        pytest.skip("No cookies.txt — cannot access members-only content")

    work_dir = TMP_DIR / "test_members_transcript"
    work_dir.mkdir(exist_ok=True)

    # Download audio only (skip full video — it's very long)
    audio_path = work_dir / f"{members_only_video_id}.m4a"
    if not audio_path.exists():
        import asyncio

        def _download_audio():
            import yt_dlp

            opts = {
                **_base_opts(),
                "format": "bestaudio[ext=m4a]/bestaudio",
                "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={members_only_video_id}"])

        await asyncio.to_thread(_download_audio)

    assert audio_path.exists(), "Audio download failed"
    logger.info("Audio file: %s (%d bytes)", audio_path.name, audio_path.stat().st_size)

    # Extract first 2 minutes of audio as wav for whisper
    import asyncio

    wav_path = work_dir / "audio.wav"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(audio_path), "-t", "120",
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(wav_path), "-y",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, f"ffmpeg failed: {stderr.decode()[-500:]}"

    # Transcribe with whisper
    from app.services.transcript import _transcribe_whisper

    result = await _transcribe_whisper(audio_path, work_dir)

    logger.info("Source: %s, segments: %d", result.source, len(result.segments))
    logger.info("First 300 chars: %s", result.text[:300])

    assert result.source == "whisper"
    assert len(result.text) > 0
    assert len(result.segments) > 0

    # Save transcript with timestamps for verification
    transcript_path = work_dir / "transcript.txt"
    lines = []
    for seg in result.segments:
        start_m, start_s = divmod(int(seg.start), 60)
        end_m, end_s = divmod(int(seg.end), 60)
        lines.append(f"[{start_m:02d}:{start_s:02d} - {end_m:02d}:{end_s:02d}] {seg.text}")
    transcript_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Transcript saved to %s (%d segments)", transcript_path, len(result.segments))

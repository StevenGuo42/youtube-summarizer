import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import torch

from app.config import COOKIES_PATH, WHISPER_MODEL_DIR

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list[Segment] = field(default_factory=list)
    source: str = ""  # "captions" or "whisper"


async def extract_transcript(
    video_id: str, video_path: Path | None, work_dir: Path
) -> TranscriptResult:
    """Extract transcript: try YouTube captions first, fall back to faster-whisper."""
    result = await _try_captions(video_id, work_dir)
    if result:
        logger.info("Got captions for %s (%d segments)", video_id, len(result.segments))
        return result

    if video_path and video_path.exists():
        logger.info("No captions for %s, falling back to whisper", video_id)
        return await _transcribe_whisper(video_path, work_dir)

    raise RuntimeError(f"No captions and no video file for {video_id}")


async def _try_captions(video_id: str, work_dir: Path) -> TranscriptResult | None:
    """Try to get YouTube captions via yt-dlp."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "json3",
        "skip_download": True,
        "outtmpl": str(work_dir / "%(id)s"),
    }
    if COOKIES_PATH.exists():
        opts["cookiefile"] = str(COOKIES_PATH)

    def _fetch():
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=True
            )

    try:
        await asyncio.to_thread(_fetch)
    except Exception:
        logger.debug("Caption fetch failed for %s", video_id)
        return None

    # yt-dlp writes subs as <id>.en.json3
    sub_file = work_dir / f"{video_id}.en.json3"
    if not sub_file.exists():
        return None

    return _parse_json3(sub_file)


def _parse_json3(path: Path) -> TranscriptResult:
    """Parse yt-dlp json3 subtitle format."""
    data = json.loads(path.read_text())
    segments = []
    for event in data.get("events", []):
        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        segments.append(Segment(
            start=start_ms / 1000.0,
            end=(start_ms + duration_ms) / 1000.0,
            text=text,
        ))

    full_text = " ".join(s.text for s in segments)
    return TranscriptResult(text=full_text, segments=segments, source="captions")


def _get_whisper_model_config() -> tuple[str, str, torch.dtype]:
    """Return (model_name, device, compute_type) based on GPU availability."""
    if torch.cuda.is_available():
        return "deepdeepmind/distil-large-v3", "cuda", "float16"
    return "small", "cpu", "float32"


async def _transcribe_whisper(video_path: Path, work_dir: Path) -> TranscriptResult:
    """Transcribe audio using faster-whisper."""
    audio_path = work_dir / "audio.wav"
    await _extract_audio(video_path, audio_path)

    model_name, device, compute_type = _get_whisper_model_config()
    logger.info("Using whisper model=%s device=%s compute_type=%s", model_name, device, compute_type)

    def _transcribe():
        from faster_whisper import WhisperModel

        WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(WHISPER_MODEL_DIR),
        )
        result_segments, _info = model.transcribe(str(audio_path))
        segments = []
        for seg in result_segments:
            segments.append(Segment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
            ))
        return segments

    segments = await asyncio.to_thread(_transcribe)
    full_text = " ".join(s.text for s in segments)
    logger.info("Whisper transcribed %d segments", len(segments))
    return TranscriptResult(text=full_text, segments=segments, source="whisper")


async def _extract_audio(video_path: Path, audio_path: Path):
    """Extract audio from video using ffmpeg."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path), "-y",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {stderr.decode()}")

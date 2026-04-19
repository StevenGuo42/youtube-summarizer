import asyncio
import gc
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import torch

from app.config import COOKIES_PATH, WHISPER_MODEL_DIR
from app.services.ytdlp import _base_opts

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
    language: str | None = None



async def extract_transcript(
    video_id: str, video_path: Path | None, work_dir: Path,
    whisper_model=None,
) -> TranscriptResult:
    """Extract transcript: try YouTube captions first, fall back to faster-whisper."""
    result = await _try_captions(video_id, work_dir)
    if result:
        logger.info("Got captions for %s (%d segments)", video_id, len(result.segments))
        return result

    if video_path and video_path.exists():
        logger.info("No captions for %s, falling back to whisper", video_id)
        return await _transcribe_whisper(video_path, work_dir, whisper_model=whisper_model)

    raise RuntimeError(f"No captions and no video file for {video_id}")


async def _try_captions(video_id: str, work_dir: Path) -> TranscriptResult | None:
    """Try to get YouTube captions via yt-dlp. Auto-detects language."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    def _get_sub_info():
        import yt_dlp
        with yt_dlp.YoutubeDL({**_base_opts(), "skip_download": True}) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(_get_sub_info)
    except Exception:
        logger.debug("Subtitle info fetch failed for %s", video_id)
        return None

    subtitles = info.get("subtitles") or {}
    auto_captions = info.get("automatic_captions") or {}
    video_lang = info.get("language")

    lang = None
    if video_lang and video_lang in subtitles:
        lang = video_lang
    elif video_lang and video_lang in auto_captions:
        lang = video_lang
    elif subtitles:
        lang = next(iter(subtitles))
    elif auto_captions:
        lang = next(iter(auto_captions))

    if not lang:
        return None

    logger.debug("Selected captions in '%s' for %s (manual=%s)", lang, video_id, lang in subtitles)

    opts = {
        **_base_opts(),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "json3",
        "skip_download": True,
        "outtmpl": str(work_dir / "%(id)s"),
    }

    def _fetch():
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)

    try:
        await asyncio.to_thread(_fetch)
    except Exception:
        logger.debug("Caption fetch failed for %s", video_id)
        return None

    sub_file = work_dir / f"{video_id}.{lang}.json3"
    if not sub_file.exists():
        files = list(work_dir.glob(f"{video_id}.*.json3"))
        if not files:
            return None
        sub_file = files[0]
        lang = sub_file.name.removeprefix(f"{video_id}.").removesuffix(".json3")

    result = _parse_json3(sub_file)
    result.language = lang
    return result


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


def _detect_language(audio_path: str) -> str | None:
    """Detect audio language using whisper small model (processes first 30s only).

    Always runs on CPU to preserve GPU VRAM for the large transcription model.
    """
    from faster_whisper import WhisperModel

    WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    try:
        model = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8",
            download_root=str(WHISPER_MODEL_DIR),
        )
        _, info = model.transcribe(audio_path, beam_size=1, without_timestamps=True)
        logger.info("Detected language: %s (%.1f%% confidence)", info.language, info.language_probability * 100)
        return info.language
    except Exception as e:
        logger.warning("Language detection failed: %s", e)
        return None
    finally:
        del model


def load_whisper_model():
    """Load the best available whisper model. Returns WhisperModel instance.

    Tries GPU large-v3 first, falls back to CPU small.
    Caller is responsible for cleanup.
    """
    from faster_whisper import WhisperModel

    WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if torch.cuda.is_available():
        try:
            model = WhisperModel(
                "Systran/faster-whisper-large-v3",
                device="cuda",
                compute_type="float16",
                download_root=str(WHISPER_MODEL_DIR),
            )
            logger.info("Loaded whisper large-v3 on GPU")
            return model
        except Exception as e:
            logger.warning("Failed to load GPU whisper: %s", e)

    model = WhisperModel(
        "small",
        device="cpu",
        compute_type="float32",
        download_root=str(WHISPER_MODEL_DIR),
    )
    logger.info("Loaded whisper small on CPU")
    return model


async def _transcribe_whisper(video_path: Path, work_dir: Path, whisper_model=None) -> TranscriptResult:
    """Transcribe audio using faster-whisper. Tries GPU first, falls back to CPU."""
    audio_path = work_dir / "audio.wav"
    await _extract_audio(video_path, audio_path)

    def _transcribe():
        from faster_whisper import WhisperModel

        WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        # Detect language first using the small model
        language = _detect_language(str(audio_path))

        if whisper_model:
            # Use pre-loaded model
            result_segments, _info = whisper_model.transcribe(
                str(audio_path), language=language,
            )
            segments = []
            for seg in result_segments:
                segments.append(Segment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text.strip(),
                ))
            return segments, language

        configs = []
        if torch.cuda.is_available():
            configs.append(("Systran/faster-whisper-large-v3", "cuda", "float16"))
        configs.append(("small", "cpu", "float32"))

        last_error = None
        for model_name, device, compute_type in configs:
            logger.info("Trying whisper model=%s device=%s compute_type=%s language=%s",
                        model_name, device, compute_type, language)
            model = None
            try:
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(WHISPER_MODEL_DIR),
                )
                result_segments, _info = model.transcribe(
                    str(audio_path), language=language,
                )
                segments = []
                for seg in result_segments:
                    segments.append(Segment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text.strip(),
                    ))
                return segments, language
            except Exception as e:
                last_error = e
                logger.warning("Whisper failed with %s/%s: %s", model_name, device, e)
                continue
            finally:
                if device == "cuda":
                    del model
                    gc.collect()
                    torch.cuda.empty_cache()

        raise RuntimeError(f"All whisper configurations failed: {last_error}")

    segments, language = await asyncio.to_thread(_transcribe)
    full_text = " ".join(s.text for s in segments)
    logger.info("Whisper transcribed %d segments", len(segments))
    return TranscriptResult(text=full_text, segments=segments, source="whisper", language=language)


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

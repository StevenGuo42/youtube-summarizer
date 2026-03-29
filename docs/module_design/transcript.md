# transcript — `app/services/transcript.py`

Caption extraction with faster-whisper fallback.

## Responsibilities

- Extract YouTube built-in captions (primary method)
- Transcribe audio with faster-whisper when no captions available (fallback)
- Output timestamped transcript as `TranscriptResult`

## Key Design Decisions

- Primary: use yt-dlp to fetch captions in json3 format (`writesubtitles`, `writeautomaticsub`, `subtitleslangs: [en]`, `skip_download`)
- Fallback: extract audio via ffmpeg (16kHz mono WAV), transcribe with faster-whisper
- Caption fetch errors are caught and trigger whisper fallback (not a hard failure)
- Uses `_base_opts()` from ytdlp service to get cookies + JS runtime config

### Language Detection

- `_detect_language()` runs before transcription using the `small` model (first 30s only)
- GPU: `small` model with float16 (~1.7s), CPU: `small` model with int8 (~2.5s)
- Detected language passed to main transcription via `language=` parameter
- Model freed after detection before main transcription loads

### GPU Support (Whisper)

- GPU path: `Systran/faster-whisper-large-v3` (non-distilled, multilingual, ~3.8GB VRAM) on CUDA with float16
- CPU fallback: `small` model with float32
- Detection: `torch.cuda.is_available()` — if GPU available, try it first; on failure, fall back to CPU
- Models downloaded on first use, cached in `data/whisper_models/`
- VRAM cleanup: model reference set to `None` + `torch.cuda.empty_cache()` in `finally` block after GPU transcription
- Must use `Systran/faster-*` repos (CTranslate2 format), NOT `distil-whisper/*` (Transformers format)
- Do NOT use `faster-distil-whisper-large-v3` — it is English-only and produces gibberish for other languages

## Interface

```python
@dataclass
class Segment:
    start: float
    end: float
    text: str

@dataclass
class TranscriptResult:
    text: str
    segments: list[Segment]
    source: str  # "captions" or "whisper"

async def extract_transcript(video_id: str, video_path: Path | None, work_dir: Path) -> TranscriptResult
def inject_ocr_into_transcript(transcript: TranscriptResult, ocr_results: list) -> TranscriptResult
```

Creates a new `TranscriptResult` with OCR text inserted as zero-duration `[OCR TEXT: ...]` segments at their keyframe timestamps. Does not mutate the input. Used by inline OCR modes.

## Dependencies

- `yt-dlp` for caption download (via `_base_opts()` from ytdlp service)
- `faster-whisper` for local fallback transcription
- `torch` for GPU detection
- `ffmpeg` for audio extraction
- `app.config` for COOKIES_PATH, WHISPER_MODEL_DIR

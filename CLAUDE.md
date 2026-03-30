# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTube Video Summarizer — self-hosted web app that summarizes YouTube videos using keyframe extraction and transcript analysis. Full MVP spec in `spec.md`.

## Tech Stack

Python 3.14 + uv, FastAPI + uvicorn, vanilla HTML/JS + Pico CSS, SQLite (aiosqlite), yt-dlp (+yt-dlp-ejs), ffmpeg, Claude Agent SDK (LLM). Node.js required (via nvm) for yt-dlp YouTube JS challenge solving. NVIDIA RTX 5060 (8GB VRAM) — be careful with VRAM usage.

Always use  `uv` to install libraries. Do NOT put design docs on git.

## Commands

```bash
uv sync                        # Install dependencies
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
uv run pytest                  # Run all tests
uv run pytest tests/test_foo.py::test_bar  # Single test
uv run pytest -k "test_name"   # Run tests matching pattern
```

## Testing

Tests use **pytest** + **pytest-asyncio**. Tests that hit real YouTube require network access. Use `tmp_path` fixture for temp directories in download tests. Test logs auto-save to `logs/tests/<module>_<timestamp>.log` (3 per module kept) via `tests/conftest.py`. Use `logger = logging.getLogger(__name__)` in test files. All tests must log their results — print text results directly, and log file paths for non-text results (images, audio, video).

## Architecture

For any functionality changes, /docs/module_design must be updated first. 

**Pipeline:** Download (yt-dlp) → Transcript (captions/Whisper) → Keyframes (ffmpeg) → Dedup (pHash/SSIM) → OCR (chandra, on deduped frames only) → Summarize (LLM) → Cleanup. One job at a time via async task queue.

**Backend (`app/`):** `main.py` (FastAPI app), `config.py`, `database.py`, `routers/` (auth, browse, queue, summaries, settings), `services/` (ytdlp, transcript, keyframes, ocr, llm, pipeline), `queue/worker.py`.

**Frontend (`app/static/`):** Single-page app, Pico CSS, semantic HTML, dark theme default.

**Data:** SQLite + cookies + settings in `data/`. Temp files in `data/tmp/<job_id>/`, cleaned per-job. API keys never exposed back to frontend.

**LLM:** Claude-only via `claude-agent-sdk`. Auth via OAuth (Claude Max plan, `claude auth login`). Bundled CLI is a native binary — no Node.js needed for the SDK. Custom prompts supported.

**yt-dlp:** Requires `yt-dlp-ejs` + Node.js runtime for YouTube JS challenge solving. `_base_opts()` centralizes shared config (cookies, JS runtime). `app/config.py` auto-adds nvm Node.js to PATH. All yt-dlp consumers (ytdlp, transcript) must use `_base_opts()`.

**OCR:** chandra-ocr-2 via `transformers` + `chandra-ocr[hf]`. 4-bit quantized with `bitsandbytes` (~2.5 GB VRAM). Extracts on-screen text from keyframes. Models auto-downloaded from HuggingFace to `data/ocr_models/`. Prompt type `"ocr"` (handles tables, math, code).

**Keyframe Dedup:** `deduplicate_keyframes()` in keyframes.py. Four modes: `regular` (pHash hamming >5), `slides` (SSIM <0.95, better for presentations), `ocr` (fuzzy text match), `none`. Keeps last frame per group. CLI flag: `--dedup {regular,slides,ocr,none}`.

**Transcript format:** XML-tagged blocks with timestamp ranges. `<transcript>` for speech, `<ocr_text>` for inline OCR. No per-segment timestamps — keyframe range provides timing context.

**Whisper:** Language auto-detected via `small` model before transcription. GPU uses `Systran/faster-whisper-large-v3` (non-distilled, multilingual, ~3.8GB VRAM). Do NOT use distilled variant — English-only.

**GPU:** NVIDIA RTX 5060 (8GB VRAM). Whisper transcription (faster-whisper + CUDA), ffmpeg decoding (`-hwaccel cuda`), and OCR inference (transformers + CUDA) use GPU when available, with automatic CPU fallback on failure. Keep VRAM usage conservative — 8GB is the limit.

**Error handling:** Pipeline steps fail independently — partial results are still saved.

**Module design docs:** `docs/module_design/<module>.md` — detailed design for each service module (interfaces, decisions, dependencies). Read these before implementing or modifying a module.

<!-- GSD:project-start source:PROJECT.md -->
## Project

**YouTube Video Summarizer**

A self-hosted web app that summarizes YouTube videos using keyframe extraction, OCR, and transcript analysis. Runs locally with a FastAPI backend, SQLite database, and vanilla JS frontend. Supports members-only content via cookies, GPU-accelerated processing, and Claude-powered summarization.

**Core Value:** Submit a YouTube URL and get back a structured, visual-aware summary — the app must reliably process videos through the full pipeline (download, transcript, keyframes, OCR, summarize) and present results in a usable interface.

### Constraints

- **Tech stack**: Python 3.14 + uv, FastAPI, vanilla HTML/JS + Pico CSS, SQLite — no frameworks, no build step
- **LLM**: Claude-only via claude-agent-sdk with OAuth auth — no API key needed
- **GPU**: 8GB VRAM limit — conservative usage, one model at a time
- **Frontend**: Vanilla JS, semantic HTML, Pico CSS — no React/Vue/etc.
- **Design docs**: Must not be committed to git
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.14 - Backend application, video processing, ML inference
- HTML/CSS/JavaScript - Frontend UI (single-page app, vanilla, no frameworks)
- SQL - Database queries and schema management
- Node.js (via nvm) - Required by yt-dlp-ejs for YouTube JavaScript challenge solving (not for the app itself)
## Runtime
- Python 3.14 (specified in `.python-version`)
- Node.js (managed via nvm, required by yt-dlp EJS challenge solver)
- uv (modern Python package manager) - Primary tool for dependency management and venv creation
- Lockfile: `pyproject.toml` (no lock file detected; dependencies pinned in pyproject.toml)
## Frameworks
- FastAPI 0.135.1+ - REST API framework for backend services
- uvicorn 0.42.0+ - ASGI server for running FastAPI application
- Pico CSS - Lightweight CSS framework for semantic HTML styling (dark theme default)
- Vanilla JS - No frontend framework; direct DOM manipulation
- pytest 9.0.2+ - Test runner
- pytest-asyncio 1.3.0+ - Async test support
- uv sync - Dependency resolution and virtual environment setup
## Key Dependencies
- aiosqlite 0.22.1+ - Async SQLite driver for database operations
- yt-dlp 2026.3.17+ - YouTube video download with metadata extraction
- yt-dlp-ejs 0.8.0+ - yt-dlp plugin for JavaScript challenge solving (YouTube members-only content, requires Node.js runtime)
- claude-agent-sdk 0.1.50+ - Claude AI agent integration via Agent SDK (native CLI binary bundled)
- anthropic 0.86.0+ - Anthropic SDK (unused; claude-agent-sdk is primary)
- ffmpeg (system binary) - Video decoding, audio extraction, keyframe extraction with scene detection
- faster-whisper 1.2.1+ - GPU-accelerated speech-to-text (non-distilled `Systran/faster-whisper-large-v3` for multilingual, ~3.8GB VRAM)
- Pillow 12.1.1+ - Image processing, downscaling, format conversion
- torch (via transformers/bitsandbytes) - PyTorch for GPU/CPU inference
- transformers (via chandra-ocr) - HuggingFace transformers for model loading
- chandra-ocr 0.2.0+ with HuggingFace backend - OCR on keyframes via `datalab-to/chandra-ocr-2` model
- bitsandbytes 0.49.2+ - 4-bit quantization for OCR model (GPU: ~2.5GB VRAM, CPU: float32)
- imagehash 4.3.2+ - Perceptual hashing for keyframe deduplication (pHash algorithm)
- scikit-image 0.26.0+ - Structural similarity (SSIM) for slide-mode deduplication
- httpx 0.28.1+ - Async HTTP client (likely used by SDK integrations)
- python-multipart 0.0.22 - Form data parsing for FastAPI file uploads
- openai 2.29.0+ - OpenAI SDK (not primary LLM provider)
- openai-whisper 20250625+ - Fallback for Whisper transcription (distilled variant included but NOT used due to English-only limitation)
- google-genai 1.68.0+ - Google Gemini SDK (not confirmed used)
## Configuration
- `.python-version` file specifies Python 3.14
- `app/config.py` centralizes all configuration:
- `pyproject.toml` - Project metadata, dependencies, and pytest configuration
- FastAPI lifespan context manager in `app/main.py` handles startup (init_db, start_worker) and shutdown (stop_worker)
## Platform Requirements
- Python 3.14
- Node.js (via nvm) for yt-dlp JavaScript challenge solving
- ffmpeg (system binary, e.g., `apt-get install ffmpeg`)
- NVIDIA CUDA toolkit (optional, for GPU acceleration):
- Self-hosted Docker container (per spec.md)
- Python 3.14 runtime
- Node.js runtime (for yt-dlp-ejs)
- ffmpeg binary
- NVIDIA GPU optional (but recommended for performance): RTX 5060 (8GB VRAM) is target hardware
- 8GB VRAM is the practical limit; keep model loading conservative
## LLM Configuration
- **Auth:** OAuth (`claude auth login` for Max plan users)
- **Models:** Configurable default `claude-sonnet-4-20250514`, stored in database
- **Custom Prompts:** Supported via `llm_settings.custom_prompt`
- **Bundled CLI:** Native binary included with `claude-agent-sdk` (no Node.js needed for SDK itself)
- **Agent Tools:** Read tool enabled for keyframe modes that require image/OCR file access
## Notes
- **yt-dlp JS Runtime:** `app/config.py` auto-detects nvm-managed Node.js and adds to PATH. Required for YouTube members-only content and JavaScript challenges.
- **Model Caching:** Whisper and OCR models auto-downloaded from HuggingFace on first use; cached locally in `data/` directory.
- **VRAM Management:** Whisper + OCR + ffmpeg GPU operations must stay under 8GB total. Explicit cache clearing via `torch.cuda.empty_cache()` after inference.
- **GPU Fallback:** All GPU-dependent operations have CPU fallbacks with automatic switching on CUDA failure.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- All lowercase with underscores: `ytdlp.py`, `keyframes.py`, `pipeline.py`
- Routers prefixed with context: `auth.py`, `queue.py`, `settings.py`, `browse.py`, `summaries.py`
- Test files match module name with `test_` prefix: `test_ytdlp.py`, `test_pipeline.py`
- Async functions named with full words, no `async_` prefix: `async def extract_keyframes()`, `async def summarize()`
- Private/internal functions prefixed with single underscore: `_base_opts()`, `_dedup_by_phash()`, `_check_nvidia_hwaccel()`
- Synchronous worker functions inside async module are inline with `def _inner()` pattern: See `app/services/ytdlp.py:51` where `_download()` wraps yt-dlp calls
- camelCase avoided entirely — use snake_case consistently: `video_id`, `keyframe_mode`, `work_dir`, `job_ids`
- Booleans are prefixed with `is_`, `has_`, `needs_`: `needs_ocr`, `gpu`, `_nvidia_hwaccel`
- Module-level cache/state variables prefixed with single underscore: `_tmp_cookies`, `_worker_task`, `_cancelled`, `_nvidia_hwaccel`, `_active_handlers`
- Dataclasses for lightweight structures: `KeyFrame`, `Segment`, `TranscriptResult`, `OcrResult`, `SummaryResult`
- Enums for fixed option sets: `KeyframeMode(str, Enum)` with values like `"image"`, `"ocr"`, `"ocr+image"`
- Request/Response models inherit from Pydantic `BaseModel`: `QueueRequest`, `LLMConfig`, `LLMConfigResponse`
## Code Style
- No explicit formatter configured (no .ruff.toml, .black.cfg, or .pylintrc found)
- Imports follow standard grouping: stdlib, third-party, local app imports
- Line length appears target ~100 characters (most lines under this)
- Type hints used consistently throughout
- No explicit linting config — project follows standard Python patterns
- Exception handling explicit (never bare `except:`)
## Import Organization
- No aliases used. Imports always use absolute paths from project root: `from app.config import`, never `from .config import`
- Relative imports prohibited
- `app/services/pipeline.py:1-14` shows stdlib, third-party, then app imports in order
- `app/services/transcript.py:1-12` imports `from app.config import COOKIES_PATH, WHISPER_MODEL_DIR` for config constants
## Error Handling
- Try-except with specific exception type: `except Exception:` when broad catch needed, specific types (`pytest.skip()`, `yt_dlp.utils.DownloadError`) when appropriate
- Errors logged immediately with context: `logger.exception("[%s] Download failed", job_id)` (see `app/services/pipeline.py:105`)
- Pipeline failures are non-fatal — partial results saved with warnings via `_add_warning()` helper (see `app/services/pipeline.py:53-68`)
- Database errors handled with try-finally pattern to ensure connection closure (see `app/services/pipeline.py:37-50`)
- Service functions may raise exceptions; callers decide handling
- Pipeline catches exceptions per step and logs them (see `app/services/pipeline.py:98-225`)
- Router endpoints catch exceptions and re-raise as HTTPException (see `app/routers/queue.py:29-30`)
## Logging
- Logger created at module level: `logger = logging.getLogger(__name__)`
- Info-level for operations: `logger.info("[%s] Downloaded: %s", job_id, video_path)` (includes job_id context)
- Exception logging with full traceback: `logger.exception("[%s] Download failed", job_id)` (see `app/services/pipeline.py:105`)
- Warning-level for degraded paths: `logger.warning("[%s] No video file, skipping keyframes", job_id)` (see `app/services/pipeline.py:127`)
- Debug-level for detailed flow: `logger.debug("Caption fetch failed for %s", video_id)` (see `app/services/transcript.py:70`)
- Job logs include `[job_id]` prefix for traceability
- Numeric details logged alongside descriptions: `logger.info("Extracted %d keyframes", len(keyframes))`
## Comments
- Used sparingly — code is generally self-documenting via clear naming
- Docstrings on public async functions explain behavior and return values
- Inline comments explain WHY, not WHAT: See `app/services/ytdlp.py:9-10` "yt-dlp modifies cookiefile in place (writes back rotated cookies from YouTube)."
- Complex logic explained: See `app/services/pipeline.py:140-144` explains OCR dedup ordering decision
- Not used (Python project)
- Function docstrings are minimal — one-line summaries with param/return types in type hints
- Module docstrings on test files explain purpose: See `tests/test_pipeline.py:1` "Tests for the complete pipeline — requires network, cookies, and Claude auth."
## Function Design
- Service functions 20-50 lines typical
- Pipeline steps extracted to separate functions (extract_transcript, extract_keyframes, etc.)
- Largest functions are orchestrators like `process_job()` (~200 lines) and `_build_interleaved_transcript()` (~150 lines)
- Positional for required args: `async def extract_keyframes(video_path: Path, work_dir: Path)`
- Keyword-only for options: `async def summarize(..., keyframe_mode: KeyframeMode = KeyframeMode.IMAGE, ocr_paths: list[Path | None] | None = None)`
- **kwargs for dynamic updates in database helpers: `async def _update_job(job_id: str, **kwargs)` (see `app/services/pipeline.py:37`)
- Single return type always explicit: `-> Path`, `-> list[KeyFrame]`, `-> SummaryResult`
- Unions: `-> TranscriptResult | None`, `-> tuple[list[KeyFrame], list | None]`
- Void returns omitted (implicit None)
## Module Design
- Services export public functions only: `async def extract_keyframes()`, `async def extract_transcript()`
- Types are exported as needed: `KeyFrame`, `TranscriptResult`, `OcrResult`, `KeyframeMode`
- Private helpers prefixed with underscore and not imported elsewhere
- Empty `__init__.py` in `app/services/`, `app/routers/`, `app/queue/`
- No wildcard imports — explicit imports required
- `app/config.py`: Configuration constants and nvm PATH setup
- `app/database.py`: Database connection + init
- `app/queue/worker.py`: Async task queue management
- `app/routers/`: FastAPI route handlers (thin layer over services)
- `app/services/`: Business logic (download, transcript, keyframes, OCR, LLM, pipeline)
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- Single job-at-a-time sequential processing with optional batch mode
- REST API routes incoming requests to job queue
- Async background worker processes pipeline steps
- Database tracks job state through multiple processing stages
- Each pipeline step is independent — partial failures don't halt processing
- GPU utilization optimized via model reuse and hardware-accelerated decoding
## Layers
- Purpose: Accept incoming requests and return data
- Location: `app/routers/`
- Contains: FastAPI route handlers for auth, browse, queue, settings, summaries
- Depends on: Database, services, queue worker
- Used by: Frontend via HTTP, external clients
- Purpose: Implement reusable domain logic for video processing
- Location: `app/services/`
- Contains: ytdlp (download), transcript (captions/whisper), keyframes (scene detection), ocr (text extraction), llm (summarization)
- Depends on: External libraries (yt-dlp, faster-whisper, transformers, Claude SDK)
- Used by: Pipeline orchestrator
- Purpose: Coordinate service execution in correct order with state management
- Location: `app/services/pipeline.py`
- Contains: Two execution modes (sequential `process_job`, batch `process_batch`)
- Depends on: All services, database, logging
- Used by: Queue worker
- Purpose: Manage job queue and trigger pipeline execution
- Location: `app/queue/worker.py`
- Contains: AsyncIO-based queue, processing mode dispatcher, job re-queueing
- Depends on: Pipeline, database
- Used by: FastAPI lifespan events
- Purpose: Persistent storage and state tracking
- Location: `app/database.py`
- Contains: SQLite schema (jobs, summaries, llm_settings, worker_settings)
- Depends on: aiosqlite
- Used by: All other layers
- Purpose: User interface for browsing, queueing, and viewing results
- Location: `app/static/`
- Contains: Semantic HTML (Pico CSS), placeholder JavaScript
- Depends on: REST API
- Used by: Browser
## Data Flow
- Job status field: pending → processing → done/failed/cancelled
- Current step tracked during processing (downloading, transcribing, extracting_keyframes, deduplicating, ocr, summarizing, cleanup)
- Warnings accumulated as JSON array (partial failures don't fail job)
- Temp files isolated in `data/tmp/<job_id>/`, deleted on completion
- SQLite used for durability — jobs re-queued on restart if pending/processing
## Key Abstractions
- Purpose: Represents extracted image at a specific timestamp
- Location: `app/services/keyframes.py` (line 52-55)
- Attributes: timestamp (float), image_path (Path)
- Pattern: Passed through dedup → OCR → LLM stages
- Purpose: Represents extracted or transcribed text with timing
- Location: `app/services/transcript.py` (line 15-26)
- Attributes: text (full text), segments (list[Segment]), source ("captions" or "whisper")
- Pattern: Segments have start/end times; full text used if segments unavailable
- Purpose: Represents text extracted from a specific keyframe image
- Location: `app/services/ocr.py` (line 16-20)
- Attributes: timestamp, image_path, text
- Pattern: Parallel list to keyframes, indexed identically
- Purpose: Determines what data to send to Claude for summarization
- Location: `app/services/llm.py` (line 18-24)
- Values: IMAGE, OCR, OCR_IMAGE, OCR_INLINE, OCR_INLINE_IMAGE, NONE
- Pattern: Controls which Read tool calls Claude can use, what content blocks generated
- Purpose: Parsed output from Claude
- Location: `app/services/llm.py` (line 79-84)
- Attributes: raw_response (full text), title, tldr, summary
- Pattern: JSON parsed from Claude, fallback to raw text if parse fails
## Entry Points
- Location: `app/main.py`
- Triggers: `uvicorn app.main:app`
- Responsibilities: Mount routers, initialize database, start/stop worker, serve static files
- Location: `app/queue/worker.py` (`_worker_loop()`)
- Triggers: Lifespan startup event
- Responsibilities: Dequeue jobs, dispatch to process_job/process_batch, handle cancellation
- Location: `app/services/pipeline.py` (`process_job()`)
- Triggers: Worker loop when job dequeued
- Responsibilities: Execute all 7 steps in sequence, update job status, handle partial failures
- Location: `app/services/pipeline.py` (`process_batch()`)
- Triggers: Worker loop when batch_size jobs available (configured via settings)
- Responsibilities: Run each step across multiple jobs before advancing, GPU model reuse
## Error Handling
- Download fails → Skip keyframe extraction, attempt transcript via captions only
- Captions unavailable → Fall back to faster-whisper on downloaded video
- Keyframe extraction fails → Summarize with transcript only
- Dedup fails → Use all keyframes, warn user
- OCR fails → Switch to image-only mode, warn user
- Transcript + keyframes both fail → Mark job failed, stop processing
- Summarization fails → Mark job failed, warn but don't continue
- Any step exception caught, logged with job_id prefix, warning appended to job record
## Cross-Cutting Concerns
- Each service uses `logger = logging.getLogger(__name__)`
- Pipeline logs include `[job_id]` prefix for tracing
- All exceptions logged with `logger.exception()` to include stack traces
- Test logs auto-captured to `logs/tests/<module>_<timestamp>.log`
- QueueRequest validates video_ids, dedup_mode, keyframe_mode
- LLMConfig validates model string format
- WorkerConfig validates processing_mode (sequential/batch) and batch_size
- yt-dlp URL construction uses f-string templates
- YouTube cookies stored in `data/cookies.txt`
- Claude auth via `~/.claude/.credentials.json` (managed by Claude SDK)
- API keys never exposed to frontend
- yt-dlp uses optional cookies + Node.js JS runtime for members-only content
- NVIDIA GPU available (RTX 5060, 8GB VRAM) — conservative usage
- Whisper model (~3.8GB) loaded once per job
- OCR model (~2.5GB) loaded once per batch
- ffmpeg uses `-hwaccel cuda` when available
- Temp files cleaned per-job (shutil.rmtree)
- Model release via `del model; gc.collect(); torch.cuda.empty_cache()`
- Single async worker loop processes one (or one batch) at a time
- No concurrent job processing within a single worker instance
- Multiple workers possible (not implemented) via separate uvicorn processes
- AsyncIO for I/O concurrency (DB, network calls, subprocess)
- Thread pool for CPU-bound work (yt-dlp, whisper, OCR) via asyncio.to_thread()
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

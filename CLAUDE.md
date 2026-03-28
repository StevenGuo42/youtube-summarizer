# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTube Video Summarizer — self-hosted web app that summarizes YouTube videos using keyframe extraction and transcript analysis. Full MVP spec in `spec.md`.

## Tech Stack

Python 3.14 + uv, FastAPI + uvicorn, vanilla HTML/JS + Pico CSS, SQLite (aiosqlite), yt-dlp (+yt-dlp-ejs), ffmpeg, Claude Agent SDK (LLM). Node.js required (via nvm) for yt-dlp YouTube JS challenge solving. NVIDIA RTX 5060 (8GB VRAM) — be careful with VRAM usage.

Always use  `uv` to install libraries. 

## Commands

```bash
uv sync                        # Install dependencies
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
uv run pytest                  # Run all tests
uv run pytest tests/test_foo.py::test_bar  # Single test
uv run pytest -k "test_name"   # Run tests matching pattern
```

## Testing

Tests use **pytest** + **pytest-asyncio**. Tests that hit real YouTube require network access. Use `tmp_path` fixture for temp directories in download tests. Test logs auto-save to `logs/tests/<module>_<timestamp>.log` (3 per module kept) via `tests/conftest.py`. Use `logger = logging.getLogger(__name__)` in test files.

## Architecture

For any functionality changes, /docs/module_design must be updated first. 

**Pipeline:** Download (yt-dlp) → Transcript (captions/Whisper) → Keyframes (ffmpeg) → OCR (chandra) → Summarize (LLM) → Cleanup. One job at a time via async task queue.

**Backend (`app/`):** `main.py` (FastAPI app), `config.py`, `database.py`, `routers/` (auth, browse, queue, summaries, settings), `services/` (ytdlp, transcript, keyframes, ocr, llm, pipeline), `queue/worker.py`.

**Frontend (`app/static/`):** Single-page app, Pico CSS, semantic HTML, dark theme default.

**Data:** SQLite + cookies + settings in `data/`. Temp files in `data/tmp/<job_id>/`, cleaned per-job. API keys never exposed back to frontend.

**LLM:** Claude-only via `claude-agent-sdk`. Auth via OAuth (Claude Max plan, `claude auth login`). Bundled CLI is a native binary — no Node.js needed for the SDK. Custom prompts supported.

**yt-dlp:** Requires `yt-dlp-ejs` + Node.js runtime for YouTube JS challenge solving. `_base_opts()` centralizes shared config (cookies, JS runtime). `app/config.py` auto-adds nvm Node.js to PATH. All yt-dlp consumers (ytdlp, transcript) must use `_base_opts()`.

**OCR:** chandra-ocr-2 via `llama-cpp-python` with GGUF quantized models (default Q4_K_M). Extracts on-screen text from keyframes. Models auto-downloaded from HuggingFace to `data/ocr_models/`. Requires CUDA build: `CMAKE_ARGS="-DGGML_CUDA=on" uv pip install --force-reinstall --no-binary llama-cpp-python --no-cache llama-cpp-python`.

**GPU:** NVIDIA RTX 5060 (8GB VRAM). Whisper transcription (faster-whisper + CUDA), ffmpeg decoding (`-hwaccel cuda`), and OCR inference (llama-cpp-python + CUDA) use GPU when available, with automatic CPU fallback on failure. Keep VRAM usage conservative — 8GB is the limit.

**Error handling:** Pipeline steps fail independently — partial results are still saved.

**Module design docs:** `docs/module_design/<module>.md` — detailed design for each service module (interfaces, decisions, dependencies). Read these before implementing or modifying a module.

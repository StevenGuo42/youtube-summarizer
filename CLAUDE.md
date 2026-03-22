# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTube Video Summarizer — self-hosted web app that summarizes YouTube videos using keyframe extraction and transcript analysis. Full MVP spec in `spec.md`.

## Tech Stack

Python 3.14 + uv, FastAPI + uvicorn, vanilla HTML/JS + Pico CSS, SQLite (aiosqlite), yt-dlp, ffmpeg, multi-provider LLM (Anthropic/OpenAI/Google/Ollama).

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

**Pipeline:** Download (yt-dlp) → Transcript (captions/Whisper) → Keyframes (ffmpeg) → Summarize (LLM) → Cleanup. One job at a time via async task queue.

**Backend (`app/`):** `main.py` (FastAPI app), `config.py`, `database.py`, `routers/` (auth, browse, queue, summaries, settings), `services/` (ytdlp, transcript, keyframes, llm, pipeline), `queue/worker.py`.

**Frontend (`app/static/`):** Single-page app, Pico CSS, semantic HTML, dark theme default.

**Data:** SQLite + cookies + settings in `data/`. Temp files in `data/tmp/<job_id>/`, cleaned per-job. API keys never exposed back to frontend.

**Error handling:** Pipeline steps fail independently — partial results are still saved.

**Module design docs:** `docs/module_design/<module>.md` — detailed design for each service module (interfaces, decisions, dependencies). Read these before implementing or modifying a module.

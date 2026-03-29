# YouTube Video Summarizer — MVP Spec

## Overview

A self-hosted web application that summarizes YouTube videos using keyframe extraction and transcript analysis. Runs in Docker, accessed via browser.

---

## Tech Stack

| Layer      | Technology                                    |
| ---------- | --------------------------------------------- |
| Runtime    | Python 3.14                                   |
| Packaging  | uv (for dependency management and venv)       |
| Backend    | FastAPI + uvicorn                             |
| Frontend   | HTML/CSS/JS (single-page, no framework) + Pico CSS |
| Video DL   | yt-dlp                                        |
| Audio/Frames | ffmpeg                                      |
| Transcription | YouTube built-in captions (primary), OpenAI Whisper (fallback) |
| Queue      | In-memory async task queue (asyncio)          |
| Database   | SQLite (job state, history)                   |
---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                   Browser UI                      │
│  (URL input, auth config, queue, progress, results)│
└──────────────────┬───────────────────────────────┘
                   │ REST API
┌──────────────────▼───────────────────────────────┐
│                FastAPI Backend                     │
│                                                   │
│  ┌─────────┐  ┌──────────┐  ┌─────────────────┐  │
│  │ yt-dlp  │  │  ffmpeg  │  │ LLM Integration │  │
│  │ module  │  │ keyframe │  │ (multi-provider) │  │
│  │         │  │ extract  │  │                  │  │
│  └─────────┘  └──────────┘  └─────────────────┘  │
│                                                   │
│  ┌──────────────────────────────────────────────┐ │
│  │   Async Task Queue (processing pipeline)     │ │
│  └──────────────────────────────────────────────┘ │
│                                                   │
│  ┌──────────────────────────────────────────────┐ │
│  │   SQLite (jobs, history, settings)           │ │
│  └──────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
```

---

## Core Pipeline

Each video goes through these steps sequentially:

### Step 1: Download

- Use `yt-dlp` to download the video.
- Pass auth options (cookies file or OAuth token) if provided.
- Download to a temp working directory inside the container.

### Step 2: Extract Transcript

- **Primary:** Attempt to pull YouTube's built-in captions via `yt-dlp --write-sub --write-auto-sub --sub-lang en --skip-download`.
- **Fallback:** If no captions available, extract audio and transcribe with OpenAI Whisper (`whisper` Python package, `base` or `small` model to keep it fast).
- Output: timestamped transcript (SRT or structured JSON).

### Step 3: Extract Keyframes

- Use `ffmpeg` scene-change detection:
  ```bash
  ffmpeg -i input.mp4 -vf "select='gt(scene,0.3)',showinfo" -vsync vfr frame_%04d.jpg
  ```
- Capture the timestamp of each extracted frame from ffmpeg's showinfo output.
- Apply a cap: max ~30 keyframes per video. If scene detection produces more, subsample evenly. If it produces too few (e.g., a static talking-head video), fall back to uniform interval sampling (1 frame every 60 seconds).
- Downscale frames to max 1024px on the long edge to save tokens.
- Output: list of `(timestamp, image_path)` tuples.

### Step 4: Summarize via LLM

- Build a prompt that includes:
  - The full transcript (or chunked if it exceeds context limits).
  - Keyframe images paired with their timestamps.
  - A system prompt instructing the model to produce a structured summary.
- Send to the user-selected LLM provider/model.
- Output: structured summary (see format below).

### Step 5: Cleanup

- Delete downloaded video, audio, and frame files from temp directory.
- Keep only the transcript and summary in the database.

---

## Summary Output Format

The LLM should return a structured summary with:

```
- Title
- Channel
- Duration
- One-paragraph TL;DR
- Key topics / sections (with timestamps)
- Notable visual elements described (charts, demos, diagrams, etc.)
- Key takeaways (bullet points)
```

Store the raw LLM response and the structured version in SQLite.

---

## API Endpoints

### Authentication & Settings

```
POST   /api/auth/cookies       Upload cookies.txt file (Netscape format)
DELETE /api/auth/cookies       Remove stored cookies
GET    /api/auth/status        Check if cookies are valid / loaded

POST   /api/settings/llm       Save LLM provider config
GET    /api/settings/llm       Get current LLM config
```

### Channel & Video Browsing

```
GET    /api/channel/search?q=<query>          Search channels by name
GET    /api/channel/<id>/videos               List videos for a channel
         ?members_only=true                   Filter to members-only
         ?page=1&per_page=20                  Pagination
         ?date_from=YYYYMMDD&date_to=YYYYMMDD Date range filter
GET    /api/playlist/<id>/videos              List videos in a playlist
GET    /api/video/info?url=<url>              Get metadata for a single video URL
```

### Processing Queue

```
POST   /api/queue                Add video(s) to processing queue
         Body: { "video_ids": ["id1", "id2", ...] }
GET    /api/queue                List all jobs (pending, processing, done, failed)
GET    /api/queue/<job_id>       Get job status + progress
DELETE /api/queue/<job_id>       Cancel a pending/processing job
```

### Results

```
GET    /api/summaries                   List all completed summaries
GET    /api/summaries/<job_id>          Get full summary for a job
DELETE /api/summaries/<job_id>          Delete a summary
GET    /api/summaries/<job_id>/export   Export summary as markdown
```

---

## UI Pages / Views

Single-page app with tab-based navigation. Uses Pico CSS for base styling — write semantic HTML and let Pico handle the look. Key Pico patterns to use:

- Wrap everything in `<main class="container">` for centered responsive layout.
- Use `<nav>` with `<ul>` for the top tab bar.
- Use `<article>` for cards (video items, queue items, summary cards).
- Use `<progress value="3" max="5">` for pipeline progress bars — Pico styles these natively.
- Use `<dialog>` for any modals/confirmations.
- Use `<details><summary>` for expandable summary sections.
- Use `role="group"` on `<div>` wrapping buttons for grouped actions (Select All / Deselect All).
- Use `aria-busy="true"` on elements to show loading spinners.
- Use `<input type="search">` for the URL/search input — Pico adds a search icon.
- Use `data-theme="dark"` on `<html>` — default to dark theme with a toggle in the nav.

### Tab 1: Browse & Queue

- **Input section at top:**
  - Text input for YouTube URL (video, channel, or playlist).
  - "Fetch" button to load videos.
  - Filter: "All" / "Public" / "Members Only" for video visibility.
  - Date range picker: filter videos by upload date (from / to).
- **Video list:**
  - Displays video thumbnails, titles, duration, upload date.
  - Checkboxes for multi-select.
  - "Add Selected to Queue" button.
- **Playlist support:**
  - If a playlist URL is entered, load all videos in that playlist.
  - "Select All" / "Deselect All" convenience buttons.

### Tab 2: Queue & Progress

- List of all queued/processing/completed jobs.
- Each job shows:
  - Video thumbnail + title.
  - Status badge (Pending / Downloading / Extracting / Summarizing / Done / Failed).
  - Progress bar showing current pipeline step (5 steps total).
  - Estimated time remaining (if possible, otherwise just step indicator).
  - Cancel button for pending/in-progress jobs.
- Auto-refreshes via polling (every 2-3 seconds) or SSE.

### Tab 3: Summaries

- List of completed summaries.
- Click to expand and view full summary.
- Copy / Export as markdown buttons.
- Delete button.

### Tab 4: Settings

- **Authentication section:**
  - Cookie file upload (drag-and-drop zone + file picker).
  - Status indicator (cookies loaded: yes/no, last uploaded date).
  - "Test Cookies" button that tries a members-only probe.
  - Clear cookies button.

- **LLM Configuration section:**
  - Dropdown: Provider (OpenAI, Anthropic, Google, Ollama/Local).
  - Based on provider selection, show:
    - API key input field (for cloud providers).
    - API base URL input field (for Ollama / custom endpoints).
    - Model dropdown (populated per provider):
      - Anthropic: claude-sonnet-4-20250514, claude-opus-4-0-20250415
      - OpenAI: gpt-4o, gpt-4o-mini
      - Google: gemini-2.0-flash, gemini-2.5-pro
      - Ollama: text field for model name
  - "Test Connection" button.
  - Save button.
  - API keys stored server-side (in SQLite or a .env file in the mounted volume), never exposed back to the frontend after saving.

---

## Data Persistence

- SQLite DB stored at `data/db.sqlite`.
- Uploaded cookies stored at `data/cookies.txt`.
- LLM settings stored in SQLite.
- Temp video/audio/frame files stored in `data/tmp/<job_id>/`, cleaned up after each job.

---

## Project Structure

```
youtube-summarizer/
├── pyproject.toml
├── uv.lock
├── app/
│   ├── main.py              # FastAPI app, CORS, static mount
│   ├── config.py            # App config, paths, constants
│   ├── database.py          # SQLite setup, models, migrations
│   ├── routers/
│   │   ├── auth.py          # Cookie upload/status endpoints
│   │   ├── browse.py        # Channel/playlist/video browsing
│   │   ├── queue.py         # Job queue management
│   │   ├── summaries.py     # Summary retrieval/export
│   │   └── settings.py      # LLM config endpoints
│   ├── services/
│   │   ├── ytdlp.py         # yt-dlp wrapper (download, metadata, search)
│   │   ├── transcript.py    # Caption extraction + Whisper fallback
│   │   ├── keyframes.py     # ffmpeg keyframe extraction
│   │   ├── llm.py           # Multi-provider LLM client
│   │   └── pipeline.py      # Orchestrates the full pipeline per job
│   ├── queue/
│   │   └── worker.py        # Async task queue + worker loop
│   └── static/
│       ├── index.html        # Single-page app (loads Pico CSS from CDN)
│       ├── style.css         # Pico CSS variable overrides + app-specific layout only
│       └── app.js
```

---

## Key Dependencies

```toml
[project]
requires-python = ">=3.14"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "yt-dlp",
    "openai-whisper",
    "httpx",              # For LLM API calls
    "anthropic",          # Anthropic SDK
    "openai",             # OpenAI SDK
    "google-genai",       # Google Gemini SDK
    "aiosqlite",          # Async SQLite
    "python-multipart",   # File uploads in FastAPI
    "pillow",             # Image resizing for keyframes
]
```

---

## Implementation Notes

- **Concurrency:** Process one video at a time to keep resource usage reasonable. Queue holds pending jobs. Consider making this configurable (1-3 concurrent jobs) in a future iteration.
- **Error handling:** Each pipeline step should catch errors independently. If keyframe extraction fails, still attempt summary with transcript only. If transcript extraction fails, still attempt with keyframes only. Mark the job as "partial" if not all steps succeed.
- **Context window management:** For very long videos, chunk the transcript to fit within the model's context window. Send keyframes proportionally with each chunk, or do a two-pass approach (summarize chunks → summarize summaries).
- **yt-dlp channel/search:** Use `yt-dlp --flat-playlist` for fast metadata-only listing of channels and playlists without downloading.
- **Whisper model size:** Default to `base` for speed. Consider making this configurable in settings. Do NOT download Whisper models at build time — download on first use and cache in the data volume.
- **Frontend:** Keep it vanilla HTML/CSS/JS for the MVP. No build step. Serve from FastAPI's static files. Use `fetch()` for all API calls. Use Pico CSS (`<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">`) as the base styling — it styles semantic HTML elements automatically with no class names needed. Use Pico's built-in dark/light theme toggle via `data-theme` attribute on `<html>`. Override Pico's CSS custom properties (e.g. `--pico-primary`) for branding. Use `<article>`, `<section>`, `<nav>`, `<dialog>`, `<details>`, `<progress>` and other semantic elements — Pico styles them all out of the box. Wrap the page in `<main class="container">` for centered responsive layout. For the tab navigation, use Pico's `role="group"` button groups. Minimal custom CSS on top — only for app-specific layout like the video grid and queue cards.
- **SSE for progress:** Use FastAPI's `StreamingResponse` or SSE (server-sent events) for real-time progress updates on the queue page instead of polling. This is cleaner and more responsive.

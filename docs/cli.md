# CLI Usage

Simple command-line interface for summarizing YouTube videos locally.

## Prerequisites

- `uv sync` to install dependencies
- `claude auth login` to authenticate with your Claude Max plan
- `data/cookies.txt` for members-only videos (export from browser)
- Node.js via nvm (for yt-dlp YouTube JS challenge solving)

## Usage

```bash
uv run python cli.py <url> [options]
```

## Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `url` | Yes | — | YouTube video URL (or bare video ID) |
| `--cookies` | No | `data/cookies.txt` | Path to cookies.txt for authenticated content |
| `--prompt` | No | Built-in | Custom summary prompt (string or path to .txt file) |
| `--model` | No | `claude-sonnet-4-20250514` | Claude model override |
| `--output`, `-o` | No | stdout | Output file path |
| `--format` | No | `markdown` | Output format: `markdown` or `json` |
| `--transcript-only` | No | off | Only extract transcript, skip summarization |
| `--no-keyframes` | No | off | Skip keyframe images (can still use `--ocr`) |
| `--ocr` | No | `none` | OCR mode: `none`, `file` (save .txt for Claude), `inline` (inject into transcript) |
| `--dedup` | No | `regular` | Keyframe dedup: `regular` (pHash), `slides` (SSIM), `ocr` (text match), `none` |

## Examples

```bash
# Summarize a public video
uv run python cli.py "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Faster summary without keyframes
uv run python cli.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --no-keyframes

# Save summary to file
uv run python cli.py "https://youtu.be/jNQXAC9IVRw" -o summary.md

# JSON output
uv run python cli.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --no-keyframes --format json -o summary.json

# Extract transcript only
uv run python cli.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --transcript-only
uv run python cli.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --transcript-only -o transcript.txt

# Members-only video
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --cookies data/cookies.txt

# Custom prompt
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --prompt "Summarize as bullet points. Return JSON with title, tldr, summary keys. Return ONLY JSON."

# Custom prompt from file
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --prompt prompts/my_prompt.txt

# Use a different model
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --model claude-opus-4-0-20250415

# OCR — extract text from keyframes
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --ocr inline
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --ocr file  # Claude reads .txt files
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --no-keyframes --ocr inline  # OCR only, no images

# Keyframe dedup modes
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --dedup regular  # pHash (default)
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --dedup slides   # SSIM (better for presentations)
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --dedup ocr --ocr inline  # dedup by OCR text
uv run python cli.py "https://www.youtube.com/watch?v=VIDEO_ID" --dedup none     # keep all keyframes
```

## Cookies for members-only videos

Export cookies from your local browser and upload to the server:

```bash
# On your local machine (requires yt-dlp installed locally)
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://youtube.com" --skip-download

# Upload to remote server
scp cookies.txt user@server:~/code/youtube-summarizer/data/cookies.txt
```

Cookies expire every ~2 weeks. Re-export when you get auth errors.

## Supported URL formats

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/embed/VIDEO_ID`
- `https://www.youtube.com/v/VIDEO_ID`
- Bare video ID: `VIDEO_ID`

## How it works

1. Fetches video metadata via yt-dlp
2. Downloads video (unless `--transcript-only`)
3. Extracts transcript — YouTube captions first, Whisper fallback with auto language detection
4. Extracts keyframes via ffmpeg scene detection (unless `--no-keyframes` without `--ocr`)
5. Deduplicates similar keyframes (pHash for regular, SSIM for slides mode)
6. Runs OCR on deduplicated keyframes (if `--ocr` enabled)
7. Groups transcript by keyframe boundaries with XML tags and timestamp ranges
8. Sends to Claude for summarization (with images/OCR files/inline OCR per mode)
9. Outputs structured summary (title, TL;DR, detailed summary)

## Notes

- Videos without captions require downloading the full video for Whisper transcription
- Whisper auto-detects language — works with non-English videos
- Keyframe extraction adds time but improves summary quality for visual content (slides, charts, demos)
- `--no-keyframes` is recommended for talking-head or podcast-style videos
- `--dedup slides` is recommended for presentation/slide-based videos (uses SSIM for finer change detection)
- `--dedup regular` (default) uses perceptual hashing, good for most videos
- OCR is slow (~10s per keyframe on GPU) — dedup runs first to reduce the number of frames to OCR
- `--dedup ocr` runs OCR on all frames first, then deduplicates by text similarity (slower but more accurate)
- Temp files are stored in `data/tmp/cli_<video_id>/` during processing

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
| `--no-keyframes` | No | off | Skip keyframe extraction (faster) |

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
3. Extracts transcript — YouTube captions first, Whisper fallback if none
4. Extracts keyframes via ffmpeg scene detection (unless `--no-keyframes`)
5. Groups transcript by keyframe boundaries
6. Sends grouped transcript + keyframe images to Claude for summarization
7. Outputs structured summary (title, TL;DR, detailed summary)

## Notes

- Videos without captions require downloading the full video for Whisper transcription
- Keyframe extraction adds time but improves summary quality for visual content (slides, charts, demos)
- `--no-keyframes` is recommended for talking-head or podcast-style videos
- Temp files are stored in `data/tmp/cli_<video_id>/` during processing

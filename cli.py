"""CLI for YouTube Video Summarizer — local single-video usage."""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cli")


def parse_args():
    p = argparse.ArgumentParser(description="Summarize a YouTube video")
    p.add_argument("url", help="YouTube video URL")
    p.add_argument("--cookies", default="data/cookies.txt", help="Path to cookies.txt (default: data/cookies.txt)")
    p.add_argument("--prompt", default=None, help="Custom summary prompt (or path to a .txt file)")
    p.add_argument("--model", default=None, help="Claude model override (default: from settings or claude-sonnet-4-20250514)")
    p.add_argument("--output", "-o", default=None, help="Output file path (default: print to stdout)")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    p.add_argument("--transcript-only", action="store_true", help="Only extract transcript, skip summarization")
    p.add_argument("--no-keyframes", action="store_true", help="Skip keyframe extraction")
    return p.parse_args()


def extract_video_id(url: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r"(?:v=|/v/|/embed/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract video ID from: {url}")


async def run(args):
    from app import config
    from app.database import init_db
    await init_db()

    # Override cookies path if specified
    cookies_path = Path(args.cookies)
    if cookies_path.exists():
        config.COOKIES_PATH = cookies_path
        logger.info("Using cookies: %s", cookies_path)
    elif args.cookies != "data/cookies.txt":
        logger.warning("Cookies file not found: %s", cookies_path)

    from app.services.ytdlp import get_video_info

    video_id = extract_video_id(args.url)
    logger.info("Video ID: %s", video_id)

    # Fetch metadata
    logger.info("Fetching video info...")
    try:
        info = await get_video_info(args.url)
    except Exception:
        info = {"id": video_id}
        logger.warning("Could not fetch video metadata")

    title = info.get("title") or "Unknown"
    channel = info.get("channel") or "Unknown"
    duration = info.get("duration")
    logger.info("Title: %s", title)
    logger.info("Channel: %s", channel)
    if duration:
        logger.info("Duration: %dm%ds", duration // 60, duration % 60)

    # Create work dir
    work_dir = config.TMP_DIR / f"cli_{video_id}"
    work_dir.mkdir(exist_ok=True)

    # Download video (needed for keyframes and whisper fallback)
    video_path = None
    if not args.transcript_only:
        from app.services.ytdlp import download_video
        logger.info("Downloading video...")
        try:
            video_path = await download_video(video_id, work_dir)
            logger.info("Downloaded: %s", video_path)
        except Exception:
            logger.exception("Download failed, will try captions only")

    # Extract transcript
    from app.services.transcript import extract_transcript
    logger.info("Extracting transcript...")
    try:
        transcript = await extract_transcript(video_id, video_path, work_dir)
    except RuntimeError as e:
        if "No captions and no video file" in str(e):
            logger.error(
                "Cannot extract transcript: no captions available and video download failed. "
                "This is likely a members-only video — check that your cookies are valid."
            )
            sys.exit(1)
        raise
    logger.info("Transcript: %s, %d segments, %d chars", transcript.source, len(transcript.segments), len(transcript.text))

    if args.transcript_only:
        _output_transcript(args, transcript)
        return

    # Extract keyframes
    keyframes = []
    if not args.no_keyframes and video_path and video_path.exists():
        from app.services.keyframes import extract_keyframes
        logger.info("Extracting keyframes...")
        try:
            keyframes = await extract_keyframes(video_path, work_dir)
            logger.info("Extracted %d keyframes", len(keyframes))
        except Exception:
            logger.warning("Keyframe extraction failed, continuing without")

    # Summarize
    from app.services.llm import summarize

    custom_prompt = None
    if args.prompt:
        prompt_path = Path(args.prompt)
        if prompt_path.exists():
            custom_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            custom_prompt = args.prompt

    video_meta = {"title": title, "channel": channel, "duration": duration}

    logger.info("Summarizing with Claude...")
    result = await summarize(
        transcript=transcript,
        keyframes=keyframes,
        video_meta=video_meta,
        custom_prompt=custom_prompt,
        model=args.model,
    )
    logger.info("Summary generated: %d chars", len(result.raw_response))

    _output_summary(args, result, video_id, title, channel)


def _output_transcript(args, transcript):
    lines = []
    if transcript.segments:
        for seg in transcript.segments:
            sm, ss = divmod(int(seg.start), 60)
            em, es = divmod(int(seg.end), 60)
            lines.append(f"[{sm:02d}:{ss:02d} - {em:02d}:{es:02d}] {seg.text}")
    else:
        lines.append(transcript.text)

    text = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        logger.info("Transcript saved to %s", args.output)
    else:
        print(text)


def _output_summary(args, result, video_id, title, channel):
    if args.format == "json":
        data = {
            "video_id": video_id,
            "title": result.title or title,
            "channel": channel,
            "tldr": result.tldr,
            "summary": result.summary,
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = f"# {result.title or title}\n\n"
        text += f"**Channel:** {channel}\n"
        text += f"**Video:** https://www.youtube.com/watch?v={video_id}\n\n"
        if result.tldr:
            text += f"**TL;DR:** {result.tldr}\n\n"
        text += result.summary

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        logger.info("Summary saved to %s", args.output)
    else:
        print(text)


def main():
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

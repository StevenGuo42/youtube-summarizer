"""Tests for cli.py — requires network and Claude auth for summarization tests."""

import json
import logging

import pytest

from app.config import TMP_DIR
from app.database import init_db
from cli import extract_video_id, run, parse_args

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
async def _ensure_db():
    await init_db()


# --- Unit tests ---

class TestExtractVideoId:
    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw") == "jNQXAC9IVRw"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/jNQXAC9IVRw") == "jNQXAC9IVRw"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/jNQXAC9IVRw") == "jNQXAC9IVRw"

    def test_with_params(self):
        assert extract_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=30") == "jNQXAC9IVRw"

    def test_bare_id(self):
        assert extract_video_id("jNQXAC9IVRw") == "jNQXAC9IVRw"

    def test_invalid(self):
        with pytest.raises(ValueError):
            extract_video_id("not-a-url")


# --- Integration tests ---

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.asyncio
async def test_transcript_only(tmp_path):
    """Test --transcript-only outputs timestamped transcript."""
    output_file = tmp_path / "transcript.txt"
    args = parse_args.__wrapped__(
        [TEST_VIDEO_URL, "--transcript-only", "-o", str(output_file)]
    ) if hasattr(parse_args, '__wrapped__') else _make_args(
        url=TEST_VIDEO_URL, transcript_only=True, output=str(output_file),
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    logger.info("Transcript output (%d chars):\n%s", len(text), text[:500])
    assert len(text) > 0
    assert "[00:" in text  # has timestamps


@pytest.mark.asyncio
async def test_summarize_no_keyframes(tmp_path):
    """Test summarization without keyframes."""
    from app.services.llm import get_auth_status

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    output_file = tmp_path / "summary.md"
    args = _make_args(
        url=TEST_VIDEO_URL, no_keyframes=True, output=str(output_file),
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    logger.info("Summary output (%d chars):\n%s", len(text), text[:500])
    assert len(text) > 100
    assert "# " in text  # has markdown heading


@pytest.mark.asyncio
async def test_summarize_json_format(tmp_path):
    """Test JSON output format."""
    from app.services.llm import get_auth_status

    status = await get_auth_status()
    if not status.get("loggedIn"):
        pytest.skip("Not logged in to Claude")

    output_file = tmp_path / "summary.json"
    args = _make_args(
        url=TEST_VIDEO_URL, no_keyframes=True, output=str(output_file), format="json",
    )

    await run(args)

    text = output_file.read_text(encoding="utf-8")
    data = json.loads(text)
    logger.info("JSON output: %s", json.dumps(data, indent=2, ensure_ascii=False)[:500])
    assert data["video_id"] == "jNQXAC9IVRw"
    assert data["title"]
    assert data["summary"]


def _make_args(**kwargs):
    """Create an argparse-like namespace with defaults."""
    defaults = {
        "url": "",
        "cookies": "data/cookies.txt",
        "prompt": None,
        "model": None,
        "output": None,
        "format": "markdown",
        "transcript_only": False,
        "no_keyframes": False,
    }
    defaults.update(kwargs)

    class Args:
        pass

    args = Args()
    for k, v in defaults.items():
        setattr(args, k, v)
    return args

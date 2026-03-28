import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import (
    KEYFRAME_MAX_DIMENSION,
    MAX_KEYFRAMES,
    SCENE_CHANGE_THRESHOLD,
    UNIFORM_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)

_nvidia_hwaccel: bool | None = None


async def _check_nvidia_hwaccel() -> bool:
    """Check if ffmpeg NVIDIA CUDA hwaccel is available (cached)."""
    global _nvidia_hwaccel
    if _nvidia_hwaccel is not None:
        return _nvidia_hwaccel
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hwaccels",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    _nvidia_hwaccel = "cuda" in stdout.decode()
    logger.info("NVIDIA hwaccel available: %s", _nvidia_hwaccel)
    return _nvidia_hwaccel


async def _ffmpeg_exec(*args: str, use_gpu: bool = False) -> tuple[int, bytes]:
    """Run ffmpeg with optional CUDA hardware-accelerated decoding."""
    cmd = ["ffmpeg"]
    if use_gpu:
        cmd.extend(["-hwaccel", "cuda"])
    cmd.extend(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr


@dataclass
class KeyFrame:
    timestamp: float
    image_path: Path


async def extract_keyframes(video_path: Path, work_dir: Path) -> list[KeyFrame]:
    """Extract keyframes via scene detection, with uniform interval fallback."""
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    keyframes = await _scene_detect(video_path, frames_dir)

    if len(keyframes) < 3:
        logger.info("Scene detection yielded %d frames, falling back to uniform interval", len(keyframes))
        keyframes = await _uniform_sample(video_path, frames_dir)

    if len(keyframes) > MAX_KEYFRAMES:
        keyframes = _subsample(keyframes, MAX_KEYFRAMES)

    for kf in keyframes:
        _downscale(kf.image_path)

    logger.info("Extracted %d keyframes from %s", len(keyframes), video_path.name)
    return keyframes


async def _scene_detect(video_path: Path, frames_dir: Path) -> list[KeyFrame]:
    """Extract keyframes using ffmpeg scene change detection (GPU-accelerated decode when available)."""
    use_gpu = await _check_nvidia_hwaccel()
    ffmpeg_args = (
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{SCENE_CHANGE_THRESHOLD})',showinfo",
        "-vsync", "vfr",
        str(frames_dir / "scene_%04d.png"),
        "-y",
    )

    returncode, stderr = await _ffmpeg_exec(*ffmpeg_args, use_gpu=use_gpu)

    if returncode != 0 and use_gpu:
        logger.warning("GPU-accelerated scene detection failed, falling back to CPU")
        for f in frames_dir.glob("scene_*.png"):
            f.unlink()
        returncode, stderr = await _ffmpeg_exec(*ffmpeg_args, use_gpu=False)

    if returncode != 0:
        logger.warning("ffmpeg scene detection failed: %s", stderr.decode()[-500:])
        return []

    timestamps = _parse_showinfo_timestamps(stderr.decode())
    frame_files = sorted(frames_dir.glob("scene_*.png"))

    keyframes = []
    for i, path in enumerate(frame_files):
        ts = timestamps[i] if i < len(timestamps) else 0.0
        keyframes.append(KeyFrame(timestamp=ts, image_path=path))

    return keyframes


def _parse_showinfo_timestamps(stderr: str) -> list[float]:
    """Parse pts_time values from ffmpeg showinfo filter output."""
    timestamps = []
    for match in re.finditer(r"pts_time:\s*([\d.]+)", stderr):
        timestamps.append(float(match.group(1)))
    return timestamps


async def _get_duration(video_path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip())


async def _uniform_sample(video_path: Path, frames_dir: Path) -> list[KeyFrame]:
    """Extract frames at uniform intervals using ffmpeg (GPU-accelerated decode when available)."""
    # Clean any scene detection frames
    for f in frames_dir.glob("scene_*.png"):
        f.unlink()

    use_gpu = await _check_nvidia_hwaccel()
    duration = await _get_duration(video_path)
    interval = min(UNIFORM_INTERVAL_SECONDS, max(duration / MAX_KEYFRAMES, 1))
    rate = 1 / interval
    logger.info("Uniform sampling: duration=%.1fs interval=%.1fs", duration, interval)

    ffmpeg_args = (
        "-i", str(video_path),
        "-vf", f"fps={rate},showinfo",
        "-vsync", "vfr",
        str(frames_dir / "uniform_%04d.png"),
        "-y",
    )

    returncode, stderr = await _ffmpeg_exec(*ffmpeg_args, use_gpu=use_gpu)

    if returncode != 0 and use_gpu:
        logger.warning("GPU-accelerated uniform sampling failed, falling back to CPU")
        for f in frames_dir.glob("uniform_*.png"):
            f.unlink()
        returncode, stderr = await _ffmpeg_exec(*ffmpeg_args, use_gpu=False)

    if returncode != 0:
        logger.warning("ffmpeg uniform sampling failed: %s", stderr.decode()[-500:])
        return []

    timestamps = _parse_showinfo_timestamps(stderr.decode())
    frame_files = sorted(frames_dir.glob("uniform_*.png"))

    keyframes = []
    for i, path in enumerate(frame_files):
        ts = timestamps[i] if i < len(timestamps) else i * interval
        keyframes.append(KeyFrame(timestamp=ts, image_path=path))

    return keyframes


def _subsample(keyframes: list[KeyFrame], max_count: int) -> list[KeyFrame]:
    """Evenly subsample keyframes to max_count."""
    if len(keyframes) <= max_count:
        return keyframes
    step = len(keyframes) / max_count
    return [keyframes[int(i * step)] for i in range(max_count)]


def _downscale(image_path: Path):
    """Downscale image so the long edge is at most KEYFRAME_MAX_DIMENSION."""
    with Image.open(image_path) as img:
        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= KEYFRAME_MAX_DIMENSION:
            return
        scale = KEYFRAME_MAX_DIMENSION / long_edge
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)
        img.save(image_path)

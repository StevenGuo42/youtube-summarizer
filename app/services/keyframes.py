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


def deduplicate_keyframes(
    keyframes: list[KeyFrame],
    ocr_results: list | None = None,
    mode: str = "regular",
) -> tuple[list[KeyFrame], list | None]:
    """Deduplicate similar consecutive keyframes.

    Modes:
    - "regular": pHash with hamming distance > 5 (good for most videos)
    - "slides": SSIM < 0.95 (structural similarity, better for presentations with text changes)
    - "ocr": fuzzy OCR text match (SequenceMatcher ratio <= 0.85). Requires ocr_results.
    - "none": no dedup, return as-is

    Keeps the LAST frame per group (last slide in a sequence has the most content).
    Returns (deduped_keyframes, deduped_ocr_results). ocr_results output is None when input is None.
    """
    if mode == "none" or not keyframes:
        return list(keyframes), list(ocr_results) if ocr_results is not None else None

    if len(keyframes) == 1:
        return list(keyframes), list(ocr_results) if ocr_results is not None else None

    if mode == "ocr":
        if ocr_results is None:
            logger.warning("OCR dedup requested but no ocr_results provided, falling back to regular")
            mode = "regular"
        else:
            return _dedup_by_ocr(keyframes, ocr_results)

    if mode == "slides":
        deduped = _dedup_by_ssim(keyframes)
    else:
        deduped = _dedup_by_phash(keyframes)

    # Filter ocr_results to match deduped keyframes if present
    if ocr_results is not None:
        deduped_set = set(id(kf) for kf in deduped)
        deduped_ocr = [ocr_results[i] for i, kf in enumerate(keyframes) if id(kf) in deduped_set]
        return deduped, deduped_ocr

    return deduped, None


def _dedup_by_ocr(
    keyframes: list[KeyFrame], ocr_results: list,
) -> tuple[list[KeyFrame], list]:
    """Group consecutive keyframes by fuzzy OCR text similarity. Keeps last per group."""
    from difflib import SequenceMatcher

    # Track groups: each group is a list of indices
    groups: list[list[int]] = [[0]]
    rep_text = ocr_results[0].text.strip()

    for i in range(1, len(keyframes)):
        curr_text = ocr_results[i].text.strip()

        # Empty OCR text frames are always kept as unique
        if not rep_text or not curr_text:
            groups.append([i])
            rep_text = curr_text
            continue

        ratio = SequenceMatcher(None, rep_text, curr_text).ratio()
        if ratio > 0.85:
            groups[-1].append(i)  # same group
        else:
            groups.append([i])  # new group
            rep_text = curr_text

    # Keep last frame per group
    keep_indices = [g[-1] for g in groups]
    keep_kf = [keyframes[i] for i in keep_indices]
    keep_ocr = [ocr_results[i] for i in keep_indices]

    logger.info("OCR dedup: %d -> %d keyframes", len(keyframes), len(keep_kf))
    return keep_kf, keep_ocr


def _dedup_by_ssim(keyframes: list[KeyFrame], threshold: float = 0.95) -> list[KeyFrame]:
    """Group consecutive keyframes by structural similarity. Keeps last per group.

    SSIM is better than pHash for presentations — it detects text changes
    that pHash misses. Threshold 0.95 means frames with SSIM >= 0.95 are
    considered the same (grouped), frames below are kept as distinct.
    """
    import numpy as np
    from PIL import Image
    from skimage.metrics import structural_similarity as ssim

    # Load and convert to grayscale, resize for speed
    def _load(kf):
        return np.array(Image.open(kf.image_path).convert("L").resize((256, 256)))

    imgs = [_load(kf) for kf in keyframes]

    groups: list[list[int]] = [[0]]
    rep_idx = 0

    for i in range(1, len(keyframes)):
        score = ssim(imgs[rep_idx], imgs[i])
        if score < threshold:
            groups.append([i])  # different enough — new group
            rep_idx = i
        else:
            groups[-1].append(i)  # similar — same group

    keep = [keyframes[g[-1]] for g in groups]

    logger.info("SSIM dedup (threshold=%.2f): %d -> %d keyframes", threshold, len(keyframes), len(keep))
    return keep


def _dedup_by_phash(keyframes: list[KeyFrame], threshold: int = 5) -> list[KeyFrame]:
    """Group consecutive keyframes by perceptual hash similarity. Keeps last per group."""
    import imagehash
    from PIL import Image

    hashes = []
    for kf in keyframes:
        hashes.append(imagehash.phash(Image.open(kf.image_path)))

    # Track groups: each group is a list of indices
    groups: list[list[int]] = [[0]]
    rep_hash = hashes[0]

    for i in range(1, len(keyframes)):
        distance = rep_hash - hashes[i]
        if distance > threshold:
            groups.append([i])  # new group
            rep_hash = hashes[i]
        else:
            groups[-1].append(i)  # same group

    # Keep last frame per group
    keep = [keyframes[g[-1]] for g in groups]

    logger.info("pHash dedup (threshold=%d): %d -> %d keyframes", threshold, len(keyframes), len(keep))
    return keep


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

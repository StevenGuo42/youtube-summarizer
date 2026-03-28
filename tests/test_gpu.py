"""Tests for GPU acceleration — CUDA availability, Whisper on GPU, ffmpeg hwaccel."""

import logging

import pytest
import torch

from app.config import TMP_DIR, WHISPER_MODEL_DIR
from app.services.keyframes import _check_nvidia_hwaccel, _ffmpeg_exec

logger = logging.getLogger(__name__)

requires_cuda = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="CUDA not available"
)


# ---------------------------------------------------------------------------
# CUDA / torch basics
# ---------------------------------------------------------------------------

@requires_cuda
def test_cuda_available():
    """Verify CUDA is detected and report GPU info."""
    device = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    free, _ = torch.cuda.mem_get_info(0)
    free_gb = free / (1024 ** 3)
    logger.info("GPU: %s | VRAM: %.1f GB total, %.1f GB free", device, total, free_gb)

    assert "cuda" in torch.device("cuda").type
    assert total > 0


@requires_cuda
def test_cuda_tensor_ops():
    """Basic CUDA tensor round-trip to confirm the driver works."""
    t = torch.randn(256, 256, device="cuda")
    result = t @ t.T
    assert result.shape == (256, 256)
    assert result.device.type == "cuda"
    del t, result
    torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
# ffmpeg NVIDIA hwaccel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ffmpeg_hwaccel_detected():
    """Verify ffmpeg reports CUDA hardware acceleration."""
    available = await _check_nvidia_hwaccel()
    logger.info("ffmpeg NVIDIA hwaccel: %s", available)
    if not torch.cuda.is_available():
        pytest.skip("CUDA not available — hwaccel may not be present")
    assert available is True


@requires_cuda
@pytest.mark.asyncio
async def test_ffmpeg_hwaccel_decode():
    """Decode a short test video with -hwaccel cuda and verify success."""
    from app.services.ytdlp import download_video

    work_dir = TMP_DIR / "test_gpu_hwaccel"
    work_dir.mkdir(exist_ok=True)

    video_path = work_dir / "jNQXAC9IVRw.mp4"
    if not video_path.exists():
        video_path = await download_video("jNQXAC9IVRw", work_dir)

    out_path = work_dir / "hwaccel_test.png"
    returncode, stderr = await _ffmpeg_exec(
        "-i", str(video_path),
        "-vf", "select='eq(n,0)'",
        "-vframes", "1",
        str(out_path), "-y",
        use_gpu=True,
    )

    logger.info("ffmpeg hwaccel decode returncode=%d", returncode)
    assert returncode == 0, f"hwaccel decode failed: {stderr.decode()[-500:]}"
    assert out_path.exists()


# ---------------------------------------------------------------------------
# Whisper GPU transcription
# ---------------------------------------------------------------------------

@requires_cuda
@pytest.mark.asyncio
async def test_whisper_gpu():
    """Transcribe a short video with faster-whisper on CUDA."""
    from app.services.ytdlp import download_video
    from app.services.transcript import _transcribe_whisper

    work_dir = TMP_DIR / "test_gpu_whisper"
    work_dir.mkdir(exist_ok=True)

    video_path = work_dir / "jNQXAC9IVRw.mp4"
    if not video_path.exists():
        video_path = await download_video("jNQXAC9IVRw", work_dir)

    result = await _transcribe_whisper(video_path, work_dir)

    logger.info("Whisper source=%s segments=%d", result.source, len(result.segments))
    logger.info("Text: %s", result.text[:200])

    assert result.source == "whisper"
    assert len(result.segments) > 0
    assert len(result.text) > 0

    # Verify GPU was used by checking model was loaded from the GPU model repo
    # (CPU fallback uses "small", GPU uses "Systran/faster-distil-whisper-large-v3")
    model_dir = WHISPER_MODEL_DIR / "models--Systran--faster-distil-whisper-large-v3"
    assert model_dir.exists(), (
        f"GPU model not found at {model_dir} — whisper likely fell back to CPU"
    )


@requires_cuda
def test_vram_not_leaked():
    """Confirm VRAM is reasonably free after GPU operations (no major leaks)."""
    free, total = torch.cuda.mem_get_info(0)
    used_gb = (total - free) / (1024 ** 3)
    total_gb = total / (1024 ** 3)
    logger.info("VRAM after tests: %.2f GB used / %.1f GB total", used_gb, total_gb)

    # After cleanup, used VRAM should be well under half of total
    assert used_gb < total_gb / 2, f"VRAM usage too high: {used_gb:.2f} GB"

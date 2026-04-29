import asyncio
import gc
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image

from app.cancel import is_cancelled
from app.config import OCR_MODEL_DIR, OCR_MODEL_NAME, OCR_PROMPT_TYPE
from app.services.keyframes import KeyFrame
from app.shutdown import is_shutting_down

logger = logging.getLogger(__name__)


@dataclass
class OcrResult:
    timestamp: float
    image_path: Path
    text: str


def _load_model():
    """Load chandra-ocr-2 with 4-bit quantization (GPU) or float32 (CPU)."""
    from transformers import AutoModelForImageTextToText, AutoProcessor

    cache_dir = str(OCR_MODEL_DIR)
    gpu = torch.cuda.is_available()

    if gpu:
        from transformers import BitsAndBytesConfig

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        logger.info("Loading %s (4-bit quantized, CUDA)", OCR_MODEL_NAME)
        model = AutoModelForImageTextToText.from_pretrained(
            OCR_MODEL_NAME,
            quantization_config=quantization_config,
            device_map="auto",
            cache_dir=cache_dir,
        )
    else:
        logger.info("Loading %s (CPU, float32)", OCR_MODEL_NAME)
        model = AutoModelForImageTextToText.from_pretrained(
            OCR_MODEL_NAME,
            device_map="cpu",
            torch_dtype=torch.float32,
            cache_dir=cache_dir,
        )

    model.eval()
    processor = AutoProcessor.from_pretrained(OCR_MODEL_NAME, cache_dir=cache_dir)
    processor.tokenizer.padding_side = "left"
    model.processor = processor
    return model, gpu


def load_model():
    """Load chandra-ocr-2 model. Returns (model, gpu) tuple.

    Caller is responsible for cleanup:
        del model; gc.collect(); torch.cuda.empty_cache()
    """
    return _load_model()


def _run_ocr(keyframes: list[KeyFrame], model_tuple=None, on_progress: Callable[[int, int], None] | None = None, job_id: str | None = None) -> list[OcrResult]:
    """Load model, run OCR on all keyframes, release model."""
    from chandra.model.hf import generate_hf
    from chandra.model.schema import BatchInputItem
    from chandra.output import parse_markdown

    if model_tuple:
        model, gpu = model_tuple
        owns_model = False
    else:
        model, gpu = _load_model()
        owns_model = True
    try:
        results = []
        for kf in keyframes:
            if is_shutting_down():
                logger.info("OCR interrupted by shutdown")
                break
            if job_id and is_cancelled(job_id):
                logger.info("[%s] OCR interrupted by job cancel", job_id)
                break
            try:
                img = Image.open(kf.image_path)
                try:
                    batch = [BatchInputItem(
                        image=img,
                        prompt_type=OCR_PROMPT_TYPE,
                    )]
                    gen_result = generate_hf(batch, model)[0]
                finally:
                    img.close()
                text = parse_markdown(gen_result.raw)
                results.append(OcrResult(
                    timestamp=kf.timestamp,
                    image_path=kf.image_path,
                    text=text,
                ))
                if on_progress:
                    on_progress(len(results), len(keyframes))
            except Exception as e:
                logger.warning("OCR failed for %s: %s", kf.image_path.name, e)
                results.append(OcrResult(
                    timestamp=kf.timestamp,
                    image_path=kf.image_path,
                    text="",
                ))
                if on_progress:
                    on_progress(len(results), len(keyframes))
        return results
    finally:
        if owns_model:
            del model
            gc.collect()
            if gpu:
                torch.cuda.empty_cache()


def save_ocr_results(ocr_results: list[OcrResult], work_dir: Path) -> list[Path | None]:
    """Write OCR results to text files for Claude to read via Read tool.

    Returns list parallel to input: Path for results with text, None for empty.
    """
    ocr_dir = work_dir / "ocr"
    ocr_dir.mkdir(exist_ok=True)

    paths: list[Path | None] = []
    for i, result in enumerate(ocr_results):
        if not result.text:
            paths.append(None)
            continue
        path = ocr_dir / f"frame_{i:04d}_ocr.txt"
        path.write_text(result.text, encoding="utf-8")
        paths.append(path)

    logger.info("Saved %d/%d OCR results to %s", sum(1 for p in paths if p), len(paths), ocr_dir)
    return paths


async def extract_text(keyframes: list[KeyFrame], model_tuple=None, on_progress: Callable[[int, int], None] | None = None, job_id: str | None = None) -> list[OcrResult]:
    """Run OCR on keyframe images using chandra-ocr-2."""
    if not keyframes:
        return []

    logger.info("Running OCR on %d keyframes", len(keyframes))
    results = await asyncio.to_thread(_run_ocr, keyframes, model_tuple, on_progress, job_id)
    ocr_count = sum(1 for r in results if r.text)
    logger.info("OCR complete: %d/%d keyframes had text", ocr_count, len(results))
    return results

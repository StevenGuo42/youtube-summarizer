import asyncio
import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import OCR_GGUF_REPO, OCR_MODEL_DIR, OCR_QUANT
from app.services.keyframes import KeyFrame

logger = logging.getLogger(__name__)

OCR_PROMPT = (
    "Extract all text visible in this image. "
    "Return only the extracted text, preserving layout where possible. "
    "If no text is visible, return NONE."
)


@dataclass
class OcrResult:
    timestamp: float
    image_path: Path
    text: str


def _download_models() -> tuple[Path, Path]:
    """Download GGUF model and mmproj if not cached."""
    from huggingface_hub import hf_hub_download

    OCR_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_path = Path(hf_hub_download(
        repo_id=OCR_GGUF_REPO,
        filename=f"chandra-ocr-2-{OCR_QUANT}.gguf",
        local_dir=OCR_MODEL_DIR,
    ))
    mmproj_path = Path(hf_hub_download(
        repo_id=OCR_GGUF_REPO,
        filename="chandra-ocr-2.mmproj-q8_0.gguf",
        local_dir=OCR_MODEL_DIR,
    ))
    return model_path, mmproj_path


def _image_to_data_uri(image_path: Path) -> str:
    """Convert image file to base64 data URI."""
    data = image_path.read_bytes()
    b64 = base64.b64encode(data).decode()
    suffix = image_path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "image/png"
    )
    return f"data:{mime};base64,{b64}"


def _run_ocr(keyframes: list[KeyFrame]) -> list[OcrResult]:
    """Load model and run OCR on all keyframes."""
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Qwen25VLChatHandler

    model_path, mmproj_path = _download_models()
    logger.info("Loading chandra-ocr-2 GGUF (%s)", OCR_QUANT)

    chat_handler = Qwen25VLChatHandler(clip_model_path=str(mmproj_path))
    llm = None
    gpu = True

    try:
        llm = Llama(
            model_path=str(model_path),
            chat_handler=chat_handler,
            n_ctx=2048,
            n_gpu_layers=-1,
        )
    except Exception as e:
        logger.warning("GPU model load failed (%s), trying CPU", e)
        gpu = False
        llm = Llama(
            model_path=str(model_path),
            chat_handler=chat_handler,
            n_ctx=2048,
            n_gpu_layers=0,
        )

    try:
        results = []
        for kf in keyframes:
            try:
                data_uri = _image_to_data_uri(kf.image_path)
                response = llm.create_chat_completion(
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                            {"type": "text", "text": OCR_PROMPT},
                        ],
                    }],
                    max_tokens=1024,
                )
                text = response["choices"][0]["message"]["content"].strip()
                if text == "NONE":
                    text = ""
                results.append(OcrResult(
                    timestamp=kf.timestamp,
                    image_path=kf.image_path,
                    text=text,
                ))
            except Exception as e:
                logger.warning("OCR failed for %s: %s", kf.image_path.name, e)
                results.append(OcrResult(
                    timestamp=kf.timestamp,
                    image_path=kf.image_path,
                    text="",
                ))
        return results
    finally:
        del llm
        del chat_handler
        if gpu:
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:
                pass


async def extract_text(keyframes: list[KeyFrame]) -> list[OcrResult]:
    """Run OCR on keyframe images using chandra-ocr-2 GGUF model."""
    if not keyframes:
        return []

    logger.info("Running OCR on %d keyframes", len(keyframes))
    results = await asyncio.to_thread(_run_ocr, keyframes)
    ocr_count = sum(1 for r in results if r.text)
    logger.info("OCR complete: %d/%d keyframes had text", ocr_count, len(results))
    return results

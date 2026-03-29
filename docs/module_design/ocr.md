# ocr — `app/services/ocr.py`

OCR text extraction from keyframe images using chandra-ocr-2 via HuggingFace Transformers.

## Responsibilities

- Run OCR on keyframe images to extract visible on-screen text
- Return per-keyframe OCR results (text + metadata)
- Download model weights on first use from HuggingFace

## Key Design Decisions

- **Model**: chandra-ocr-2 (4.8B param, Qwen 3.5 architecture) via HuggingFace Transformers
- **Runtime**: `transformers` with `AutoModelForImageTextToText` + `chandra` library for prompting and output parsing
- **Quantization**: 4-bit via `BitsAndBytesConfig` (~2.5 GB VRAM)
- **Model download**: `transformers` auto-download, cached in `data/ocr_models/`
- **Prompt type**: `"ocr"` — extracts text as HTML, handles tables/math/code. No bounding boxes.
- **Output parsing**: `chandra.output.parse_markdown()` converts HTML to clean markdown
- **Processing**: keyframes processed sequentially (not batched) to keep VRAM low
- **Model lifecycle**: loaded once per `extract_text()` call, released after all keyframes processed
- **Why not GGUF/llama-cpp-python**: chandra-ocr-2 uses Qwen 3.5 architecture which llama-cpp-python does not support for vision (segfaults on CUDA, blank output on CPU)
- **Why not vLLM**: vLLM runs as a persistent server holding VRAM; we need load/process/free lifecycle to share 8 GB VRAM with Whisper

### GPU Support

- GPU: 4-bit quantized via `BitsAndBytesConfig`, `device_map="auto"` — total ~3.4 GB VRAM
- CPU fallback: `device_map="cpu"`, `torch_dtype=torch.float32` (no quantization)
- VRAM cleanup: `del model` + `gc.collect()` + `torch.cuda.empty_cache()` in `finally` block

## Interface

```python
@dataclass
class OcrResult:
    timestamp: float
    image_path: Path
    text: str

async def extract_text(keyframes: list[KeyFrame]) -> list[OcrResult]
def save_ocr_results(ocr_results: list[OcrResult], work_dir: Path) -> list[Path | None]
```

Per-keyframe OCR: returns one `OcrResult` per input keyframe. Empty `text` if no text detected or OCR fails for that frame.

Saves OCR text to individual `.txt` files in `work_dir/ocr/`. Returns paths parallel to input (`None` for empty-text results). Used by file-based keyframe modes so Claude can read OCR text via the `Read` tool.

## Dependencies

- `chandra-ocr[hf]` (model prompting, output parsing)
- `transformers` (model loading and inference)
- `bitsandbytes` (4-bit quantization for GPU)
- `torch` for CUDA detection and VRAM cleanup
- `PIL` for image loading
- `app.config` for `OCR_MODEL_DIR`, `OCR_MODEL_NAME`, `OCR_PROMPT_TYPE`
- `app.services.keyframes` for `KeyFrame` type

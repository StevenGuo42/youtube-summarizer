# OCR Module: Migrate from llama-cpp-python to chandra-ocr + HF Transformers

## Problem

chandra-ocr-2 uses Qwen 3.5 architecture (`qwen35` model + `qwen3vl_merger` mmproj). Neither mainline llama-cpp-python (0.3.19) nor JamePeng's fork (0.3.33) supports this architecture correctly:

- **Mainline 0.3.19**: Segfaults in CUDA vision encoder (`ggml_gallocr_alloc_graph` during `clip_image_batch_encode`).
- **JamePeng's fork 0.3.33**: No segfault, but vision encoder produces blank embeddings — model sees all images as empty.
- **Root cause**: llama.cpp's `qwen3vl` clip graph builder doesn't work correctly with this model's mmproj. The GGUF conversion (by `prithivMLmods`) is community-made and untested.

The official inference path for chandra-ocr-2 is via HuggingFace Transformers or vLLM.

## Solution

Replace `llama-cpp-python` + GGUF with `chandra-ocr[hf]` + HuggingFace Transformers. Use 4-bit quantization to fit within 8 GB VRAM budget.

## Dependencies

**Remove:**
- `llama-cpp-python` from `pyproject.toml`

**Add:**
- `chandra-ocr[hf]` (brings `transformers`, `accelerate`, `bitsandbytes`, `qwen-vl-utils`)

**Keep:**
- `torch` (already present for faster-whisper)
- `pillow` (already present for keyframe downscaling)

## Config Changes (`app/config.py`)

**Remove:**
- `OCR_GGUF_REPO`
- `OCR_QUANT`

**Keep:**
- `OCR_MODEL_DIR` — used as HuggingFace cache directory (`HF_HOME`)

**Add:**
- `OCR_MODEL_NAME = "datalab-to/chandra-ocr-2"` — HuggingFace model ID
- `OCR_PROMPT_TYPE = "ocr"` — chandra prompt type (handles tables, math, code blocks; no bounding boxes)

## Interface (Unchanged)

```python
@dataclass
class OcrResult:
    timestamp: float
    image_path: Path
    text: str

async def extract_text(keyframes: list[KeyFrame]) -> list[OcrResult]
```

`text` field contains the parsed markdown output from chandra (via `parse_markdown()`). Empty string if no text detected or OCR fails for that frame.

## Internal Implementation

### Model Loading

```python
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.bfloat16,
)

model = AutoModelForImageTextToText.from_pretrained(
    OCR_MODEL_NAME,
    quantization_config=quantization_config,
    device_map="auto",
    cache_dir=str(OCR_MODEL_DIR),
)
model.eval()
model.processor = AutoProcessor.from_pretrained(
    OCR_MODEL_NAME,
    cache_dir=str(OCR_MODEL_DIR),
)
model.processor.tokenizer.padding_side = "left"
```

### Inference

Use chandra's `generate_hf()` with `BatchInputItem`:

```python
from chandra.model.hf import generate_hf
from chandra.model.schema import BatchInputItem
from chandra.output import parse_markdown

batch = [BatchInputItem(image=Image.open(kf.image_path), prompt_type="ocr")]
result = generate_hf(batch, model)[0]
text = parse_markdown(result.raw)
```

Process keyframes one at a time (sequential, not batched) to keep VRAM usage low.

### CPU Fallback

If CUDA is unavailable, load without quantization on CPU:

```python
model = AutoModelForImageTextToText.from_pretrained(
    OCR_MODEL_NAME,
    device_map="cpu",
    torch_dtype=torch.float32,
    cache_dir=str(OCR_MODEL_DIR),
)
```

### VRAM Cleanup

Same pattern as current code — `finally` block ensures cleanup:

```python
finally:
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
```

## VRAM Budget

- 4-bit quantized model: ~2.5 GB
- Vision encoder overhead: ~0.4 GB
- Inference working memory: ~0.5 GB
- **Total**: ~3.4 GB (within 8 GB limit, leaves room for OS/driver)

Whisper and OCR never run concurrently (sequential pipeline), so VRAM is not shared.

## Test Plan

`tests/test_ocr.py` with `data/test/funsd_82092117.png` (fax cover sheet document):

- **`test_ocr_extract_text`**: Verify OCR extracts key phrases ("attorney general", "george baroody", "fax" or "facsimile")
- **`test_ocr_vram_freed`**: Verify VRAM delta < 100 MB after processing

Both tests require CUDA (`@requires_cuda` skip marker).

## Module Design Doc Update

`docs/module_design/ocr.md` must be updated to reflect:
- Runtime change: `llama-cpp-python` -> `transformers` + `chandra-ocr`
- Model loading: `BitsAndBytesConfig` 4-bit quantization
- Inference: `generate_hf()` + `parse_markdown()`
- Dependencies list change

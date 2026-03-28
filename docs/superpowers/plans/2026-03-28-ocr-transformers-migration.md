# OCR Transformers Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace broken llama-cpp-python GGUF OCR with chandra-ocr + HuggingFace Transformers (4-bit quantized).

**Architecture:** Load chandra-ocr-2 via `AutoModelForImageTextToText` with `BitsAndBytesConfig` 4-bit quantization (~2.5 GB VRAM). Use `chandra.model.hf.generate_hf()` for inference and `chandra.output.parse_markdown()` for output parsing. Same load/process/free lifecycle.

**Tech Stack:** `chandra-ocr[hf]`, `transformers`, `bitsandbytes`, `torch`, `PIL`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `pyproject.toml` | Modify | Remove `llama-cpp-python`, add `chandra-ocr[hf]` |
| `app/config.py` | Modify | Replace OCR config constants |
| `app/services/ocr.py` | Rewrite | Transformers-based OCR implementation |
| `tests/test_ocr.py` | Modify | Update imports (test logic stays same) |
| `docs/module_design/ocr.md` | Rewrite | Update module design doc |

---

### Task 1: Update dependencies

**Files:**
- Modify: `pyproject.toml:14` (replace `llama-cpp-python` with `chandra-ocr[hf]`)

- [ ] **Step 1: Remove llama-cpp-python, add chandra-ocr[hf]**

In `pyproject.toml`, replace:
```
    "llama-cpp-python>=0.3.19",
```
with:
```
    "chandra-ocr[hf]",
```

- [ ] **Step 2: Uninstall JamePeng's fork and install new deps**

Run:
```bash
uv pip uninstall llama-cpp-python && uv sync
```
Expected: `chandra-ocr` and its deps (`transformers`, `accelerate`, `bitsandbytes`, `qwen-vl-utils`) installed successfully.

- [ ] **Step 3: Verify imports work**

Run:
```bash
uv run python -c "from chandra.model.hf import generate_hf; from chandra.model.schema import BatchInputItem; from chandra.output import parse_markdown; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: replace llama-cpp-python with chandra-ocr[hf]"
```

---

### Task 2: Update config

**Files:**
- Modify: `app/config.py:23-25` (replace OCR constants)

- [ ] **Step 1: Replace OCR config constants**

In `app/config.py`, replace:
```python
OCR_MODEL_DIR = DATA_DIR / "ocr_models"
OCR_GGUF_REPO = "prithivMLmods/chandra-ocr-2-GGUF"
OCR_QUANT = "Q4_K_M"
```
with:
```python
OCR_MODEL_DIR = DATA_DIR / "ocr_models"
OCR_MODEL_NAME = "datalab-to/chandra-ocr-2"
OCR_PROMPT_TYPE = "ocr"
```

- [ ] **Step 2: Verify config imports**

Run:
```bash
uv run python -c "from app.config import OCR_MODEL_DIR, OCR_MODEL_NAME, OCR_PROMPT_TYPE; print(OCR_MODEL_NAME, OCR_PROMPT_TYPE)"
```
Expected: `datalab-to/chandra-ocr-2 ocr`

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "chore: update OCR config for chandra-ocr transformers"
```

---

### Task 3: Rewrite OCR service

**Files:**
- Rewrite: `app/services/ocr.py`

- [ ] **Step 1: Write the new OCR service**

Replace `app/services/ocr.py` with:

```python
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image

from app.config import OCR_MODEL_DIR, OCR_MODEL_NAME, OCR_PROMPT_TYPE
from app.services.keyframes import KeyFrame

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


def _run_ocr(keyframes: list[KeyFrame]) -> list[OcrResult]:
    """Load model, run OCR on all keyframes, release model."""
    from chandra.model.hf import generate_hf
    from chandra.model.schema import BatchInputItem
    from chandra.output import parse_markdown

    model, gpu = _load_model()
    try:
        results = []
        for kf in keyframes:
            try:
                batch = [BatchInputItem(
                    image=Image.open(kf.image_path),
                    prompt_type=OCR_PROMPT_TYPE,
                )]
                gen_result = generate_hf(batch, model)[0]
                text = parse_markdown(gen_result.raw)
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
        del model
        if gpu:
            torch.cuda.empty_cache()


async def extract_text(keyframes: list[KeyFrame]) -> list[OcrResult]:
    """Run OCR on keyframe images using chandra-ocr-2."""
    if not keyframes:
        return []

    logger.info("Running OCR on %d keyframes", len(keyframes))
    results = await asyncio.to_thread(_run_ocr, keyframes)
    ocr_count = sum(1 for r in results if r.text)
    logger.info("OCR complete: %d/%d keyframes had text", ocr_count, len(results))
    return results
```

- [ ] **Step 2: Verify module imports cleanly**

Run:
```bash
uv run python -c "from app.services.ocr import extract_text, OcrResult; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/services/ocr.py
git commit -m "feat: rewrite OCR service using chandra-ocr + transformers"
```

---

### Task 4: Run OCR tests

**Files:**
- Modify: `tests/test_ocr.py` (no changes needed — interface is unchanged)

- [ ] **Step 1: Verify test file needs no changes**

The existing `tests/test_ocr.py` imports `extract_text` and `OcrResult` from `app.services.ocr` and uses `KeyFrame` from `app.services.keyframes`. The interface hasn't changed, so the test should work as-is.

Run:
```bash
uv run python -c "from tests.test_ocr import *; print('imports OK')"
```
Expected: `imports OK`

- [ ] **Step 2: Run OCR tests**

Run:
```bash
uv run pytest tests/test_ocr.py -v
```
Expected: Both `test_ocr_extract_text` and `test_ocr_vram_freed` PASS. The first run will download the model (~10 GB for full weights, cached in `data/ocr_models/`).

- [ ] **Step 3: Check test output for correct OCR**

Review the test log at `logs/tests/test_ocr_*.log`. The OCR output should contain readable text from the fax cover sheet, including "attorney general", "george baroody", and "fax" or "facsimile".

- [ ] **Step 4: If tests fail, debug**

Common issues:
- `bitsandbytes` not finding CUDA: run `uv run python -c "import bitsandbytes; print(bitsandbytes.__version__)"` to verify
- Model download fails: check network access and disk space in `data/ocr_models/`
- VRAM OOM: check `nvidia-smi` — nothing else should be using GPU during tests
- Import errors from `chandra`: verify `uv run pip show chandra-ocr` shows the `[hf]` extra is installed

- [ ] **Step 5: Commit test results confirmation**

No code changes needed if tests pass. If any test adjustments were required (e.g., VRAM tolerance), commit them:
```bash
git add tests/test_ocr.py
git commit -m "test: verify OCR tests pass with chandra-ocr transformers backend"
```

---

### Task 5: Update module design doc

**Files:**
- Rewrite: `docs/module_design/ocr.md`

- [ ] **Step 1: Rewrite module design doc**

Replace `docs/module_design/ocr.md` with:

```markdown
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
- VRAM cleanup: `del model` + `torch.cuda.empty_cache()` in `finally` block

## Interface

```python
@dataclass
class OcrResult:
    timestamp: float
    image_path: Path
    text: str

async def extract_text(keyframes: list[KeyFrame]) -> list[OcrResult]
```

Per-keyframe OCR: returns one `OcrResult` per input keyframe. Empty `text` if no text detected or OCR fails for that frame.

## Dependencies

- `chandra-ocr[hf]` (model prompting, output parsing)
- `transformers` (model loading and inference)
- `bitsandbytes` (4-bit quantization for GPU)
- `torch` for CUDA detection and VRAM cleanup
- `PIL` for image loading
- `app.config` for `OCR_MODEL_DIR`, `OCR_MODEL_NAME`, `OCR_PROMPT_TYPE`
- `app.services.keyframes` for `KeyFrame` type
```

- [ ] **Step 2: Commit**

```bash
git add docs/module_design/ocr.md
git commit -m "docs: update OCR module design for chandra-ocr transformers backend"
```

---

### Task 6: Clean up old GGUF model files

**Files:** None (filesystem cleanup)

- [ ] **Step 1: Remove old GGUF model files**

The old GGUF models in `data/ocr_models/` are no longer needed (~3.4 GB total). Remove them:

```bash
rm -f data/ocr_models/chandra-ocr-2-Q4_K_M.gguf data/ocr_models/chandra-ocr-2.mmproj-q8_0.gguf
```

- [ ] **Step 2: Remove test artifacts**

```bash
rm -f data/test/tiny_test.png data/test/simple_text.png
```

- [ ] **Step 3: Verify no stale imports**

Run:
```bash
uv run python -c "from app.services.ocr import extract_text; from app.config import OCR_MODEL_NAME; print('All clean')"
```
Expected: `All clean`

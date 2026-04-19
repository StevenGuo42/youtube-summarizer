import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_PATH = DATA_DIR / "settings.json"

_DEFAULTS: dict[str, Any] = {
    "llm": {
        "model": "claude-sonnet-4-20250514",
        "custom_prompt": None,
        "custom_prompt_mode": "replace",
        "output_language": None,
    },
    "worker": {
        "processing_mode": "sequential",
        "batch_size": 5,
    },
}


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("Failed to read settings.json, using defaults")
        return {}


def _write_settings(data: dict) -> None:
    """Atomic write: write to temp file in same dir, then rename."""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".json.tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(SETTINGS_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def get_llm_settings() -> dict:
    settings = _read_settings()
    llm = settings.get("llm", {})
    return {**_DEFAULTS["llm"], **{k: v for k, v in llm.items() if v is not None or k in _DEFAULTS["llm"]}}


def save_llm_settings(
    model: str | None = None,
    custom_prompt: str | None = None,
    custom_prompt_mode: str | None = None,
    output_language: str | None = None,
) -> None:
    settings = _read_settings()
    llm = settings.get("llm", {})
    llm["model"] = model or _DEFAULTS["llm"]["model"]
    llm["custom_prompt"] = custom_prompt
    llm["custom_prompt_mode"] = custom_prompt_mode or "replace"
    llm["output_language"] = output_language
    settings["llm"] = llm
    _write_settings(settings)


def get_worker_settings() -> dict:
    settings = _read_settings()
    worker = settings.get("worker", {})
    return {**_DEFAULTS["worker"], **{k: v for k, v in worker.items() if v is not None}}


def save_worker_settings(
    processing_mode: str | None = None,
    batch_size: int | None = None,
) -> None:
    settings = _read_settings()
    worker = settings.get("worker", {})
    if processing_mode is not None:
        worker["processing_mode"] = processing_mode
    if batch_size is not None:
        worker["batch_size"] = batch_size
    settings["worker"] = worker
    _write_settings(settings)

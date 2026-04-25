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
        "active_provider": "claude",
        "providers": {
            "claude": {
                "model": "claude-sonnet-4-20250514",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
            },
            "codex": {
                # gpt-5.4 is the verified working default — gpt-5 returns HTTP 400 on ChatGPT plans
                "model": "gpt-5.4",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
            },
            "litellm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": None,
                "api_base_url": None,
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
            },
        },
    },
    "worker": {
        "processing_mode": "sequential",
        "batch_size": 5,
    },
    "defaults": {
        "dedup_mode": "regular",
        "keyframe_mode": "image",
    },
}


def _mask_api_key(key: str | None) -> str | None:
    """Mask an API key for safe display in API responses. Returns ...XXXX (last 4 chars)."""
    if not key or len(key) < 4:
        return key
    return f"...{key[-4:]}"


def _is_masked(value: str | None) -> bool:
    """Return True if the value looks like a masked API key (starts with '...')."""
    return bool(value and value.startswith("..."))


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


def _migrate_if_needed(settings: dict) -> dict:
    """One-shot migration: flat llm.* shape → llm.providers.claude on first read.

    Detection: if settings["llm"] lacks both "active_provider" AND "providers",
    it's the old flat shape (v1.1). Migrate in-memory and write back atomically.
    Rollback safety: atomic write preserves old file on failure.
    """
    llm = settings.get("llm", {})
    if not llm:
        return settings  # Empty or absent llm block — no migration needed
    if "active_provider" in llm or "providers" in llm:
        return settings  # Already new shape — no migration needed

    # Old flat shape detected: migrate to nested
    logger.info("Migrating settings.json from flat llm.* to llm.providers.claude shape")
    defaults = _DEFAULTS["llm"]["providers"]
    claude_config = {
        "model": llm.get("model") or defaults["claude"]["model"],
        "custom_prompt": llm.get("custom_prompt"),
        "custom_prompt_mode": llm.get("custom_prompt_mode") or "replace",
        "output_language": llm.get("output_language"),
    }
    settings["llm"] = {
        "active_provider": "claude",
        "providers": {
            "claude": claude_config,
            "codex": dict(defaults["codex"]),
            "litellm": dict(defaults["litellm"]),
        },
    }
    # Write migrated settings back atomically — subsequent reads see new shape
    try:
        _write_settings(settings)
        logger.info("Settings migration complete")
    except Exception:
        logger.exception("Failed to write migrated settings; in-memory state is migrated")
    return settings


def _deep_merge_llm_defaults(llm: dict) -> dict:
    """Deep-merge _DEFAULTS["llm"] under each provider to fill gaps."""
    result = dict(_DEFAULTS["llm"])
    result["active_provider"] = llm.get("active_provider", result["active_provider"])
    providers = {}
    for provider_name, default_cfg in _DEFAULTS["llm"]["providers"].items():
        stored = llm.get("providers", {}).get(provider_name, {})
        merged = dict(default_cfg)
        for k, v in stored.items():
            if v is not None or k in default_cfg:
                merged[k] = v
        providers[provider_name] = merged
    result["providers"] = providers
    return result


def get_llm_settings() -> dict:
    """Return current LLM settings, applying migration and defaults.

    Returns nested dict: {active_provider, providers: {claude, codex, litellm}}.
    """
    settings = _read_settings()
    settings = _migrate_if_needed(settings)
    llm = settings.get("llm", {})
    return _deep_merge_llm_defaults(llm)


def save_llm_settings(
    active_provider: str | None = None,
    providers_config: dict | None = None,
    # Legacy flat-schema params (kept for backward compat during transition)
    model: str | None = None,
    custom_prompt: str | None = None,
    custom_prompt_mode: str | None = None,
    output_language: str | None = None,
    litellm_api_key: str | None = None,
) -> None:
    """Persist LLM settings.

    New callers pass active_provider + providers_config (nested schema).
    If providers_config["litellm"]["api_key"] is masked (starts with "..."),
    the stored key is preserved unchanged (no-op for masked echo saves).
    """
    settings = _read_settings()
    settings = _migrate_if_needed(settings)

    if active_provider is not None or providers_config is not None:
        # New nested-schema save path (Plan 05 router)
        llm = settings.get("llm", {})
        if active_provider is not None:
            llm["active_provider"] = active_provider
        if providers_config is not None:
            existing_providers = llm.get("providers", {})
            for provider_name, cfg in providers_config.items():
                existing_cfg = existing_providers.get(provider_name, {})
                merged = dict(existing_cfg)
                merged.update(cfg)
                # API key no-op guard: if incoming key is masked, keep stored key
                if provider_name == "litellm" and _is_masked(cfg.get("api_key")):
                    merged["api_key"] = existing_cfg.get("api_key")
                existing_providers[provider_name] = merged
            llm["providers"] = existing_providers
        settings["llm"] = llm
    elif model is not None or custom_prompt is not None or custom_prompt_mode is not None or output_language is not None:
        # Legacy flat save path — update claude provider only
        llm = settings.get("llm", {})
        providers = llm.get("providers", {})
        claude_cfg = providers.get("claude", {})
        if model is not None:
            claude_cfg["model"] = model
        if custom_prompt is not None:
            claude_cfg["custom_prompt"] = custom_prompt
        if custom_prompt_mode is not None:
            claude_cfg["custom_prompt_mode"] = custom_prompt_mode
        if output_language is not None:
            claude_cfg["output_language"] = output_language
        providers["claude"] = claude_cfg
        llm["providers"] = providers
        settings["llm"] = llm
    elif litellm_api_key is not None:
        # Standalone api_key update (test helper / direct key save)
        llm = settings.get("llm", {})
        providers = llm.get("providers", {})
        litellm_cfg = providers.get("litellm", {})
        if not _is_masked(litellm_api_key):
            litellm_cfg["api_key"] = litellm_api_key
        providers["litellm"] = litellm_cfg
        llm["providers"] = providers
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


def get_default_options() -> dict:
    settings = _read_settings()
    defaults = settings.get("defaults", {})
    return {**_DEFAULTS["defaults"], **{k: v for k, v in defaults.items() if v is not None}}


def save_default_options(
    dedup_mode: str | None = None,
    keyframe_mode: str | None = None,
) -> None:
    settings = _read_settings()
    defaults = settings.get("defaults", {})
    if dedup_mode is not None:
        defaults["dedup_mode"] = dedup_mode
    if keyframe_mode is not None:
        defaults["keyframe_mode"] = keyframe_mode
    settings["defaults"] = defaults
    _write_settings(settings)

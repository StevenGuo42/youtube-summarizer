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
                "active_litellm_provider": "openai",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
                "providers": {
                    "openai":    {"model": "gpt-4o",                   "api_key": None, "api_base_url": None},
                    "anthropic": {"model": "claude-sonnet-4-20250514",  "api_key": None, "api_base_url": None},
                    "gemini":    {"model": "gemini-2.5-flash",          "api_key": None, "api_base_url": None},
                    "ollama":    {"model": "llama3",                    "api_key": None, "api_base_url": "http://localhost:11434"},
                    "vllm":      {"model": "",                          "api_key": None, "api_base_url": "http://localhost:8000/v1"},
                    "custom":    {"model": "",                          "api_key": None, "api_base_url": ""},
                },
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
        # New shape detected — but check if litellm sub-slot needs Phase 13 → 13.1 migration
        litellm_slot = llm.get("providers", {}).get("litellm", {})
        if "provider" in litellm_slot and "providers" not in litellm_slot:
            # Phase 13 flat litellm shape detected: migrate to nested sub-providers
            logger.info("Migrating settings.json: litellm flat slot → per-provider nested shape")
            old_provider = litellm_slot.get("provider") or "openai"
            _ALLOWED = {"openai", "anthropic", "gemini", "ollama", "vllm", "custom"}
            if old_provider not in _ALLOWED:
                old_provider = "openai"  # defensive: never lose stored key
            old_key = litellm_slot.get("api_key")
            old_model = litellm_slot.get("model")
            old_base_url = litellm_slot.get("api_base_url")
            # Start from defaults for all 5 sub-providers
            default_sub = _DEFAULTS["llm"]["providers"]["litellm"]["providers"]
            new_providers_dict = {name: dict(cfg) for name, cfg in default_sub.items()}
            # Migrate old credentials into the matching slot (never lose stored key)
            if old_key is not None:
                new_providers_dict[old_provider]["api_key"] = old_key
            if old_model:
                new_providers_dict[old_provider]["model"] = old_model
            if old_base_url is not None:
                new_providers_dict[old_provider]["api_base_url"] = old_base_url
            # Build the new litellm slot
            new_litellm = {
                "active_litellm_provider": old_provider,
                "custom_prompt": litellm_slot.get("custom_prompt"),
                "custom_prompt_mode": litellm_slot.get("custom_prompt_mode") or "replace",
                "output_language": litellm_slot.get("output_language"),
                "providers": new_providers_dict,
            }
            settings["llm"]["providers"]["litellm"] = new_litellm
            try:
                _write_settings(settings)
                logger.info("LiteLLM per-provider migration complete")
            except Exception:
                logger.exception("Failed to write Phase-13.1 migrated settings; in-memory migrated")
        return settings  # Already new shape (with litellm sub-migration applied if needed)

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
        if provider_name == "litellm":
            # Merge top-level litellm fields
            merged = {k: v for k, v in default_cfg.items() if k != "providers"}
            for k in ("active_litellm_provider", "custom_prompt", "custom_prompt_mode", "output_language"):
                val = stored.get(k)
                if val is not None:
                    merged[k] = val
            # Merge inner sub-providers
            stored_sub = stored.get("providers", {})
            default_sub = default_cfg["providers"]
            merged_sub = {}
            for sub_name, sub_defaults in default_sub.items():
                sub_stored = stored_sub.get(sub_name, {})
                sub_merged = dict(sub_defaults)
                for k, v in sub_stored.items():
                    sub_merged[k] = v  # always take stored value (even None)
                merged_sub[sub_name] = sub_merged
            merged["providers"] = merged_sub
            providers[provider_name] = merged
        else:
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
) -> None:
    """Persist LLM settings.

    New callers pass active_provider + providers_config (nested schema).
    For litellm, each sub-provider's api_key is independently guarded:
    if the incoming api_key is masked (starts with "..."), the stored key is preserved.
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
                if provider_name == "litellm":
                    # Handle nested sub-providers with per-provider key no-op
                    incoming_sub = cfg.get("providers", {})
                    existing_sub = existing_cfg.get("providers", {})
                    if incoming_sub:
                        merged_sub = dict(existing_sub)
                        for sub_name, sub_cfg in incoming_sub.items():
                            existing_sub_cfg = existing_sub.get(sub_name, {})
                            sub_merged = dict(existing_sub_cfg)
                            sub_merged.update(sub_cfg)
                            # Per-provider key no-op: masked key does NOT overwrite stored key
                            if _is_masked(sub_cfg.get("api_key")):
                                sub_merged["api_key"] = existing_sub_cfg.get("api_key")
                            merged_sub[sub_name] = sub_merged
                        merged["providers"] = merged_sub
                    # Remove legacy "api_key" / "provider" at top level if still present
                    merged.pop("api_key", None)
                    merged.pop("provider", None)
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

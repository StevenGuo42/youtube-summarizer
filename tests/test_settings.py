"""Tests for app.settings — unit tests for settings migration and API key masking (D-14, D-15)."""
import json
import logging

import pytest

logger = logging.getLogger(__name__)


class TestLlmMigration:
    """Flat llm.* shape migrates to llm.providers.claude on first read after phase 13 (D-14)."""

    def test_flat_shape_migrated(self, tmp_path, monkeypatch):
        """Flat llm.* shape is migrated to nested providers.claude on first read."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "model": "claude-sonnet-4-6",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        result = settings_mod.get_llm_settings()
        assert result["active_provider"] == "claude"
        assert result["providers"]["claude"]["model"] == "claude-sonnet-4-6"
        assert "codex" in result["providers"]
        assert "litellm" in result["providers"]
        logger.info("Migration result: %s", result)

    def test_already_nested_not_re_migrated(self, tmp_path, monkeypatch):
        """Nested shape is not re-migrated on subsequent reads."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "claude",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-6", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": None, "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        result = settings_mod.get_llm_settings()
        # Should not re-write and should preserve nested shape
        assert result["active_provider"] == "claude"
        assert result["providers"]["codex"]["model"] == "gpt-5.4"
        logger.info("No-op migration: %s", result)

    def test_migration_preserves_existing_model(self, tmp_path, monkeypatch):
        """Existing model string is preserved in claude provider after migration."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {"model": "claude-opus-4-20250514", "custom_prompt": None,
                    "custom_prompt_mode": "replace", "output_language": None},
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        result = settings_mod.get_llm_settings()
        assert result["providers"]["claude"]["model"] == "claude-opus-4-20250514"
        logger.info("Model preserved: %s", result["providers"]["claude"]["model"])

    def test_codex_default_model_is_gpt_5_4(self, tmp_path, monkeypatch):
        """After migration, Codex default model is gpt-5.4 (not gpt-5 which returns HTTP 400)."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {"model": "claude-sonnet-4-6", "custom_prompt": None,
                    "custom_prompt_mode": "replace", "output_language": None},
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        result = settings_mod.get_llm_settings()
        assert result["providers"]["codex"]["model"] == "gpt-5.4"
        logger.info("Codex default model: %s", result["providers"]["codex"]["model"])


class TestMaskedApiKey:
    """API key masking logic for LiteLLM settings (D-15)."""

    def test_mask_long_key(self):
        from app.settings import _mask_api_key
        assert _mask_api_key("sk-abcdefgh") == "...efgh"

    def test_mask_short_key_unchanged(self):
        from app.settings import _mask_api_key
        assert _mask_api_key("abc") == "abc"

    def test_mask_none_unchanged(self):
        from app.settings import _mask_api_key
        assert _mask_api_key(None) is None

    def test_is_masked_detects_prefix(self):
        from app.settings import _is_masked
        assert _is_masked("...abcd") is True
        assert _is_masked("sk-real") is False

    def test_masked_save_is_noop(self, tmp_path, monkeypatch):
        """Saving a masked API key value does not overwrite the stored real key."""
        import app.settings as settings_mod
        real_key = "sk-realkeyabcdefgh"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {"provider": "openai", "model": "gpt-4o",
                                "api_key": real_key, "api_base_url": None,
                                "custom_prompt": None, "custom_prompt_mode": "replace",
                                "output_language": None},
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        # Simulate saving with a masked value (the kind returned by GET /api/settings/llm)
        masked = f"...{real_key[-4:]}"
        settings_mod.save_llm_settings(active_provider="litellm", litellm_api_key=masked)
        result = settings_mod.get_llm_settings()
        # Real key should be unchanged — masked save is a no-op
        assert result["providers"]["litellm"]["api_key"] == real_key
        logger.info("No-op confirmed: real key unchanged after masked save")

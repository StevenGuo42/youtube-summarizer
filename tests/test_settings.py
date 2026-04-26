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
        # Write already-nested Phase 13.1 shape (migration already run)
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "active_litellm_provider": "openai",
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                        "providers": {
                            "openai":    {"model": "gpt-4o", "api_key": real_key, "api_base_url": None},
                            "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": None, "api_base_url": None},
                            "gemini":    {"model": "gemini-2.5-flash", "api_key": None, "api_base_url": None},
                            "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                            "custom":    {"model": "", "api_key": None, "api_base_url": ""},
                        },
                    },
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        # Simulate saving with a masked value (the kind returned by GET /api/settings/llm)
        masked = f"...{real_key[-4:]}"
        settings_mod.save_llm_settings(
            active_provider="litellm",
            providers_config={"litellm": {
                "active_litellm_provider": "openai",
                "custom_prompt": None,
                "custom_prompt_mode": "replace",
                "output_language": None,
                "providers": {
                    "openai":    {"model": "gpt-4o", "api_key": masked, "api_base_url": None},
                    "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": None, "api_base_url": None},
                    "gemini":    {"model": "gemini-2.5-flash", "api_key": None, "api_base_url": None},
                    "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                    "custom":    {"model": "", "api_key": None, "api_base_url": ""},
                },
            }},
        )
        result = settings_mod.get_llm_settings()
        # Real key should be unchanged — masked save is a no-op
        assert result["providers"]["litellm"]["providers"]["openai"]["api_key"] == real_key
        logger.info("No-op confirmed: real key unchanged after masked save")


class TestLitellmPerProviderMigration:
    """Per-provider LiteLLM migration: flat Phase 13 slot → nested sub-providers (D-1.2)."""

    def test_flat_litellm_migrates_to_nested(self, tmp_path, monkeypatch):
        """Phase 13 flat litellm.{provider, api_key, model} migrates to providers[provider] slot."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "provider": "anthropic",
                        "model": "claude-opus-4",
                        "api_key": "sk-ant-realkey",
                        "api_base_url": None,
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                    },
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        result = settings_mod.get_llm_settings()
        litellm = result["providers"]["litellm"]
        assert litellm["active_litellm_provider"] == "anthropic"
        assert litellm["providers"]["anthropic"]["api_key"] == "sk-ant-realkey"
        assert litellm["providers"]["anthropic"]["model"] == "claude-opus-4"
        # Other providers should have default values
        assert litellm["providers"]["openai"]["api_key"] is None
        logger.info("Migration result litellm slot: %s", litellm)

    def test_migration_unknown_provider_defaults_to_openai(self, tmp_path, monkeypatch):
        """If stored provider is not in allow-list, migration defaults to openai and preserves key."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "provider": "unknown-provider",
                        "model": "some-model",
                        "api_key": "sk-orphaned-key",
                        "api_base_url": None,
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                    },
                },
            },
        }))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        result = settings_mod.get_llm_settings()
        litellm = result["providers"]["litellm"]
        assert litellm["active_litellm_provider"] == "openai"
        assert litellm["providers"]["openai"]["api_key"] == "sk-orphaned-key"
        logger.info("Unknown provider defaulted to openai: %s", litellm["active_litellm_provider"])

    def test_already_nested_is_idempotent(self, tmp_path, monkeypatch):
        """Already-nested litellm shape is not re-migrated on subsequent reads."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        original = {
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "active_litellm_provider": "gemini",
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                        "providers": {
                            "openai":    {"model": "gpt-4o", "api_key": None, "api_base_url": None},
                            "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": "sk-stored", "api_base_url": None},
                            "gemini":    {"model": "gemini-2.5-flash", "api_key": "AIza-stored", "api_base_url": None},
                            "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                            "custom":    {"model": "", "api_key": None, "api_base_url": ""},
                        },
                    },
                },
            },
        }
        settings_file.write_text(json.dumps(original))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        # Call get_llm_settings twice — second call should not re-migrate
        result1 = settings_mod.get_llm_settings()
        result2 = settings_mod.get_llm_settings()
        assert result1["providers"]["litellm"]["active_litellm_provider"] == "gemini"
        assert result2["providers"]["litellm"]["providers"]["anthropic"]["api_key"] == "sk-stored"
        assert result2["providers"]["litellm"]["providers"]["gemini"]["api_key"] == "AIza-stored"
        logger.info("Idempotency confirmed: %s", result2["providers"]["litellm"]["active_litellm_provider"])


class TestLitellmPerProviderMasking:
    """Per-provider api_key masking and no-op save (D-1.4, D-1.6)."""

    def _make_nested_settings(self, tmp_path, openai_key=None, anthropic_key=None):
        """Helper: write nested litellm settings with specified keys."""
        return {
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "active_litellm_provider": "openai",
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                        "providers": {
                            "openai":    {"model": "gpt-4o", "api_key": openai_key, "api_base_url": None},
                            "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": anthropic_key, "api_base_url": None},
                            "gemini":    {"model": "gemini-2.5-flash", "api_key": None, "api_base_url": None},
                            "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                            "vllm":      {"model": "", "api_key": None, "api_base_url": "http://localhost:8000/v1"},
                            "custom":    {"model": "", "api_key": None, "api_base_url": ""},
                        },
                    },
                },
            },
        }

    def test_each_provider_key_masked_independently(self, tmp_path, monkeypatch):
        """get_llm_settings returns raw keys; router masks them — each sub-provider masked separately."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(
            self._make_nested_settings(tmp_path, openai_key="sk-openaiabcdefgh", anthropic_key="sk-ant-anthropicXYZ")
        ))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        result = settings_mod.get_llm_settings()
        # get_llm_settings returns UNMASKED keys (masking is router's job)
        litellm = result["providers"]["litellm"]
        assert litellm["providers"]["openai"]["api_key"] == "sk-openaiabcdefgh"
        assert litellm["providers"]["anthropic"]["api_key"] == "sk-ant-anthropicXYZ"
        # Verify masking helper produces expected output
        from app.settings import _mask_api_key
        assert _mask_api_key("sk-openaiabcdefgh") == "...efgh"
        assert _mask_api_key("sk-ant-anthropicXYZ") == "...cXYZ"
        logger.info("Per-provider masking verified")

    def test_masked_openai_save_is_noop_does_not_affect_anthropic(self, tmp_path, monkeypatch):
        """Saving masked openai key is no-op for openai only; anthropic key update still applies."""
        import app.settings as settings_mod
        settings_file = tmp_path / "settings.json"
        real_openai = "sk-openairealkey1234"
        settings_file.write_text(json.dumps(
            self._make_nested_settings(tmp_path, openai_key=real_openai, anthropic_key=None)
        ))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)

        # Simulate POST: masked openai key + real new anthropic key
        masked_openai = f"...{real_openai[-4:]}"
        new_litellm = {
            "active_litellm_provider": "anthropic",
            "custom_prompt": None,
            "custom_prompt_mode": "replace",
            "output_language": None,
            "providers": {
                "openai":    {"model": "gpt-4o", "api_key": masked_openai, "api_base_url": None},
                "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": "sk-ant-newkey9876", "api_base_url": None},
                "gemini":    {"model": "gemini-2.5-flash", "api_key": None, "api_base_url": None},
                "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                "custom":    {"model": "", "api_key": None, "api_base_url": ""},
            },
        }
        settings_mod.save_llm_settings(
            active_provider="litellm",
            providers_config={"litellm": new_litellm},
        )
        result = settings_mod.get_llm_settings()
        litellm = result["providers"]["litellm"]
        assert litellm["providers"]["openai"]["api_key"] == real_openai, "masked save must be no-op for openai"
        assert litellm["providers"]["anthropic"]["api_key"] == "sk-ant-newkey9876", "anthropic key should be updated"
        logger.info("Per-provider no-op confirmed; openai=%s, anthropic=%s",
                    litellm["providers"]["openai"]["api_key"], litellm["providers"]["anthropic"]["api_key"])


class TestLlmEndpointsPhase131:
    """Router-level tests for Phase 13.1 nested LiteLLM shape (D-1.4, D-1.6)."""

    def _nested_settings_json(self, openai_key="sk-openai1234abcd"):
        return {
            "llm": {
                "active_provider": "litellm",
                "providers": {
                    "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                               "custom_prompt_mode": "replace", "output_language": None},
                    "codex": {"model": "gpt-5.4", "custom_prompt": None,
                              "custom_prompt_mode": "replace", "output_language": None},
                    "litellm": {
                        "active_litellm_provider": "openai",
                        "custom_prompt": None,
                        "custom_prompt_mode": "replace",
                        "output_language": None,
                        "providers": {
                            "openai":    {"model": "gpt-4o", "api_key": openai_key, "api_base_url": None},
                            "anthropic": {"model": "claude-sonnet-4-20250514", "api_key": None, "api_base_url": None},
                            "gemini":    {"model": "gemini-2.5-flash", "api_key": None, "api_base_url": None},
                            "ollama":    {"model": "llama3", "api_key": None, "api_base_url": "http://localhost:11434"},
                            "vllm":      {"model": "", "api_key": None, "api_base_url": "http://localhost:8000/v1"},
                            "custom":    {"model": "", "api_key": None, "api_base_url": ""},
                        },
                    },
                },
            },
        }

    def test_get_returns_nested_with_masked_keys(self, tmp_path, monkeypatch):
        """GET /api/settings/llm returns nested litellm shape with per-provider masked api_key."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        real_key = "sk-openai1234abcd"
        settings_file.write_text(json.dumps(self._nested_settings_json(real_key)))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        client = TestClient(fastapi_app)
        resp = client.get("/api/settings/llm")
        assert resp.status_code == 200
        data = resp.json()
        litellm = data["providers"]["litellm"]
        assert "active_litellm_provider" in litellm
        assert "providers" in litellm
        openai_key = litellm["providers"]["openai"]["api_key"]
        assert openai_key == f"...{real_key[-4:]}", f"Expected masked key, got {openai_key}"
        assert litellm["providers"]["anthropic"]["api_key"] is None
        logger.info("GET nested shape OK, openai key masked: %s", openai_key)

    def test_test_endpoint_success(self, tmp_path, monkeypatch):
        """POST /api/settings/llm/test returns ok=True with latency_ms when provider responds."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json("sk-realopenai1234")))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

        # Mock litellm.acompletion to return a stub response immediately
        async def fake_acompletion(*args, **kwargs):
            class Stub:
                choices = [type("M", (), {"message": type("Msg", (), {"content": "ok"})})()]
            return Stub()
        import litellm
        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "openai"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["error"] is None
        assert isinstance(data["latency_ms"], int) and data["latency_ms"] >= 0
        logger.info("Test endpoint success: %s", data)

    def test_test_endpoint_auth_failure(self, tmp_path, monkeypatch):
        """POST /api/settings/llm/test returns ok=False with sanitized error on auth failure."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json("sk-bogus")))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

        import litellm
        async def fake_acompletion(*args, **kwargs):
            # AuthenticationError signature: (message, llm_provider, model)
            raise litellm.AuthenticationError(
                "Invalid API key. body: Authorization: Bearer sk-bogus",
                llm_provider="openai",
                model="gpt-4o",
            )
        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "openai"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        # Error must NOT echo the bearer token from the upstream message
        assert "sk-bogus" not in (data["error"] or "")
        assert "authentication" in data["error"].lower()
        logger.info("Test endpoint auth-fail (sanitized): %s", data)

    def test_test_endpoint_unknown_provider(self, tmp_path, monkeypatch):
        """POST /api/settings/llm/test with unknown provider returns 400."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json()))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "bogus-llm"})
        assert resp.status_code == 400
        logger.info("Test endpoint rejected unknown provider with 400")

    def test_test_endpoint_no_key_configured(self, tmp_path, monkeypatch):
        """POST /api/settings/llm/test returns ok=False when sub-provider has no api_key configured (and no override)."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        # anthropic has api_key=None in the helper
        settings_file.write_text(json.dumps(self._nested_settings_json()))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "anthropic"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "api key" in data["error"].lower()

    def test_test_endpoint_uses_override_key(self, tmp_path, monkeypatch):
        """POST /api/settings/llm/test uses request-body api_key override when provided."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json("sk-stored1234")))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

        captured = {}
        async def fake_acompletion(*args, **kwargs):
            captured["api_key"] = kwargs.get("api_key")
            class Stub:
                choices = [type("M", (), {"message": type("Msg", (), {"content": "ok"})})()]
            return Stub()
        import litellm
        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "openai", "api_key": "sk-override9999"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert captured["api_key"] == "sk-override9999", "override key should be passed to litellm, not stored"
        logger.info("Override key correctly passed to litellm")

    def test_test_endpoint_bad_request_extracts_inner_message(self, tmp_path, monkeypatch):
        """BadRequestError surfaces the upstream provider's user-facing message (e.g. credit balance)."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json("sk-real1234abcd")))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)

        import litellm
        async def fake_acompletion(*args, **kwargs):
            raise litellm.BadRequestError(
                'litellm.BadRequestError: AnthropicException - {"type":"error","error":'
                '{"type":"invalid_request_error","message":"Your credit balance is too low. '
                'Authorization: Bearer sk-leakedkey1234"}}',
                model="claude-haiku-4-5",
                llm_provider="anthropic",
            )
        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

        client = TestClient(fastapi_app)
        resp = client.post("/api/settings/llm/test", json={"provider": "openai"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        # Inner message extracted
        assert "credit balance" in data["error"].lower()
        # Bearer token / sk-... redacted defensively
        assert "sk-leakedkey1234" not in data["error"]
        assert "leakedkey" not in data["error"]
        # Redaction placeholder is fine; original token must not survive
        import re
        assert not re.search(r"sk-[A-Za-z0-9_\-]{6,}", data["error"]), \
            f"unredacted sk-... found: {data['error']!r}"
        logger.info("BadRequestError surfaced inner message (sanitized): %s", data["error"])

    def test_post_rejects_invalid_active_litellm_provider(self, tmp_path, monkeypatch):
        """POST /api/settings/llm returns 400 for unknown active_litellm_provider."""
        import app.settings as settings_mod
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(self._nested_settings_json()))
        monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_file)
        monkeypatch.setattr(settings_mod, "DATA_DIR", tmp_path)
        client = TestClient(fastapi_app)
        payload = {
            "active_provider": "litellm",
            "providers": {
                "claude": {"model": "claude-sonnet-4-20250514", "custom_prompt": None,
                           "custom_prompt_mode": "replace", "output_language": None},
                "codex":  {"model": "gpt-5.4", "custom_prompt": None,
                           "custom_prompt_mode": "replace", "output_language": None},
                "litellm": {
                    "active_litellm_provider": "unknown-provider",
                    "custom_prompt": None, "custom_prompt_mode": "replace", "output_language": None,
                    "providers": {
                        "openai": {"model": "gpt-4o", "api_key": None, "api_base_url": None},
                    },
                },
            },
        }
        resp = client.post("/api/settings/llm", json=payload)
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        logger.info("Invalid active_litellm_provider rejected with 400")

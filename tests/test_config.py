"""Tests for config.get_settings()'s Azure-vs-plain-OpenAI mode selection."""

from __future__ import annotations

import pytest

import config


def _clear_all(monkeypatch):
    for name in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "EMBEDDING_DEPLOYMENT",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_azure_mode_selected_when_endpoint_set(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    monkeypatch.setenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

    settings = config.get_settings()

    assert settings.azure_openai_endpoint == "https://example.openai.azure.com"
    assert settings.azure_openai_api_key.get_secret_value() == "azure-key"
    assert settings.azure_openai_deployment == "gpt-4o"
    assert settings.embedding_deployment == "text-embedding-3-small"
    assert settings.openai_api_key is None
    assert settings.openai_model is None


def test_azure_mode_missing_vars_raises_with_names(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")

    with pytest.raises(RuntimeError) as exc_info:
        config.get_settings()

    message = str(exc_info.value)
    assert "AZURE_OPENAI_API_KEY" in message
    assert "AZURE_OPENAI_DEPLOYMENT" in message
    assert "EMBEDDING_DEPLOYMENT" in message


def test_openai_mode_selected_when_endpoint_unset(monkeypatch):
    _clear_all(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-plain-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")

    settings = config.get_settings()

    assert settings.azure_openai_endpoint is None
    assert settings.openai_api_key.get_secret_value() == "sk-plain-key"
    assert settings.openai_model == "gpt-4o"


def test_openai_mode_missing_vars_raises_with_names(monkeypatch):
    _clear_all(monkeypatch)

    with pytest.raises(RuntimeError) as exc_info:
        config.get_settings()

    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert "OPENAI_MODEL" in message

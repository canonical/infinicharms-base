# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import pytest

from infinicharms.llm import LLMClient, LLMError, LLMResult


def test_requires_token():
    """A missing token raises."""
    with pytest.raises(LLMError):
        LLMClient("")


def test_configures_model_with_base_url():
    """The client wires the OpenAI-compatible model with base_url/api_key/model."""
    client = LLMClient("tok", model="some/model", base_url="https://example/v1")
    provider = client._model._provider
    assert client._model.model_name == "some/model"
    assert str(provider.base_url).rstrip("/") == "https://example/v1"
    assert provider.client.api_key == "tok"


def _fake_agent(monkeypatch, output):
    """Patch LLMClient._agent so run_sync returns (or raises) a canned output."""

    class _FakeResult:
        def __init__(self, out):
            self.output = out

    class _FakeAgent:
        def run_sync(self, user_prompt):
            if isinstance(output, Exception):
                raise output
            return _FakeResult(output)

    monkeypatch.setattr(LLMClient, "_agent", lambda self, prompt, out_type: _FakeAgent())


def test_chat_returns_text(monkeypatch):
    """Chat returns the model's text output."""
    _fake_agent(monkeypatch, "hello there")
    client = LLMClient("tok")
    assert client.chat("sys", "user") == "hello there"


def test_chat_empty_content_raises(monkeypatch):
    """Empty content from the model raises LLMError."""
    _fake_agent(monkeypatch, "")
    client = LLMClient("tok")
    with pytest.raises(LLMError):
        client.chat("sys", "user")


def test_chat_maps_errors(monkeypatch):
    """Any request error is mapped to LLMError."""
    _fake_agent(monkeypatch, RuntimeError("network down"))
    client = LLMClient("tok")
    with pytest.raises(LLMError):
        client.chat("sys", "user")


def test_summarize_failure_returns_structured(monkeypatch):
    """summarize_failure returns the structured LLMResult from the model."""
    _fake_agent(monkeypatch, LLMResult(title="T", body="B", severity="high"))
    client = LLMClient("tok")
    result = client.summarize_failure("sys", "user")
    assert result.title == "T"
    assert result.body == "B"
    assert result.severity == "high"


def test_summarize_failure_fills_defaults(monkeypatch):
    """Empty fields fall back to safe defaults."""
    _fake_agent(monkeypatch, LLMResult(title="", body="", severity=""))
    client = LLMClient("tok")
    result = client.summarize_failure("sys", "user")
    assert result.title == "Charm hook failure"
    assert result.severity == "unknown"
    assert result.classification == "unknown"


def test_summarize_failure_parses_classification(monkeypatch):
    """A valid classification from the model is normalized and returned."""
    _fake_agent(
        monkeypatch,
        LLMResult(title="T", body="B", severity="low", classification="NOT-IMPLEMENTED"),
    )
    client = LLMClient("tok")
    result = client.summarize_failure("sys", "user")
    assert result.classification == "not-implemented"


def test_summarize_failure_normalizes_bad_classification(monkeypatch):
    """An out-of-contract classification degrades to ``unknown``."""
    _fake_agent(
        monkeypatch,
        LLMResult(title="T", body="B", severity="low", classification="maybe"),
    )
    client = LLMClient("tok")
    result = client.summarize_failure("sys", "user")
    assert result.classification == "unknown"


def test_summarize_failure_maps_errors(monkeypatch):
    """A request error is mapped to LLMError so callers can degrade."""
    _fake_agent(monkeypatch, RuntimeError("boom"))
    client = LLMClient("tok")
    with pytest.raises(LLMError):
        client.summarize_failure("sys", "user")

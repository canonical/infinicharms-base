# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import pytest

from infinitycharms import llm
from infinitycharms.llm import LLMClient, LLMError


def test_requires_token():
    """A missing token raises."""
    with pytest.raises(LLMError):
        LLMClient("")


def test_extract_json_plain():
    """Plain JSON extracts."""
    parsed = llm._extract_json('{"title": "t", "severity": "low"}')
    assert parsed == {"title": "t", "severity": "low"}


def test_extract_json_fenced():
    """JSON wrapped in a code fence extracts."""
    text = '```json\n{"title": "t", "body": "b"}\n```'
    parsed = llm._extract_json(text)
    assert parsed is not None
    assert parsed["title"] == "t"


def test_extract_json_none():
    """Non-JSON returns None."""
    assert llm._extract_json("no json here") is None


def test_summarize_failure_parses(monkeypatch):
    """summarize_failure parses a JSON reply into an LLMResult."""
    client = LLMClient("tok")
    monkeypatch.setattr(
        client, "chat", lambda s, u: '{"title": "T", "body": "B", "severity": "high"}'
    )
    result = client.summarize_failure("sys", "user")
    assert result.title == "T"
    assert result.severity == "high"


def test_summarize_failure_fallback(monkeypatch):
    """Non-JSON reply falls back to raw text as body."""
    client = LLMClient("tok")
    monkeypatch.setattr(client, "chat", lambda s, u: "just prose")
    result = client.summarize_failure("sys", "user")
    assert result.body == "just prose"


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeCompletion(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeOpenAI:
    last_kwargs: dict | None = None
    reply: str | None = '{"title": "T", "body": "B", "severity": "low"}'

    def __init__(self, **kwargs):
        _FakeOpenAI.last_kwargs = kwargs
        self.chat = _FakeChat(_FakeOpenAI.reply)


def _install_fake_openai(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)


def test_uses_openai_sdk_with_base_url(monkeypatch):
    """The client configures the SDK with base_url/api_key/model."""
    _install_fake_openai(monkeypatch)
    client = LLMClient("tok", model="some/model", base_url="https://example/v1")
    text = client.chat("sys", "user")
    assert text == _FakeOpenAI.reply
    # SDK constructed with our base_url + token.
    kwargs = _FakeOpenAI.last_kwargs
    assert kwargs is not None
    assert kwargs["base_url"] == "https://example/v1"
    assert kwargs["api_key"] == "tok"


def test_chat_maps_sdk_errors(monkeypatch):
    """Any SDK error is mapped to LLMError."""

    class _Boom(_FakeOpenAI):
        def __init__(self, **kwargs):
            self.chat = _FakeChat(None)

            def _raise(**_):
                raise RuntimeError("network down")

            self.chat.completions.create = _raise

    import openai

    monkeypatch.setattr(openai, "OpenAI", _Boom)
    client = LLMClient("tok")
    with pytest.raises(LLMError):
        client.chat("sys", "user")


def test_chat_empty_content_raises(monkeypatch):
    """Empty content from the model raises LLMError."""
    _install_fake_openai(monkeypatch)
    monkeypatch.setattr(_FakeOpenAI, "reply", None)
    client = LLMClient("tok")
    with pytest.raises(LLMError):
        client.chat("sys", "user")
    monkeypatch.setattr(_FakeOpenAI, "reply", '{"title": "T"}')

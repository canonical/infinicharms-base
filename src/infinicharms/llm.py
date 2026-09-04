# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""A small chat client built on Pydantic AI.

The failure agent uses this to turn a diagnostics bundle into a concise
root-cause summary and a well-formed issue title/body.

We use `Pydantic AI <https://pydantic.dev/docs/ai/overview/>`_ with its
``OpenAIChatModel`` pointed at an OpenAI-compatible ``base_url`` (OpenRouter by
default). Because the model speaks the OpenAI chat-completions API, swapping in a
different provider/gateway or model is just a matter of changing ``base_url`` and
``model`` -- leaving room for other models and providers without rewriting this
module.

If the token is missing or the request fails, callers fall back to a templated
issue (graceful degradation). See PLAN.md §2.6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default provider/gateway. Any OpenAI-compatible endpoint works here.
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_TIMEOUT = 30


class LLMError(Exception):
    """Raised when the LLM call cannot be completed."""


@dataclass
class LLMResult:
    """Structured result of a failure summarization.

    Attributes:
        classification: The LLM's own judgment of ``not-implemented`` vs
            ``error`` (or ``unknown`` if it declined/failed to decide). The
            LLM sees a pre-computed heuristic guess in the prompt but is
            explicitly allowed to override it -- e.g. an unhandled relation
            event surfacing as a plain ``AttributeError`` is still a missing
            feature, even though no ``NotImplementedFeature`` was raised.
    """

    title: str
    body: str
    severity: str = "unknown"
    classification: str = "unknown"
    raw: str = ""


class LLMClient:
    """Chat client over any OpenAI-compatible endpoint (OpenRouter by default).

    Built on Pydantic AI: it wraps an ``OpenAIChatModel`` pointed at a
    configurable ``base_url``, so it works with OpenAI, OpenRouter, or a
    self-hosted gateway that speaks the OpenAI chat-completions API.
    """

    def __init__(
        self,
        token: str,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not token:
            raise LLMError("LLM API token is required")
        self._timeout = timeout
        try:
            # Imported lazily so importing this module never fails if the SDK is
            # unavailable; callers degrade gracefully to a templated issue.
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.profiles.openai import OpenAIModelProfile
            from pydantic_ai.providers.openai import OpenAIProvider
        except ImportError as exc:  # pragma: no cover - exercised via degradation
            raise LLMError(f"pydantic-ai not available: {exc}") from exc

        self._model = OpenAIChatModel(
            model or DEFAULT_MODEL,
            provider=OpenAIProvider(base_url=base_url, api_key=token),
            # OpenRouter (and some gateways) only accept the older ``max_tokens``
            # field, not ``max_completion_tokens``; keep us provider-agnostic.
            profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
        )

    def _agent(self, system_prompt: str, output_type: type):
        """Build a single-shot agent for the given output type."""
        try:
            from pydantic_ai import Agent
            from pydantic_ai.settings import ModelSettings
        except ImportError as exc:  # pragma: no cover - exercised via degradation
            raise LLMError(f"pydantic-ai not available: {exc}") from exc
        return Agent(
            self._model,
            instructions=system_prompt,
            output_type=output_type,
            model_settings=ModelSettings(timeout=self._timeout),
        )

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Run a single chat completion and return the assistant text."""
        agent = self._agent(system_prompt, str)
        try:
            result = agent.run_sync(user_prompt)
        except Exception as exc:  # noqa: BLE001 - map any SDK error to LLMError
            raise LLMError(f"LLM request failed: {exc}") from exc
        content = result.output
        if not content:
            raise LLMError("LLM returned empty content")
        return content

    def summarize_failure(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Ask the model to summarize a failure into a structured result.

        Pydantic AI validates the model's response against the ``LLMResult``
        schema, so we get ``title``, ``body`` and ``severity`` back directly. If
        the request fails, the error propagates as ``LLMError`` and callers fall
        back to a templated issue.
        """
        agent = self._agent(system_prompt, LLMResult)
        try:
            result = agent.run_sync(user_prompt)
        except Exception as exc:  # noqa: BLE001 - map any SDK error to LLMError
            raise LLMError(f"LLM request failed: {exc}") from exc
        parsed = result.output
        classification = (parsed.classification or "unknown").strip().lower()
        if classification not in ("not-implemented", "error"):
            classification = "unknown"
        return LLMResult(
            title=(parsed.title or "Charm hook failure").strip(),
            body=(parsed.body or "").strip(),
            severity=(parsed.severity or "unknown").strip(),
            classification=classification,
            raw=str(parsed),
        )

# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""A small chat-completion client built on the OpenAI SDK.

The failure agent uses this to turn a diagnostics bundle into a concise
root-cause summary and a well-formed issue title/body.

We use the official ``openai`` SDK pointed at an OpenAI-compatible ``base_url``
(OpenRouter by default). Because the SDK speaks the OpenAI API, swapping in a
different provider/gateway or model is just a matter of changing ``base_url`` and
``model`` -- leaving room for other models and providers without rewriting this
module.

If the token is missing or the request fails, callers fall back to a templated
issue (graceful degradation). See PLAN.md §2.6.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default provider/gateway. Any OpenAI-compatible endpoint works here.
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_TIMEOUT = 30

# Optional attribution headers recommended by OpenRouter (ignored by others).
_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/infinitycharms",
    "X-Title": "InfinityCharms failure agent",
}


class LLMError(Exception):
    """Raised when the LLM call cannot be completed."""


@dataclass
class LLMResult:
    """Structured result of a failure summarization."""

    title: str
    body: str
    severity: str = "unknown"
    raw: str = ""


class LLMClient:
    """Chat client over any OpenAI-compatible endpoint (OpenRouter by default).

    The client is provider-agnostic thanks to the configurable ``base_url``:
    point it at OpenAI, OpenRouter, or a self-hosted gateway that speaks the
    OpenAI chat-completions API.
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
        self._model = model or DEFAULT_MODEL
        try:
            # Imported lazily so importing this module never fails if the SDK is
            # unavailable; callers degrade gracefully to a templated issue.
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised via degradation
            raise LLMError(f"openai SDK not available: {exc}") from exc
        self._client = OpenAI(
            base_url=base_url,
            api_key=token,
            timeout=timeout,
            default_headers=_DEFAULT_HEADERS,
        )

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """Run a single chat completion and return the assistant text."""
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - map any SDK error to LLMError
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {exc}") from exc
        if content is None:
            raise LLMError("LLM returned empty content")
        return content

    def summarize_failure(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Ask the model to summarize a failure, expecting a JSON object back.

        The prompt instructs the model to reply with a JSON object containing
        ``title``, ``body`` and ``severity``. We parse defensively and fall back
        to using the raw text as the body if parsing fails.
        """
        text = self.chat(system_prompt, user_prompt)
        parsed = _extract_json(text)
        if parsed is None:
            return LLMResult(
                title="Charm hook failure",
                body=text.strip(),
                severity="unknown",
                raw=text,
            )
        return LLMResult(
            title=str(parsed.get("title") or "Charm hook failure"),
            body=str(parsed.get("body") or text).strip(),
            severity=str(parsed.get("severity") or "unknown"),
            raw=text,
        )


def _extract_json(text: str) -> dict[str, object] | None:
    """Extract the first JSON object from ``text``, tolerating code fences."""
    stripped = text.strip()
    # Strip a leading/trailing markdown code fence if present.
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        result = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None

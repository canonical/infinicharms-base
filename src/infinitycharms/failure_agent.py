# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""The failure agent: collect, summarize, and file/update a GitHub issue.

Triggered by the ``ops.main()`` wrapper on any uncaught exception from any hook
or action handler (see PLAN.md §2.1). The agent is strictly best-effort and is
itself wrapped in try/except so it can never mask the original failure.

Flow (PLAN.md §6.1):

1. Collect diagnostics.
2. Classify (``not-implemented`` vs ``error``).
3. Summarize with the LLM, or fall back to a templated body.
4. De-duplicate by fingerprint: comment on an existing open issue, else create a
   new one with ``charm:<name>``, ``type:*`` and ``severity:*`` labels.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from . import diagnostics, monitor, state
from .exceptions import NotImplementedFeature
from .github_client import GitHubClient, GitHubError
from .llm import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMClient, LLMError, LLMResult

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration the agent needs to do its job.

    All values come from charm config; missing values degrade gracefully.
    """

    monorepo: str | None = None
    charm_name: str | None = None
    github_token: str | None = None
    llm_api_token: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None


def _prompts_dir() -> Path:
    return state.charm_dir() / "prompts"


def _soul_path() -> Path:
    return state.charm_dir() / "SOUL.md"


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except OSError:
        return default


def classify(exc: BaseException | None) -> str:
    """Return ``not-implemented`` for NotImplementedFeature, else ``error``."""
    if isinstance(exc, NotImplementedFeature):
        return "not-implemented"
    return "error"


def _build_user_prompt(diag: diagnostics.Diagnostics, classification: str) -> str:
    template = _read_text(_prompts_dir() / "failure_summary.md")
    recent = json.dumps(monitor.recent(limit=10), indent=2)
    prefix = ""
    if classification == "not-implemented":
        prefix = _read_text(_prompts_dir() / "not_implemented.md") + "\n\n"
    if not template:
        # Minimal inline fallback if the prompt file is missing.
        template = (
            "Charm {charm_name} hook {hook} failed ({classification}).\n"
            "Exception {exception_type}: {exception_message}\n\n{traceback}"
        )
    body = template.format(
        charm_name=diag.charm_name or "unknown",
        hook=diag.hook or "unknown",
        classification=classification,
        substrate=diag.substrate or "unknown",
        applied_tag=diag.applied_tag or "none",
        exception_type=diag.exception_type,
        exception_message=diag.exception_message,
        traceback=diag.traceback,
        recent_hooks=recent,
    )
    return prefix + body


def _fallback_body(diag: diagnostics.Diagnostics, classification: str) -> str:
    template = _read_text(_prompts_dir() / "issue_body.md.tmpl")
    recent = json.dumps(monitor.recent(limit=10), indent=2)
    if not template:
        return (
            f"Automated failure report for {diag.charm_name}.\n\n"
            f"{diag.exception_type}: {diag.exception_message}\n\n"
            f"```\n{diag.traceback}\n```"
        )
    return template.format(
        classification=classification,
        charm_name=diag.charm_name or "unknown",
        hook=diag.hook or "unknown",
        substrate=diag.substrate or "unknown",
        applied_tag=diag.applied_tag or "none",
        timestamp=diag.timestamp,
        exception_type=diag.exception_type,
        exception_message=diag.exception_message,
        traceback=diag.traceback,
        recent_hooks=recent,
    )


def _summarize(
    config: AgentConfig, diag: diagnostics.Diagnostics, classification: str
) -> LLMResult:
    """Summarize the failure via the LLM, falling back to a template."""
    fallback_title = f"{diag.charm_name or 'charm'}: {diag.hook or 'hook'} failed"
    if not config.llm_api_token:
        logger.info("No LLM API token; using fallback issue template")
        return LLMResult(
            title=fallback_title,
            body=_fallback_body(diag, classification),
            severity="unknown",
        )
    try:
        client = LLMClient(
            config.llm_api_token,
            config.llm_model or DEFAULT_MODEL,
            base_url=config.llm_base_url or DEFAULT_BASE_URL,
        )
        system_prompt = _read_text(_soul_path(), default="You summarize charm failures.")
        user_prompt = _build_user_prompt(diag, classification)
        return client.summarize_failure(system_prompt, user_prompt)
    except LLMError as exc:
        logger.warning("LLM summarization failed (%s); using fallback template", exc)
        return LLMResult(
            title=fallback_title,
            body=_fallback_body(diag, classification),
            severity="unknown",
        )


def _labels(charm_name: str | None, classification: str, severity: str) -> list[str]:
    labels = [f"type:{classification}"]
    if charm_name:
        labels.append(f"charm:{charm_name}")
    if severity and severity != "unknown":
        labels.append(f"severity:{severity}")
    return labels


def _file_or_update_issue(
    config: AgentConfig,
    diag: diagnostics.Diagnostics,
    result: LLMResult,
    classification: str,
    st: state.State,
) -> None:
    """Create a new issue or comment on the existing open one (de-dup)."""
    if not (config.monorepo and config.github_token):
        logger.warning("Missing monorepo/github-token; cannot file issue")
        return
    client = GitHubClient(config.monorepo, config.github_token)
    fingerprint = diag.fingerprint
    existing = st.issue_for(fingerprint)

    if existing is not None and client.issue_is_open(existing):
        comment = (
            f"Recurred at `{diag.timestamp}` on hook `{diag.hook}`.\n\n"
            f"- exception: `{diag.exception_type}: {diag.exception_message}`"
        )
        client.comment_issue(existing, comment)
        logger.info("Commented on existing issue #%s for fingerprint %s", existing, fingerprint)
        return

    labels = _labels(config.charm_name, classification, result.severity)
    number = client.create_issue(result.title, result.body, labels=labels)
    st.record_issue(fingerprint, number)
    st.save()
    logger.info("Filed issue #%s for fingerprint %s", number, fingerprint)


def run(config: AgentConfig, exc_info: tuple | None) -> None:
    """Run the failure agent. Best-effort; never raises.

    Args:
        config: The agent configuration derived from charm config.
        exc_info: ``sys.exc_info()`` from the failing dispatch.
    """
    try:
        exc_type: type[BaseException] | None = None
        exc: BaseException | None = None
        tb: TracebackType | None = None
        if exc_info:
            exc_type, exc, tb = exc_info

        st = state.State.load()
        diag = diagnostics.collect(
            exc_type,
            exc,
            tb,
            charm_name=config.charm_name,
            applied_tag=st.applied_tag,
        )
        classification = classify(exc)

        # Record the most recent failure snapshot for diagnostics/context.
        st.last_failure = {
            "timestamp": diag.timestamp,
            "hook": diag.hook,
            "classification": classification,
            "exception_type": diag.exception_type,
            "exception_message": diag.exception_message,
            "fingerprint": diag.fingerprint,
        }
        st.save()
        monitor.record("failed", status=diag.exception_type, hook=diag.hook)

        result = _summarize(config, diag, classification)
        _file_or_update_issue(config, diag, result, classification, st)
    except GitHubError as exc:
        logger.warning("Failure agent could not file issue: %s", exc)
    except Exception:  # noqa: BLE001 - agent must never mask the original error
        logger.exception("Failure agent crashed (suppressed)")

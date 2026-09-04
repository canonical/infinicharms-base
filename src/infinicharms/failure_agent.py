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
from datetime import datetime, timezone
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
    """Heuristic (type-based) failure classification hint.

    This is only a *prior* handed to the LLM, not the final verdict. An explicit
    ``NotImplementedFeature`` is a strong ``not-implemented`` signal; anything
    else defaults to ``error``. The LLM is free to override this -- e.g. a
    downstream charm that forgot to observe/handle an event will surface a plain
    ``AttributeError``/``KeyError`` that is really a missing-feature gap, not a
    runtime bug. See ``run()`` for how the LLM's judgment wins.
    """
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
    """Summarize the failure via the LLM, falling back to a template.

    ``classification`` is the *heuristic* hint; the returned ``LLMResult`` may
    carry the LLM's own ``classification`` which the caller prefers when set.
    On the fallback (no token / LLM failure) path we echo the heuristic hint so
    labeling never loses the type-based signal.
    """
    fallback_title = f"{diag.charm_name or 'charm'}: {diag.hook or 'hook'} failed"
    if not config.llm_api_token:
        logger.info("No LLM API token; using fallback issue template")
        return LLMResult(
            title=fallback_title,
            body=_fallback_body(diag, classification),
            severity="unknown",
            classification=classification,
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
            classification=classification,
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
) -> dict[str, object]:
    """Create a new issue or comment on the existing open one (de-dup).

    Returns a small outcome dict (``outcome`` is one of ``filed``, ``commented``
    or ``skipped``) so the caller can persist how the agent actually did. A
    ``GitHubError`` propagates to the caller, which records an ``outcome=failed``.
    """
    if not (config.monorepo and config.github_token):
        logger.warning("Missing monorepo/github-token; cannot file issue")
        return {"outcome": "skipped", "reason": "missing monorepo/github-token"}
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
        return {"outcome": "commented", "issue": existing}

    labels = _labels(config.charm_name, classification, result.severity)
    number = client.create_issue(result.title, result.body, labels=labels)
    st.record_issue(fingerprint, number)
    st.save()
    logger.info("Filed issue #%s for fingerprint %s", number, fingerprint)
    return {"outcome": "filed", "issue": number, "labels": labels}


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
        # Heuristic (type-based) hint handed to the LLM as a prior.
        heuristic = classify(exc)

        result = _summarize(config, diag, heuristic)

        # The LLM's own judgment wins when it made one; otherwise fall back to
        # the type-based heuristic. This is what lets an unexpected exception
        # from a downstream charm that forgot to handle an event be classified
        # as ``not-implemented`` even though no ``NotImplementedFeature`` was
        # raised.
        classification = heuristic
        if result.classification in ("not-implemented", "error"):
            classification = result.classification
            if classification != heuristic:
                logger.info("LLM reclassified failure %s -> %s", heuristic, classification)

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

        outcome = _file_or_update_issue(config, diag, result, classification, st)
        _record_agent_run(st, diag, classification, outcome)
    except GitHubError as exc:
        logger.warning("Failure agent could not file issue: %s", exc)
        _safe_record_failed_agent_run("GitHubError", str(exc))
    except Exception as exc:  # noqa: BLE001 - agent must never mask the original error
        logger.exception("Failure agent crashed (suppressed)")
        _safe_record_failed_agent_run(type(exc).__name__, str(exc))


def _record_agent_run(
    st: state.State,
    diag: diagnostics.Diagnostics,
    classification: str,
    outcome: dict[str, object],
) -> None:
    """Persist a snapshot of the agent's own outcome to ``last_agent_run``."""
    st.last_agent_run = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hook": diag.hook,
        "classification": classification,
        "fingerprint": diag.fingerprint,
        **outcome,
    }
    st.save()


def _safe_record_failed_agent_run(error_type: str, error_message: str) -> None:
    """Best-effort record that the agent itself failed. Never raises.

    Used from the exception handlers, where we may not have a fully-built state
    object; we reload from disk so we don't clobber other fields.
    """
    try:
        st = state.State.load()
        st.last_agent_run = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": "failed",
            "error_type": error_type,
            "error_message": error_message,
        }
        st.save()
    except Exception:  # noqa: BLE001 - recording must never mask the original error
        logger.exception("Could not record failed agent run (suppressed)")

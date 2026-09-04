# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Diagnostic collection for the failure agent.

This module gathers a rich, best-effort context bundle about a hook failure so
the agent (LLM or fallback template) has everything it needs to file a useful
issue. It never raises: collection failures degrade to ``None``/empty values.

See PLAN.md §2.6 (collector) and §6.1.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import TracebackType

logger = logging.getLogger(__name__)

# Juju hook-context environment variables worth capturing. We deliberately avoid
# copying the whole environment so tokens passed via env don't leak into issues.
_JUJU_ENV_KEYS = (
    "JUJU_DISPATCH_PATH",
    "JUJU_HOOK_NAME",
    "JUJU_ACTION_NAME",
    "JUJU_UNIT_NAME",
    "JUJU_MODEL_NAME",
    "JUJU_MODEL_UUID",
    "JUJU_MACHINE_ID",
    "JUJU_AVAILABILITY_ZONE",
    "JUJU_RELATION",
    "JUJU_RELATION_ID",
    "JUJU_REMOTE_APP",
    "JUJU_REMOTE_UNIT",
    "JUJU_VERSION",
    "JUJU_CHARM_DIR",
)


@dataclass
class Diagnostics:
    """A best-effort snapshot of a hook failure's context."""

    timestamp: str
    hook: str | None
    exception_type: str
    exception_message: str
    traceback: str
    traceback_head: str
    juju_env: dict[str, str] = field(default_factory=dict)
    charm_name: str | None = None
    applied_tag: str | None = None
    substrate: str | None = None
    python_version: str = field(default_factory=platform.python_version)
    fingerprint: str = ""

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict of the diagnostics."""
        return {
            "timestamp": self.timestamp,
            "hook": self.hook,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "traceback": self.traceback,
            "traceback_head": self.traceback_head,
            "juju_env": self.juju_env,
            "charm_name": self.charm_name,
            "applied_tag": self.applied_tag,
            "substrate": self.substrate,
            "python_version": self.python_version,
            "fingerprint": self.fingerprint,
        }


def _hook_name() -> str | None:
    """Best-effort determination of the failing hook/action name."""
    dispatch = os.environ.get("JUJU_DISPATCH_PATH")
    if dispatch:
        # e.g. "hooks/install" or "actions/force-update"
        return dispatch.rsplit("/", 1)[-1]
    return os.environ.get("JUJU_HOOK_NAME") or os.environ.get("JUJU_ACTION_NAME")


def _collect_juju_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _JUJU_ENV_KEYS if k in os.environ}


def _guess_substrate() -> str | None:
    """Best-effort guess of the substrate from the hook environment.

    Kubernetes charms run without a machine id; machine charms have one. This is
    only a hint for the issue body, never load-bearing.
    """
    if os.environ.get("JUJU_MACHINE_ID"):
        return "machine"
    if os.environ.get("JUJU_UNIT_NAME"):
        return "kubernetes"
    return None


def _fingerprint(charm_name: str | None, hook: str | None, exc_type: str, tb_head: str) -> str:
    """Stable hash of the failure identity, for issue de-duplication."""
    payload = "|".join([charm_name or "", hook or "", exc_type, tb_head]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def collect(
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: TracebackType | None,
    *,
    charm_name: str | None = None,
    applied_tag: str | None = None,
) -> Diagnostics:
    """Collect diagnostics for a failure. Never raises."""
    try:
        type_name = exc_type.__name__ if exc_type else "UnknownError"
        message = str(exc) if exc else ""
        tb_text = "".join(traceback.format_exception(exc_type, exc, tb)) if exc_type else ""
        # The last non-empty traceback frame line is a good identity anchor.
        tb_lines = [line for line in tb_text.strip().splitlines() if line.strip()]
        tb_head = tb_lines[-1] if tb_lines else type_name
        hook = _hook_name()
        return Diagnostics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            hook=hook,
            exception_type=type_name,
            exception_message=message,
            traceback=tb_text,
            traceback_head=tb_head,
            juju_env=_collect_juju_env(),
            charm_name=charm_name,
            applied_tag=applied_tag,
            substrate=_guess_substrate(),
            fingerprint=_fingerprint(charm_name, hook, type_name, tb_head),
        )
    except Exception:  # pragma: no cover - collector must never raise
        logger.exception("Diagnostics collection failed")
        return Diagnostics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            hook=None,
            exception_type="DiagnosticsError",
            exception_message="diagnostics collection failed",
            traceback="",
            traceback_head="DiagnosticsError",
            fingerprint="0000000000000000",
        )

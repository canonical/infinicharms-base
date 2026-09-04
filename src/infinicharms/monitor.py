# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Hook-run monitoring.

Records a small rolling log of hook runs and outcomes to
``$JUJU_CHARM_DIR/.infinicharms/hooks.log`` so the failure agent has recent
context to attach to issues. See PLAN.md §6.2.

The monitor is best-effort: recording must never break a hook.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import state

logger = logging.getLogger(__name__)

HOOKS_LOG_FILENAME = "hooks.log"
# Keep the log small so it can be embedded in an issue body.
MAX_ENTRIES = 50


def hooks_log_path() -> Path:
    """Return the path to the rolling hooks log."""
    return state.state_dir() / HOOKS_LOG_FILENAME


def _current_hook() -> str | None:
    dispatch = os.environ.get("JUJU_DISPATCH_PATH")
    if dispatch:
        return dispatch.rsplit("/", 1)[-1]
    return os.environ.get("JUJU_HOOK_NAME") or os.environ.get("JUJU_ACTION_NAME")


def record(outcome: str, status: str | None = None, hook: str | None = None) -> None:
    """Append a single hook-run entry to the rolling log. Never raises."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hook": hook or _current_hook(),
            "outcome": outcome,
            "status": status,
        }
        path = hooks_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if path.exists():
            lines = path.read_text().splitlines()
        lines.append(json.dumps(entry))
        # Trim to the last MAX_ENTRIES.
        lines = lines[-MAX_ENTRIES:]
        path.write_text("\n".join(lines) + "\n")
    except Exception:  # pragma: no cover - monitoring must never break a hook
        logger.exception("Failed to record hook run")


def recent(limit: int = 10) -> list[dict[str, object]]:
    """Return the most recent hook-run entries (newest last). Never raises."""
    try:
        path = hooks_log_path()
        if not path.exists():
            return []
        entries: list[dict[str, object]] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries[-limit:]
    except Exception:  # pragma: no cover
        logger.exception("Failed to read hook log")
        return []

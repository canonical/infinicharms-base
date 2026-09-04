# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Persistent charm state stored in ``$JUJU_CHARM_DIR/.infinicharms/state.json``.

State survives between hook invocations *within a charm revision* (it lives on
the charm's on-disk directory). It is used to:

* record the currently-applied release tag so the updater is idempotent and
  refuses downgrades (see PLAN.md §2.4 / §5), and
* map failure fingerprints to the GitHub issue number they were filed under, so
  the failure agent can comment on an existing issue instead of opening a
  duplicate (see PLAN.md §6.1).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIRNAME = ".infinicharms"
STATE_FILENAME = "state.json"


def charm_dir() -> Path:
    """Return the charm directory (``$JUJU_CHARM_DIR``), falling back to cwd."""
    return Path(os.environ.get("JUJU_CHARM_DIR", os.getcwd()))


def state_dir() -> Path:
    """Return the ``.infinicharms`` directory under the charm dir."""
    return charm_dir() / STATE_DIRNAME


def state_path() -> Path:
    """Return the full path to ``state.json``."""
    return state_dir() / STATE_FILENAME


@dataclass
class State:
    """The persisted charm state.

    Attributes:
        applied_tag: The release tag currently applied by the updater, if any.
        issues: Mapping of failure fingerprint -> filed GitHub issue number.
        last_failure: A small snapshot of the most recent failure, kept for
            diagnostics/context.
        last_agent_run: A small snapshot of the *failure agent's own* most recent
            outcome (did it file/comment/skip/fail, and why). This is distinct
            from ``last_failure`` (which describes the hook that broke) and is
            what you inspect to answer "how did the agent do?" -- see the
            ``outcome`` field, which is one of ``filed``, ``commented``,
            ``skipped`` or ``failed``.
    """

    applied_tag: str | None = None
    issues: dict[str, int] = field(default_factory=dict)
    last_failure: dict[str, object] | None = None
    last_agent_run: dict[str, object] | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> State:
        """Load state from disk, returning a default instance if missing/corrupt."""
        path = path or state_path()
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file %s: %s; using defaults", path, exc)
            return cls()
        return cls(
            applied_tag=raw.get("applied_tag"),
            issues={str(k): int(v) for k, v in (raw.get("issues") or {}).items()},
            last_failure=raw.get("last_failure"),
            last_agent_run=raw.get("last_agent_run"),
        )

    def save(self, path: Path | None = None) -> None:
        """Persist state to disk atomically."""
        path = path or state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        tmp.replace(path)

    def issue_for(self, fingerprint: str) -> int | None:
        """Return the issue number previously filed for a fingerprint, if any."""
        return self.issues.get(fingerprint)

    def record_issue(self, fingerprint: str, issue_number: int) -> None:
        """Associate a fingerprint with a filed issue number."""
        self.issues[fingerprint] = issue_number

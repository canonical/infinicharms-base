#!/usr/bin/env python3
# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""InfinityCharms base (template) charm.

A workload-less, infrastructure-agnostic, self-healing template charm. It bakes
in three capabilities (see PLAN.md):

1. Failure agent -- on any hook failure, collect diagnostics, summarize with an
   LLM, and file/update a GitHub issue on the monorepo.
2. Hot-patch / self-update (Option A) -- fetch and apply the latest matching
   release for this charm.
3. Hook monitoring -- record every hook run and status for context.

The failure agent is wired in the ``main()`` entrypoint by wrapping
``ops.main()`` in try/except, because ``ops`` emits no event when a hook fails
(PLAN.md §2.1).
"""

import json
import logging
import subprocess
import sys

import ops

from infinitycharms import failure_agent, monitor, updater
from infinitycharms.exceptions import NotImplementedFeature

logger = logging.getLogger(__name__)


class InfinityCharmsBaseCharm(ops.CharmBase):
    """The base template charm."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        # Lifecycle hooks.
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        # Status collection (fires only after a successful hook).
        framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

    # -- helpers -----------------------------------------------------------

    def _cfg(self, key: str) -> str | None:
        value = self.config.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _maybe_update(self) -> None:
        """Run the Option A self-update if enabled and configured."""
        if not bool(self.config.get("auto-update", True)):
            return
        monorepo = self._cfg("monorepo")
        charm_name = self._cfg("charm-name")
        if not (monorepo and charm_name):
            logger.debug("Skipping auto-update: monorepo/charm-name not set")
            return
        up = updater.Updater(monorepo, charm_name, github_token=self._cfg("github-token"))
        try:
            result = up.apply()
            if result.get("updated"):
                logger.info("Self-update applied: %s", result.get("applied_tag"))
        except updater.UpdateError as exc:
            logger.warning("Self-update failed: %s", exc)

    # -- lifecycle handlers ------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Bootstrap on install: record the run and set a waiting status."""
        monitor.record("started", hook="install")
        self.unit.status = ops.MaintenanceStatus("bootstrapping base charm")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start."""
        monitor.record("started", hook="start")

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Apply self-update on config change and revalidate config."""
        monitor.record("started", hook="config-changed")
        self._maybe_update()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic reconcile: run the self-update loop."""
        monitor.record("started", hook="update-status")
        self._maybe_update()

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent) -> None:
        """Handle a Juju-driven upgrade (durable path, complements Option A)."""
        monitor.record("started", hook="upgrade-charm")

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent) -> None:
        """Report unit status after a successful hook.

        This runs only after a hook succeeds, so it is a good place to record a
        healthy run and surface configuration gaps as blocked/active status.
        """
        monitor.record("succeeded", status="collect-unit-status")
        missing = [k for k in ("monorepo", "charm-name") if not self._cfg(k)]
        if missing:
            event.add_status(ops.BlockedStatus(f"missing config: {', '.join(missing)}"))
            return
        event.add_status(ops.ActiveStatus("ready"))


def _config_get(key: str) -> str | None:
    """Read a single config value via the ``config-get`` hook tool.

    When ``ops.main`` raises we may not have a live charm object, so the failure
    agent wrapper reads config directly via the Juju hook command.
    """
    try:
        out = subprocess.run(  # noqa: S603
            ["config-get", "--format=json", key],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    try:
        value = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _agent_config_from_env() -> failure_agent.AgentConfig:
    """Build agent config outside a charm instance, using ``config-get``."""
    return failure_agent.AgentConfig(
        monorepo=_config_get("monorepo"),
        charm_name=_config_get("charm-name"),
        github_token=_config_get("github-token"),
        llm_api_token=_config_get("llm-api-token"),
        llm_model=_config_get("llm-model"),
        llm_base_url=_config_get("llm-base-url"),
    )


def main() -> None:
    """Entrypoint that wraps ``ops.main`` to catch any hook/action failure.

    ``ops`` emits no event on hook failure, so the only reliable central catch
    point is around ``ops.main``. On any uncaught exception we run the failure
    agent (best-effort) and then re-raise so Juju still sees the ``error`` state.
    See PLAN.md §2.1.
    """
    try:
        ops.main(InfinityCharmsBaseCharm)
    except Exception:  # noqa: BLE001 - deliberately catch-all, then re-raise
        try:
            failure_agent.run(_agent_config_from_env(), sys.exc_info())
        except Exception:  # noqa: BLE001 - the agent must never mask the error
            logger.exception("Failure agent wrapper crashed (suppressed)")
        raise


# Re-export so scaffolded charms can `raise NotImplementedFeature(...)`.
__all__ = ["InfinityCharmsBaseCharm", "NotImplementedFeature", "main"]


if __name__ == "__main__":  # pragma: nocover
    main()

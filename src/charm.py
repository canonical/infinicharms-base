#!/usr/bin/env python3
# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""InfiniCharms base (template) charm.

A workload-less, infrastructure-agnostic, self-healing template charm. It bakes
in three capabilities (see PLAN.md):

1. Failure agent -- on any hook failure, collect diagnostics, summarize with an
   LLM, and file/update a GitHub issue on the monorepo.
2. Hot-patch / self-update (Option A) -- fetch the latest matching release for
   this charm into ``.infinicharms/evolved`` and run *that* code instead.
3. Hook monitoring -- record every hook run and status for context.

``main()`` always attempts the self-update first (item 2), then dynamically
resolves which ``CharmBase`` to actually dispatch through: the evolved
checkout if one has been fetched, or this file's own baked-in
``InfiniCharmsBaseCharm`` otherwise (see ``infinicharms.shim``). Whichever
class wins is wrapped so any exception escaping an observed hook/action
handler is reported to the failure agent as it happens -- because ``ops``
emits no event when a hook fails, this per-hook guard (plus an outer
try/except as a safety net) is how failures reach GitHub issues (PLAN.md §2.1).
"""

import json
import logging
import subprocess
import sys

import ops

from infinicharms import failure_agent, monitor, shim, updater
from infinicharms.exceptions import NotImplementedFeature

logger = logging.getLogger(__name__)


class InfiniCharmsBaseCharm(ops.CharmBase):
    """The base template charm (baked-in fallback behaviour).

    This is the seed every InfiniCharms charm is scaffolded from, and the class
    ``main()`` falls back to running directly only until (or unless) a release
    has been fetched into ``.infinicharms/evolved`` by the self-updater -- see
    ``infinicharms.shim``. Once a release exists, ``main()`` dynamically loads
    and runs *that* checkout's ``CharmBase`` subclass instead, which is
    expected to be this same file, edited by the downstream charm's author.
    """

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
        # Introspection action for debugging the failure agent itself.
        framework.observe(self.on["agent-status"].action, self._on_agent_status_action)

    # -- helpers -----------------------------------------------------------

    def _cfg(self, key: str) -> str | None:
        value = self.config.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    # -- lifecycle handlers ------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Bootstrap on install: record the run and set a waiting status."""
        monitor.record("started", hook="install")
        self.unit.status = ops.MaintenanceStatus("bootstrapping base charm")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start."""
        monitor.record("started", hook="start")

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Revalidate config on config change.

        The self-update loop no longer runs from here: ``main()`` always runs
        it once per dispatch, before any hook or action fires -- see
        ``_maybe_self_update()``.
        """
        monitor.record("started", hook="config-changed")

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic reconcile."""
        monitor.record("started", hook="update-status")

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent) -> None:
        """Handle a Juju-driven upgrade (durable path, complements Option A)."""
        monitor.record("started", hook="upgrade-charm")

    def _on_agent_status_action(self, event: ops.ActionEvent) -> None:
        """Report the failure agent's most recent outcome (read-only).

        Surfaces ``.infinicharms/state.json`` so operators can see how the agent
        did (filed/commented/skipped/failed and why) without ``juju ssh``.
        """
        from infinicharms import state

        st = state.State.load()
        last_agent_run = st.last_agent_run or {}
        event.set_results(
            {
                "last-agent-run": json.dumps(last_agent_run, sort_keys=True),
                "last-failure": json.dumps(st.last_failure or {}, sort_keys=True),
                "outcome": str(last_agent_run.get("outcome", "none")),
                "filed-issues": json.dumps(st.issues, sort_keys=True),
            }
        )

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

    Runs before any charm object exists (from ``main()``), so the self-update
    and failure-agent config must be read directly via the Juju hook command
    rather than through ``self.config``.
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


def _config_get_bool(key: str, default: bool) -> bool:
    """Read a boolean config value via ``config-get``, degrading to ``default``."""
    try:
        out = subprocess.run(  # noqa: S603
            ["config-get", "--format=json", key],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return default
    try:
        value = json.loads(out.stdout)
    except json.JSONDecodeError:
        return default
    if value is None:
        return default
    return bool(value)


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


def _maybe_self_update() -> None:
    """Run the Option A self-update, best-effort; never fails the dispatch.

    Called unconditionally from ``main()`` on *every* dispatch, before the
    charm class to run is even resolved -- so a release fetched during this
    very dispatch is what gets loaded and run by ``infinicharms.shim`` just
    below, with no need to wait for a subsequent hook.

    Every outcome (applied/skipped/up-to-date/failed) is recorded via
    ``monitor.record`` under the synthetic hook name ``"self-update"``, in
    addition to being logged -- so it shows up in the rolling hooks log that
    gets embedded in failure-agent issue bodies, giving useful context even
    when the *failure* being reported is unrelated to the updater itself.
    """
    if not _config_get_bool("auto-update", True):
        logger.info("Self-update disabled (auto-update=false); skipping")
        monitor.record("skipped", hook="self-update", status="auto-update disabled")
        return
    monorepo = _config_get("monorepo")
    charm_name = _config_get("charm-name")
    if not (monorepo and charm_name):
        logger.info(
            "Skipping self-update: monorepo/charm-name not set (monorepo=%r, charm-name=%r)",
            monorepo,
            charm_name,
        )
        monitor.record("skipped", hook="self-update", status="monorepo/charm-name not set")
        return
    logger.info("Checking for a self-update: monorepo=%s charm-name=%s", monorepo, charm_name)
    up = updater.Updater(monorepo, charm_name, github_token=_config_get("github-token"))
    try:
        result = up.apply()
    except updater.UpdateError as exc:
        logger.warning("Self-update failed: %s", exc)
        monitor.record("failed", hook="self-update", status=str(exc))
        return
    if result.get("updated"):
        logger.info("Self-update applied: %s", result.get("applied_tag"))
        monitor.record("updated", hook="self-update", status=str(result.get("applied_tag")))
    else:
        reason = result.get("reason", "unknown")
        logger.info("Self-update made no change: %s (%s)", reason, result)
        monitor.record("skipped", hook="self-update", status=str(reason))


def _report_hook_failure(exc_info: tuple) -> None:
    """Report a single hook/action failure to the failure agent immediately.

    This is the callback ``infinicharms.shim`` invokes from *inside* the
    per-hook guard, as soon as a handler raises -- giving the most precise
    attribution available. Marks the exception as already-reported so
    ``main()``'s outer safety-net ``except`` (below) never files/comments a
    second time for the same failure.
    """
    exc = exc_info[1] if exc_info else None
    logger.info(
        "Reporting guarded hook failure to the failure agent: %s: %s",
        type(exc).__name__ if exc else "UnknownError",
        exc,
    )
    try:
        failure_agent.run(_agent_config_from_env(), exc_info)
    except Exception:  # noqa: BLE001 - the agent must never mask the error
        logger.exception("Failure agent wrapper crashed (suppressed)")
    else:
        logger.info("Failure agent finished handling the guarded hook failure")
    finally:
        shim.mark_reported(exc)


def main() -> None:
    """Entrypoint: self-update, then dispatch through the resolved charm class.

    Every dispatch first runs the Option A self-updater (best-effort), then
    resolves which ``CharmBase`` to actually run via ``infinicharms.shim`` --
    the evolved checkout fetched into ``.infinicharms/evolved``, or this file's
    own baked-in behaviour if none exists yet -- wrapped so any exception
    escaping an observed hook/action handler is reported to the failure agent
    as it happens (PLAN.md §2.1/§2.4). The outer try/except below is only a
    safety net for failures outside any observed handler (e.g. a broken
    checkout, or the evolved class itself misbehaving during construction); it
    never reports a failure the per-hook guard already reported.
    """
    _maybe_self_update()
    charm_cls = shim.resolve_charm_class(InfiniCharmsBaseCharm, _report_hook_failure)
    logger.info("Dispatching through %s", charm_cls.__qualname__)
    try:
        ops.main(charm_cls)
    except Exception:  # noqa: BLE001 - deliberately catch-all, then re-raise
        exc_info = sys.exc_info()
        if shim.already_reported(exc_info[1]):
            logger.info("Failure already reported by the per-hook guard; not reporting again")
        else:
            logger.info(
                "Unguarded failure escaped ops.main(); reporting via the outer safety net: %s: %s",
                type(exc_info[1]).__name__ if exc_info[1] else "UnknownError",
                exc_info[1],
            )
            try:
                failure_agent.run(_agent_config_from_env(), exc_info)
            except Exception:  # noqa: BLE001 - the agent must never mask the error
                logger.exception("Failure agent wrapper crashed (suppressed)")
        raise


# Re-export so scaffolded charms can `raise NotImplementedFeature(...)`.
__all__ = ["InfiniCharmsBaseCharm", "NotImplementedFeature", "main"]


if __name__ == "__main__":  # pragma: nocover
    main()

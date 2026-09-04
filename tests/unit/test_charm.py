# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.
#
# To learn more about testing, see
# https://canonical.com/juju/docs/ops/latest/explanation/testing/

import ops
from ops import testing

from charm import InfiniCharmsBaseCharm


def test_blocked_without_config():
    """Without monorepo/charm-name the unit reports blocked."""
    ctx = testing.Context(InfiniCharmsBaseCharm)
    state_out = ctx.run(ctx.on.update_status(), testing.State(config={}))
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "missing config" in state_out.unit_status.message


def test_active_with_config(monkeypatch):
    """With required config and no update available, the unit is active."""
    # Avoid real network in the update loop.
    import infinicharms.updater as updater

    monkeypatch.setattr(updater.Updater, "apply", lambda self, **kw: {"updated": False})
    ctx = testing.Context(InfiniCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo", "auto-update": False}
    state_out = ctx.run(ctx.on.update_status(), testing.State(config=config))
    assert state_out.unit_status == ops.ActiveStatus("ready")


def test_maybe_update_skipped_when_auto_update_false(monkeypatch):
    """auto-update=False must never call Updater.apply()."""
    import infinicharms.updater as updater

    called = {"n": 0}
    monkeypatch.setattr(
        updater.Updater, "apply", lambda self, **kw: called.__setitem__("n", called["n"] + 1)
    )
    ctx = testing.Context(InfiniCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo", "auto-update": False}
    ctx.run(ctx.on.config_changed(), testing.State(config=config))
    assert called["n"] == 0


def test_maybe_update_skipped_without_monorepo_or_charm_name(monkeypatch):
    """Missing monorepo/charm-name must never call Updater.apply()."""
    import infinicharms.updater as updater

    called = {"n": 0}
    monkeypatch.setattr(
        updater.Updater, "apply", lambda self, **kw: called.__setitem__("n", called["n"] + 1)
    )
    ctx = testing.Context(InfiniCharmsBaseCharm)
    # auto-update defaults to True, but monorepo/charm-name are missing.
    ctx.run(ctx.on.config_changed(), testing.State(config={}))
    assert called["n"] == 0


def test_maybe_update_failure_does_not_fail_the_hook(monkeypatch):
    """A self-update UpdateError must be swallowed: the hook itself succeeds.

    This is the other half of the "fail -> patch -> next dispatch succeeds"
    loop: a failed *update attempt* is not the same as a failed *hook*, and
    must not trip the failure-agent / `error` status path.
    """
    import infinicharms.updater as updater

    def raise_update_error(self, **kw):
        raise updater.UpdateError("network unreachable")

    monkeypatch.setattr(updater.Updater, "apply", raise_update_error)
    ctx = testing.Context(InfiniCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo"}
    # Must not raise.
    state_out = ctx.run(ctx.on.config_changed(), testing.State(config=config))
    assert isinstance(state_out.unit_status, ops.ActiveStatus | ops.MaintenanceStatus)


def test_maybe_update_applies_when_release_available(monkeypatch):
    """When Updater.apply() reports an update, the hook still completes normally."""
    import infinicharms.updater as updater

    monkeypatch.setattr(
        updater.Updater,
        "apply",
        lambda self, **kw: {"updated": True, "applied_tag": "boo/v1.2.3"},
    )
    ctx = testing.Context(InfiniCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo"}
    state_out = ctx.run(ctx.on.update_status(), testing.State(config=config))
    assert state_out.unit_status == ops.ActiveStatus("ready")

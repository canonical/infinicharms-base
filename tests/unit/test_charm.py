# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.
#
# To learn more about testing, see
# https://canonical.com/juju/docs/ops/latest/explanation/testing/

import ops
from ops import testing

from charm import InfinityCharmsBaseCharm


def test_blocked_without_config():
    """Without monorepo/charm-name the unit reports blocked."""
    ctx = testing.Context(InfinityCharmsBaseCharm)
    state_out = ctx.run(ctx.on.update_status(), testing.State(config={}))
    assert isinstance(state_out.unit_status, ops.BlockedStatus)
    assert "missing config" in state_out.unit_status.message


def test_active_with_config(monkeypatch):
    """With required config and no update available, the unit is active."""
    # Avoid real network in the update loop.
    import infinitycharms.updater as updater

    monkeypatch.setattr(updater.Updater, "apply", lambda self, **kw: {"updated": False})
    ctx = testing.Context(InfinityCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo", "auto-update": False}
    state_out = ctx.run(ctx.on.update_status(), testing.State(config=config))
    assert state_out.unit_status == ops.ActiveStatus("ready")

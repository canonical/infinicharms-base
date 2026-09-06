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


def test_active_with_config():
    """With required config, the unit is active.

    Hook handlers no longer run the self-updater themselves (that's now
    `main()`'s job, once per dispatch -- see `test_main.py`), so this only
    exercises the baked-in lifecycle/status-collection behaviour.
    """
    ctx = testing.Context(InfiniCharmsBaseCharm)
    config = {"monorepo": "acme/mono", "charm-name": "boo"}
    state_out = ctx.run(ctx.on.update_status(), testing.State(config=config))
    assert state_out.unit_status == ops.ActiveStatus("ready")

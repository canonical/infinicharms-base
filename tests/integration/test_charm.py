# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.
#
# The integration tests use the Jubilant library and the pytest-jubilant plugin.
# See https://canonical.com/juju/docs/ops/latest/howto/write-integration-tests-for-a-charm/
#
# pytest-jubilant provides a module-scoped `juju` fixture that creates a temporary Juju model.
# The `charm` fixture is defined in conftest.py.

import logging
import pathlib

import jubilant
import pytest

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the workload-less base charm.

    Without ``monorepo``/``charm-name`` config the charm reports blocked, so we
    wait for the unit to settle rather than for active.
    """
    juju.deploy(charm, app="infinicharms-base")
    juju.wait(lambda status: jubilant.all_blocked(status) or jubilant.all_active(status))

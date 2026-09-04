# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import sys

from infinicharms import diagnostics
from infinicharms.exceptions import NotImplementedFeature


def _exc_info(exc: BaseException):
    try:
        raise exc
    except BaseException:
        return sys.exc_info()


def test_collect_populates_fields(monkeypatch):
    """Collecting from a raised error populates identity fields + fingerprint."""
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/install")
    monkeypatch.setenv("JUJU_MACHINE_ID", "0")
    exc_type, exc, tb = _exc_info(ValueError("boom"))

    diag = diagnostics.collect(exc_type, exc, tb, charm_name="boo")
    assert diag.exception_type == "ValueError"
    assert diag.exception_message == "boom"
    assert diag.hook == "install"
    assert diag.substrate == "machine"
    assert diag.charm_name == "boo"
    assert len(diag.fingerprint) == 16
    assert "ValueError" in diag.traceback


def test_fingerprint_stable(monkeypatch):
    """Same failure identity yields the same fingerprint."""
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/config-changed")
    exc_type, exc, tb = _exc_info(NotImplementedFeature("thing"))
    a = diagnostics.collect(exc_type, exc, tb, charm_name="boo")
    b = diagnostics.collect(exc_type, exc, tb, charm_name="boo")
    assert a.fingerprint == b.fingerprint

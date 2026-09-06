# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Tests for `infinicharms.shim`: evolved-charm loading and per-hook guarding."""

from __future__ import annotations

import ops
from ops import testing

from infinicharms import shim, state


class _DefaultCharm(ops.CharmBase):
    """Stand-in for the base template's own baked-in fallback."""


def test_load_delegate_class_falls_back_when_no_checkout(monkeypatch, tmp_path):
    """With no evolved checkout on disk, the default class is returned as-is."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    assert shim.load_delegate_class(_DefaultCharm) is _DefaultCharm


def test_load_delegate_class_imports_evolved_checkout(monkeypatch, tmp_path):
    """A valid evolved checkout's CharmBase subclass is found and returned."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    evolved_src = state.evolved_dir() / "src"
    evolved_src.mkdir(parents=True)
    (evolved_src / "charm.py").write_text(
        "import ops\n\n\nclass EvolvedCharm(ops.CharmBase):\n    pass\n"
    )

    delegate_cls = shim.load_delegate_class(_DefaultCharm)
    assert delegate_cls.__name__ == "EvolvedCharm"
    assert issubclass(delegate_cls, ops.CharmBase)
    assert delegate_cls is not _DefaultCharm


def test_load_delegate_class_falls_back_on_broken_checkout(monkeypatch, tmp_path):
    """A checkout that fails to import degrades to the default, never raises."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    evolved_src = state.evolved_dir() / "src"
    evolved_src.mkdir(parents=True)
    (evolved_src / "charm.py").write_text(
        "raise RuntimeError('syntactically fine, semantically broken')\n"
    )

    assert shim.load_delegate_class(_DefaultCharm) is _DefaultCharm


def test_load_delegate_class_falls_back_when_no_charmbase_subclass(monkeypatch, tmp_path):
    """A checkout with no CharmBase subclass degrades to the default."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    evolved_src = state.evolved_dir() / "src"
    evolved_src.mkdir(parents=True)
    (evolved_src / "charm.py").write_text("VALUE = 1\n")

    assert shim.load_delegate_class(_DefaultCharm) is _DefaultCharm


def test_mark_and_already_reported_roundtrip():
    """mark_reported/already_reported track a single exception instance."""
    exc = ValueError("boom")
    other = ValueError("boom")
    assert shim.already_reported(exc) is False
    shim.mark_reported(exc)
    assert shim.already_reported(exc) is True
    assert shim.already_reported(other) is False
    assert shim.already_reported(None) is False


def test_guarded_charm_reports_and_reraises_hook_failures():
    """A guarded charm class reports a handler's exception, then still raises it."""

    class Boom(ops.CharmBase):
        def __init__(self, framework: ops.Framework) -> None:
            super().__init__(framework)
            framework.observe(self.on.start, self._on_start)

        def _on_start(self, event: ops.StartEvent) -> None:
            raise ValueError("hook exploded")

    reports = []
    guarded_cls = shim.build_guarded_charm_class(
        Boom, lambda exc_info: reports.append(exc_info[1])
    )

    ctx = testing.Context(guarded_cls, meta={"name": "guarded-test"})
    try:
        ctx.run(ctx.on.start(), testing.State())
    except Exception as exc:  # scenario wraps the original in UncaughtCharmError
        assert isinstance(exc.__cause__, ValueError)
    else:
        raise AssertionError("expected the guarded handler's exception to propagate")

    assert len(reports) == 1
    assert str(reports[0]) == "hook exploded"


def test_guarded_charm_does_not_report_successful_hooks():
    """A guarded charm class never calls report_failure when nothing raises."""

    class Fine(ops.CharmBase):
        def __init__(self, framework: ops.Framework) -> None:
            super().__init__(framework)
            framework.observe(self.on.start, self._on_start)

        def _on_start(self, event: ops.StartEvent) -> None:
            pass

    reports = []
    guarded_cls = shim.build_guarded_charm_class(
        Fine, lambda exc_info: reports.append(exc_info[1])
    )

    ctx = testing.Context(guarded_cls, meta={"name": "guarded-test"})
    ctx.run(ctx.on.start(), testing.State())
    assert reports == []


def test_resolve_charm_class_wraps_the_default_when_no_checkout(monkeypatch, tmp_path):
    """resolve_charm_class guards the default class when no checkout exists."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    guarded_cls = shim.resolve_charm_class(_DefaultCharm, lambda exc_info: None)
    assert issubclass(guarded_cls, _DefaultCharm)
    assert guarded_cls is not _DefaultCharm

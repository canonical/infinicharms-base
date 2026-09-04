# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Tests for the `main()` entrypoint wrapper in `charm.py`.

`ops.testing.Context` exercises charm event handlers directly and never goes
through `main()`, so the failure-agent wrapper described in PLAN.md §2.1 (wrap
`ops.main()` in try/except, run the failure agent, re-raise) has no coverage
from `test_charm.py`. These tests close that gap by monkeypatching `ops.main`
itself, so we can control exactly when a "hook" fails without needing a real
Juju unit.
"""

from __future__ import annotations

import subprocess

import pytest

import charm as charm_module


def test_main_runs_failure_agent_and_reraises(monkeypatch):
    """On any hook failure, main() must run the failure agent then re-raise."""

    def boom(charm_cls):
        raise ValueError("hook exploded")

    monkeypatch.setattr(charm_module.ops, "main", boom)
    monkeypatch.setattr(charm_module, "_config_get", lambda key: None)

    calls = {}

    def fake_run(config, exc_info):
        calls["config"] = config
        calls["exc_type"] = exc_info[1].__class__.__name__
        calls["exc_message"] = str(exc_info[1])

    monkeypatch.setattr(charm_module.failure_agent, "run", fake_run)

    with pytest.raises(ValueError, match="hook exploded"):
        charm_module.main()

    assert calls["exc_type"] == "ValueError"
    assert calls["exc_message"] == "hook exploded"


def test_main_success_path_never_calls_agent(monkeypatch):
    """If ops.main succeeds, the failure agent must never be invoked."""
    monkeypatch.setattr(charm_module.ops, "main", lambda charm_cls: None)

    called = {"ran": False}
    monkeypatch.setattr(
        charm_module.failure_agent,
        "run",
        lambda *a, **k: called.__setitem__("ran", True),
    )

    charm_module.main()  # must not raise
    assert called["ran"] is False


def test_main_agent_crash_never_masks_original_error(monkeypatch):
    """A crashing failure agent must never hide the original hook exception."""

    def boom(charm_cls):
        raise RuntimeError("original hook error")

    def agent_boom(config, exc_info):
        raise RuntimeError("agent itself crashed")

    monkeypatch.setattr(charm_module.ops, "main", boom)
    monkeypatch.setattr(charm_module, "_config_get", lambda key: None)
    monkeypatch.setattr(charm_module.failure_agent, "run", agent_boom)

    with pytest.raises(RuntimeError, match="original hook error"):
        charm_module.main()


def test_config_get_parses_json_value(monkeypatch):
    """_config_get shells out to `config-get --format=json` and parses it."""

    class FakeCompleted:
        stdout = '"acme/mono"\n'

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted())
    assert charm_module._config_get("monorepo") == "acme/mono"


def test_config_get_blank_value_is_none(monkeypatch):
    """An empty/blank config value degrades to None."""

    class FakeCompleted:
        stdout = '""\n'

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted())
    assert charm_module._config_get("monorepo") is None


def test_config_get_missing_tool_returns_none(monkeypatch):
    """Outside a hook context (no `config-get` tool), degrade to None."""

    def raise_missing(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_missing)
    assert charm_module._config_get("monorepo") is None


def test_config_get_call_error_returns_none(monkeypatch):
    """A non-zero `config-get` exit degrades to None instead of raising."""

    def raise_called_process_error(*a, **k):
        raise subprocess.CalledProcessError(1, ["config-get"])

    monkeypatch.setattr(subprocess, "run", raise_called_process_error)
    assert charm_module._config_get("monorepo") is None


def test_agent_config_from_env_reads_all_fields(monkeypatch):
    """_agent_config_from_env assembles an AgentConfig purely via config-get."""
    values = {
        "monorepo": "acme/mono",
        "charm-name": "boo",
        "github-token": "ghtok",
        "llm-api-token": "llmtok",
        "llm-model": "some/model",
        "llm-base-url": "https://example/api",
    }
    monkeypatch.setattr(charm_module, "_config_get", lambda key: values.get(key))

    cfg = charm_module._agent_config_from_env()
    assert cfg.monorepo == "acme/mono"
    assert cfg.charm_name == "boo"
    assert cfg.github_token == "ghtok"
    assert cfg.llm_api_token == "llmtok"
    assert cfg.llm_model == "some/model"
    assert cfg.llm_base_url == "https://example/api"


def test_recovery_loop_fail_then_succeed(monkeypatch, tmp_path):
    """Model the recovery loop across two separate hook dispatches.

    Dispatch 1 fails (agent notified, unit would go `error`); dispatch 2 (a
    fresh process, per PLAN.md §2.4) runs patched code and succeeds.

    Each call to ``main()`` here stands in for one *separate* Juju hook dispatch
    (i.e. a fresh Python process in reality). We can't literally re-exec a new
    interpreter in a unit test, so we model "the patch landed" by having the
    fake ``ops.main`` behave differently on the second call.
    """
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setattr(charm_module, "_config_get", lambda key: None)

    agent_calls = []
    monkeypatch.setattr(
        charm_module.failure_agent,
        "run",
        lambda config, exc_info: agent_calls.append(exc_info[1].__class__.__name__),
    )

    attempt = {"n": 0}

    def fake_ops_main(charm_cls):
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise RuntimeError("bug in pre-patch code")
        # Second dispatch: the self-update already swapped in a fix.
        return None

    monkeypatch.setattr(charm_module.ops, "main", fake_ops_main)

    # Dispatch 1: hook fails -> agent runs -> exception still propagates so
    # Juju would see `error` status.
    with pytest.raises(RuntimeError, match="bug in pre-patch code"):
        charm_module.main()
    assert agent_calls == ["RuntimeError"]

    # Dispatch 2: patched code path succeeds; agent must not run again.
    charm_module.main()
    assert agent_calls == ["RuntimeError"]

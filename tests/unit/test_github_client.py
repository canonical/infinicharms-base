# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import pytest

from infinitycharms import github_client
from infinitycharms.github_client import GitHubClient, GitHubError


def test_parse_issue_number():
    """The issue number is parsed from gh's URL output."""
    out = "https://github.com/acme/mono/issues/42\n"
    assert github_client._parse_issue_number(out) == 42


def test_parse_issue_number_bad():
    """Unparsable output raises."""
    with pytest.raises(GitHubError):
        github_client._parse_issue_number("nope")


def test_create_issue_uses_env_token(monkeypatch):
    """create_issue passes GH_TOKEN via env, not argv, and parses the number."""
    captured = {}

    def fake_run(self, args):
        captured["args"] = args
        return "https://github.com/acme/mono/issues/7\n"

    monkeypatch.setattr(GitHubClient, "ensure_gh", lambda self: "/usr/bin/gh")
    monkeypatch.setattr(GitHubClient, "_run", fake_run)

    client = GitHubClient("acme/mono", "tok-secret")
    number = client.create_issue("title", "body", labels=["charm:boo", "type:error"])
    assert number == 7
    # Token must never appear on argv.
    assert "tok-secret" not in " ".join(captured["args"])
    assert "--label" in captured["args"]


def test_arch_mapping(monkeypatch):
    """Architecture maps to gh's naming."""
    monkeypatch.setattr(github_client.platform, "machine", lambda: "x86_64")
    assert github_client._arch() == "amd64"
    monkeypatch.setattr(github_client.platform, "machine", lambda: "aarch64")
    assert github_client._arch() == "arm64"

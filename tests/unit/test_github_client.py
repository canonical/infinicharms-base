# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import pytest

from infinicharms import github_client
from infinicharms.github_client import GitHubClient, GitHubError


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
    calls = []

    def fake_run(self, args):
        calls.append(args)
        # The issue-create call is the one that returns a URL.
        if "create" in args and "issue" in args:
            return "https://github.com/acme/mono/issues/7\n"
        return ""

    monkeypatch.setattr(GitHubClient, "ensure_gh", lambda self: "/usr/bin/gh")
    monkeypatch.setattr(GitHubClient, "_run", fake_run)

    client = GitHubClient("acme/mono", "tok-secret")
    number = client.create_issue("title", "body", labels=["charm:boo", "type:error"])
    assert number == 7
    issue_args = [a for a in calls if "issue" in a and "create" in a][0]
    # Token must never appear on argv.
    assert "tok-secret" not in " ".join(issue_args)
    assert "--label" in issue_args


def test_create_issue_ensures_labels_first(monkeypatch):
    """create_issue upserts each label (gh label create --force) before filing."""
    calls = []

    def fake_run(self, args):
        calls.append(args)
        if "create" in args and "issue" in args:
            return "https://github.com/acme/mono/issues/9\n"
        return ""

    monkeypatch.setattr(GitHubClient, "ensure_gh", lambda self: "/usr/bin/gh")
    monkeypatch.setattr(GitHubClient, "_run", fake_run)

    client = GitHubClient("acme/mono", "tok")
    client.create_issue("t", "b", labels=["type:error", "charm:boo"])

    label_calls = [a for a in calls if "label" in a and "create" in a]
    assert len(label_calls) == 2
    assert all("--force" in a for a in label_calls)
    # Labels are created before the issue.
    assert calls.index(label_calls[-1]) < next(
        i for i, a in enumerate(calls) if "issue" in a and "create" in a
    )


def test_ensure_labels_swallows_errors(monkeypatch):
    """A failure to create a label must not abort issue creation."""

    def fake_run(self, args):
        if "label" in args:
            raise GitHubError("no permission")
        if "create" in args and "issue" in args:
            return "https://github.com/acme/mono/issues/3\n"
        return ""

    monkeypatch.setattr(GitHubClient, "ensure_gh", lambda self: "/usr/bin/gh")
    monkeypatch.setattr(GitHubClient, "_run", fake_run)

    client = GitHubClient("acme/mono", "tok")
    # Should still create the issue despite label failures.
    assert client.create_issue("t", "b", labels=["type:error"]) == 3


def test_label_color():
    """Label namespaces map to stable colors, with a default fallback."""
    assert github_client._label_color("type:error") == "d73a4a"
    assert github_client._label_color("charm:boo") == "0e8a16"
    assert github_client._label_color("severity:high") == "fbca04"
    assert github_client._label_color("weird") == "ededed"


def test_arch_mapping(monkeypatch):
    """Architecture maps to gh's naming."""
    monkeypatch.setattr(github_client.platform, "machine", lambda: "x86_64")
    assert github_client._arch() == "amd64"
    monkeypatch.setattr(github_client.platform, "machine", lambda: "aarch64")
    assert github_client._arch() == "arm64"

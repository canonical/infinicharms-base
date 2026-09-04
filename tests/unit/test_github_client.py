# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import os
import subprocess
from pathlib import Path

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


BUG_TEMPLATE = (
    "[Bug]\n"
    "<!--\n"
    "Thanks for reporting a bug! This will be picked up automatically by an\n"
    "agent that will investigate, comment a plan on this issue, and open a PR\n"
    "with a fix (or close the issue if no change is needed).\n"
    "-->\n"
    "\n"
    "### Charm name\n"
    "\n"
    "\n"
    "\n"
    "### What happened?\n"
    "\n"
    "\n"
    "\n"
    "### What did you expect to happen?\n"
    "\n"
    "\n"
    "\n"
    "### Charm version (optional)\n"
    "\n"
    "------------------------ >8 ------------------------\n"
    "\n"
    "Please Enter the title on the first line and the body on subsequent lines.\n"
    "Lines below dotted lines will be ignored, and an empty title aborts the creation process.\n"
)


def test_bug_editor_script_fills_in_template(tmp_path):
    """The generated editor script fills in each field via sed, in place."""
    template_file = tmp_path / "issue.md"
    template_file.write_text(BUG_TEMPLATE)

    script = GitHubClient._write_bug_editor_script(
        tmp_path,
        title="charm boo: db-relation-changed failed",
        charm_name="boo",
        what_happened="The relation handler raised a KeyError.\nSecond line.",
        what_expected="It should not crash.",
        charm_version="1.2.3",
    )
    subprocess.run(["sh", str(script), str(template_file)], check=True)

    result = template_file.read_text()
    lines = result.splitlines()
    assert lines[0] == "charm boo: db-relation-changed failed"

    assert "### Charm name\nboo" in result
    assert "### What happened?\nThe relation handler raised a KeyError.\nSecond line." in result
    assert "### What did you expect to happen?\nIt should not crash." in result
    assert "### Charm version (optional)\n1.2.3" in result
    # The separator and trailing instructions are left untouched.
    assert "------------------------ >8 ------------------------" in result


def test_bug_editor_script_leaves_optional_version_blank(tmp_path):
    """An empty charm_version doesn't corrupt the template."""
    template_file = tmp_path / "issue.md"
    template_file.write_text(BUG_TEMPLATE)

    script = GitHubClient._write_bug_editor_script(
        tmp_path,
        title="t",
        charm_name="boo",
        what_happened="h",
        what_expected="e",
        charm_version="",
    )
    subprocess.run(["sh", str(script), str(template_file)], check=True)

    result = template_file.read_text()
    assert "### Charm version (optional)\n\n------------------------" in result


def test_create_bug_uses_env_token_and_editor(monkeypatch, tmp_path):
    """create_bug drives gh issue create -T Bug -e via a generated $EDITOR script."""
    calls = []
    editor_existed = {}

    def fake_run(self, args, extra_env=None):
        calls.append(args)
        if "create" in args and "issue" in args:
            # The editor script must exist (and be executable) while gh would
            # be invoking it, i.e. for the duration of this call.
            editor_existed["exists"] = Path(extra_env["EDITOR"]).exists()
            editor_existed["executable"] = os.access(extra_env["EDITOR"], os.X_OK)
            return "https://github.com/acme/mono/issues/11\n"
        return ""

    monkeypatch.setattr(GitHubClient, "ensure_gh", lambda self: "/usr/bin/gh")
    monkeypatch.setattr(GitHubClient, "_run", fake_run)

    client = GitHubClient("acme/mono", "tok-secret")
    number = client.create_bug(
        title="title",
        charm_name="boo",
        what_happened="it broke",
        what_expected="it should work",
        charm_version="1.0",
    )
    assert number == 11

    issue_args = [a for a in calls if "issue" in a and "create" in a][0]
    assert "-T" in issue_args
    assert "Bug" in issue_args
    assert "-e" in issue_args
    assert "--label" not in issue_args
    assert "tok-secret" not in " ".join(issue_args)
    assert editor_existed["exists"]
    assert editor_existed["executable"]


def test_arch_mapping(monkeypatch):
    """Architecture maps to gh's naming."""
    monkeypatch.setattr(github_client.platform, "machine", lambda: "x86_64")
    assert github_client._arch() == "amd64"
    monkeypatch.setattr(github_client.platform, "machine", lambda: "aarch64")
    assert github_client._arch() == "arm64"

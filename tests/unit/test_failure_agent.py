# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import sys

from infinicharms import failure_agent, state
from infinicharms.exceptions import NotImplementedFeature
from infinicharms.llm import LLMResult


def _exc_info(exc: BaseException):
    try:
        raise exc
    except BaseException:
        return sys.exc_info()


def test_classify():
    """NotImplementedFeature classifies as not-implemented, else error."""
    assert failure_agent.classify(NotImplementedFeature("x")) == "not-implemented"
    assert failure_agent.classify(ValueError("x")) == "error"


def test_run_files_new_issue(monkeypatch, tmp_path):
    """A first-time failure files a new issue and records the fingerprint."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/install")

    created = {}

    class FakeGH:
        def __init__(self, repo, token):
            created["repo"] = repo

        def issue_is_open(self, number):
            return False

        def create_issue(self, title, body, labels=None):
            created["title"] = title
            created["labels"] = labels
            return 101

        def comment_issue(self, number, body):
            created["commented"] = number

    monkeypatch.setattr(failure_agent, "GitHubClient", FakeGH)
    # No LLM API token -> fallback template path.
    config = failure_agent.AgentConfig(monorepo="acme/mono", charm_name="boo", github_token="tok")
    failure_agent.run(config, _exc_info(ValueError("boom")))

    st = state.State.load()
    assert 101 in st.issues.values()
    assert created["title"]
    assert "charm:boo" in created["labels"]
    assert "type:error" in created["labels"]


def test_run_comments_on_existing_open_issue(monkeypatch, tmp_path):
    """A repeat failure comments on the existing open issue (de-dup)."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/install")

    events = {}

    class FakeGH:
        def __init__(self, repo, token):
            pass

        def issue_is_open(self, number):
            return True

        def create_issue(self, title, body, labels=None):
            events["created"] = True
            return 999

        def comment_issue(self, number, body):
            events["commented"] = number

    monkeypatch.setattr(failure_agent, "GitHubClient", FakeGH)
    config = failure_agent.AgentConfig(monorepo="acme/mono", charm_name="boo", github_token="tok")
    exc_info = _exc_info(ValueError("boom"))

    # First run records the issue.
    monkeypatch.setattr(FakeGH, "issue_is_open", lambda self, n: False)
    failure_agent.run(config, exc_info)
    # Second run: same fingerprint, issue open -> comment.
    monkeypatch.setattr(FakeGH, "issue_is_open", lambda self, n: True)
    failure_agent.run(config, _exc_info(ValueError("boom")))
    assert "commented" in events


def test_summarize_uses_llm(monkeypatch, tmp_path):
    """When a token is present, the LLM result is used."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    from infinicharms import diagnostics

    diag = diagnostics.collect(*_exc_info(ValueError("boom")), charm_name="boo")

    class FakeClient:
        def __init__(self, token, model, base_url=None):
            pass

        def summarize_failure(self, system, user):
            return LLMResult(title="LLM title", body="LLM body", severity="high")

    monkeypatch.setattr(failure_agent, "LLMClient", FakeClient)
    config = failure_agent.AgentConfig(charm_name="boo", llm_api_token="k")
    result = failure_agent._summarize(config, diag, "error")
    assert result.title == "LLM title"
    assert result.severity == "high"

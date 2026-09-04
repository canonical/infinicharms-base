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

        def create_bug(self, title, charm_name, what_happened, what_expected, charm_version=""):
            created["title"] = title
            created["charm_name"] = charm_name
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
    assert created["charm_name"] == "boo"
    # The agent's own outcome is recorded for debuggability.
    assert st.last_agent_run is not None
    assert st.last_agent_run["outcome"] == "filed"
    assert st.last_agent_run["issue"] == 101


def test_run_records_failed_agent_run_on_github_error(monkeypatch, tmp_path):
    """When gh fails, the agent records outcome=failed with the error."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/install")

    from infinicharms.github_client import GitHubError

    class FakeGH:
        def __init__(self, repo, token):
            pass

        def issue_is_open(self, number):
            return False

        def create_bug(self, title, charm_name, what_happened, what_expected, charm_version=""):
            raise GitHubError("gh failed (1): Could not resolve to a Repository")

        def comment_issue(self, number, body):
            pass

    monkeypatch.setattr(failure_agent, "GitHubClient", FakeGH)
    config = failure_agent.AgentConfig(monorepo="acme/nope", charm_name="boo", github_token="tok")
    failure_agent.run(config, _exc_info(ValueError("boom")))

    st = state.State.load()
    assert st.issues == {}
    assert st.last_agent_run is not None
    assert st.last_agent_run["outcome"] == "failed"
    assert st.last_agent_run["error_type"] == "GitHubError"
    assert "Could not resolve" in str(st.last_agent_run["error_message"])


def test_run_records_skipped_when_config_missing(monkeypatch, tmp_path):
    """Without monorepo/github-token the agent records outcome=skipped."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/install")

    config = failure_agent.AgentConfig(charm_name="boo")  # no monorepo/token
    failure_agent.run(config, _exc_info(ValueError("boom")))

    st = state.State.load()
    assert st.last_agent_run is not None
    assert st.last_agent_run["outcome"] == "skipped"


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

        def create_bug(self, title, charm_name, what_happened, what_expected, charm_version=""):
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


def _run_capturing_classification(monkeypatch, tmp_path, exc, llm_result):
    """Run the agent with a stubbed LLM + GitHub and return the recorded classification."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/db-relation-changed")

    class FakeGH:
        def __init__(self, repo, token):
            pass

        def issue_is_open(self, number):
            return False

        def create_bug(self, title, charm_name, what_happened, what_expected, charm_version=""):
            return 1

        def comment_issue(self, number, body):
            pass

    class FakeClient:
        def __init__(self, token, model, base_url=None):
            pass

        def summarize_failure(self, system, user):
            return llm_result

    monkeypatch.setattr(failure_agent, "GitHubClient", FakeGH)
    monkeypatch.setattr(failure_agent, "LLMClient", FakeClient)
    config = failure_agent.AgentConfig(
        monorepo="acme/mono", charm_name="boo", github_token="tok", llm_api_token="k"
    )
    failure_agent.run(config, _exc_info(exc))
    st = state.State.load()
    return st.last_agent_run["classification"]


def test_llm_overrides_heuristic_to_not_implemented(monkeypatch, tmp_path):
    """LLM classifies an unexpected AttributeError as not-implemented.

    Even though no ``NotImplementedFeature`` was raised, the LLM's verdict wins.
    """
    result = LLMResult(
        title="boo: implement db relation",
        body="...",
        severity="medium",
        classification="not-implemented",
    )
    classification = _run_capturing_classification(
        monkeypatch, tmp_path, AttributeError("no attr 'relation'"), result
    )
    assert classification == "not-implemented"


def test_llm_overrides_heuristic_to_error(monkeypatch, tmp_path):
    """LLM downgrades a NotImplementedFeature it deems a real bug to error.

    The LLM verdict wins over the type-based hint.
    """
    result = LLMResult(
        title="boo: db relation crashes",
        body="...",
        severity="high",
        classification="error",
    )
    classification = _run_capturing_classification(
        monkeypatch, tmp_path, NotImplementedFeature("db"), result
    )
    assert classification == "error"


def test_unknown_llm_classification_falls_back_to_heuristic(monkeypatch, tmp_path):
    """When the LLM declines to classify, the heuristic classification is used."""
    result = LLMResult(title="t", body="b", severity="low", classification="unknown")
    classification = _run_capturing_classification(
        monkeypatch, tmp_path, NotImplementedFeature("db"), result
    )
    assert classification == "not-implemented"


def test_fallback_template_uses_heuristic_classification(monkeypatch, tmp_path):
    """With no LLM token, the fallback result carries the heuristic classification."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    from infinicharms import diagnostics

    diag = diagnostics.collect(*_exc_info(ValueError("boom")), charm_name="boo")
    config = failure_agent.AgentConfig(charm_name="boo")  # no llm_api_token
    result = failure_agent._summarize(config, diag, "not-implemented")
    assert result.classification == "not-implemented"

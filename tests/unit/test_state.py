# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

from infinicharms import state


def test_state_roundtrip(tmp_path):
    """State saves and loads, including issues and applied tag."""
    path = tmp_path / "state.json"
    st = state.State(applied_tag="boo/v1.0.0")
    st.record_issue("abc123", 42)
    st.save(path)

    loaded = state.State.load(path)
    assert loaded.applied_tag == "boo/v1.0.0"
    assert loaded.issue_for("abc123") == 42
    assert loaded.issue_for("missing") is None


def test_state_persists_last_agent_run(tmp_path):
    """The agent's own outcome snapshot round-trips."""
    path = tmp_path / "state.json"
    st = state.State(last_agent_run={"outcome": "failed", "error_type": "GitHubError"})
    st.save(path)

    loaded = state.State.load(path)
    assert loaded.last_agent_run == {"outcome": "failed", "error_type": "GitHubError"}


def test_state_missing_file_defaults(tmp_path):
    """Loading a non-existent state file returns defaults."""
    loaded = state.State.load(tmp_path / "nope.json")
    assert loaded.applied_tag is None
    assert loaded.issues == {}


def test_state_corrupt_file_defaults(tmp_path):
    """Loading a corrupt state file returns defaults."""
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    loaded = state.State.load(path)
    assert loaded.applied_tag is None

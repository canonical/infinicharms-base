# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

import io
import zipfile

import pytest

from infinitycharms import state, updater


def test_semver_parse_tag():
    """Only tags matching <charm-name>/vX.Y.Z parse."""
    assert updater.SemVer.parse_tag("boo/v1.2.3", "boo") == updater.SemVer(1, 2, 3)
    assert updater.SemVer.parse_tag("bar/v1.2.3", "boo") is None
    assert updater.SemVer.parse_tag("boo/1.2.3", "boo") is None


def test_semver_ordering():
    """SemVer compares as expected."""
    assert updater.SemVer(1, 0, 0) < updater.SemVer(1, 0, 1)
    assert updater.SemVer(2, 0, 0) > updater.SemVer(1, 9, 9)


def _make_charm_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/charm.py", "print('new')\n")
        zf.writestr("SOUL.md", "new soul\n")
    return buf.getvalue()


def test_apply_downloads_and_swaps(monkeypatch, tmp_path):
    """apply() resolves latest, downloads, unpacks, swaps and records tag."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "charm.py").write_text("print('old')\n")

    up = updater.Updater("acme/mono", "boo")

    monkeypatch.setattr(
        up,
        "latest_release",
        lambda tag=None: updater.Release(
            tag="boo/v1.0.0",
            version=updater.SemVer(1, 0, 0),
            asset_url="https://example/boo.charm",
            asset_name="boo_v1.0.0.charm",
        ),
    )
    charm_bytes = _make_charm_zip()
    monkeypatch.setattr(up, "_download", lambda url, dest: dest.write_bytes(charm_bytes))

    result = up.apply()
    assert result["updated"] is True
    assert result["applied_tag"] == "boo/v1.0.0"
    assert (tmp_path / "src" / "charm.py").read_text() == "print('new')\n"
    assert (tmp_path / "SOUL.md").read_text() == "new soul\n"

    st = state.State.load()
    assert st.applied_tag == "boo/v1.0.0"


def test_apply_refuses_downgrade(monkeypatch, tmp_path):
    """apply() is idempotent and refuses downgrades without force."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    st = state.State(applied_tag="boo/v2.0.0")
    st.save()

    up = updater.Updater("acme/mono", "boo")
    monkeypatch.setattr(
        up,
        "latest_release",
        lambda tag=None: updater.Release(
            tag="boo/v1.0.0",
            version=updater.SemVer(1, 0, 0),
            asset_url="https://example/boo.charm",
            asset_name="boo_v1.0.0.charm",
        ),
    )
    result = up.apply()
    assert result["updated"] is False


def test_apply_no_release(monkeypatch, tmp_path):
    """apply() handles no matching release."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    up = updater.Updater("acme/mono", "boo")
    monkeypatch.setattr(up, "latest_release", lambda tag=None: None)
    result = up.apply()
    assert result["updated"] is False
    assert result["reason"] == "no matching release"


def test_bad_zip_raises(monkeypatch, tmp_path):
    """A non-zip artifact raises UpdateError."""
    monkeypatch.setenv("JUJU_CHARM_DIR", str(tmp_path))
    up = updater.Updater("acme/mono", "boo")
    monkeypatch.setattr(
        up,
        "latest_release",
        lambda tag=None: updater.Release(
            tag="boo/v1.0.0",
            version=updater.SemVer(1, 0, 0),
            asset_url="https://example/boo.charm",
            asset_name="boo_v1.0.0.charm",
        ),
    )
    monkeypatch.setattr(up, "_download", lambda url, dest: dest.write_bytes(b"not a zip"))
    with pytest.raises(updater.UpdateError):
        up.apply()

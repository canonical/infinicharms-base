# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Option A hot-patch / self-update.

On each run (gated by ``auto-update``), resolve the latest matching release for
this charm from the monorepo, download the packed ``.charm`` (a zip), unpack it,
and overwrite ``src/``, ``lib/``, ``prompts/`` and ``SOUL.md`` under
``$JUJU_CHARM_DIR``. The next hook execs the new code (a fresh Python process
re-reads disk).

Important caveats (PLAN.md §2.4): this is **not durable** across ``juju refresh``
or pod churn — Juju restores the controller-stored revision. That is acceptable
for the hackathon demo; the mechanism is self-contained and requires no
controller credentials.

Release naming convention (PLAN.md §5):

* tag:   ``<charm-name>/v<MAJOR>.<MINOR>.<PATCH>``   e.g. ``boo/v0.3.1``
* asset: ``<charm-name>_v<MAJOR>.<MINOR>.<PATCH>.charm``  e.g. ``boo_v0.3.1.charm``
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import state

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
# Directories/files that a release may overwrite in-place.
SWAP_TARGETS = ("src", "lib", "prompts", "SOUL.md")
_TAG_RE = re.compile(r"^(?P<name>.+)/v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class UpdateError(Exception):
    """Raised when an update cannot be completed."""


@dataclass(order=True)
class SemVer:
    """A minimal semantic version for comparison."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse_tag(cls, tag: str, charm_name: str) -> SemVer | None:
        """Parse ``<charm-name>/vX.Y.Z``; return None if it doesn't match."""
        match = _TAG_RE.match(tag)
        if not match or match.group("name") != charm_name:
            return None
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
        )


@dataclass
class Release:
    """A resolved release candidate for this charm."""

    tag: str
    version: SemVer
    asset_url: str
    asset_name: str


class Updater:
    """Resolve, download, and apply the latest matching release."""

    def __init__(self, monorepo: str, charm_name: str, github_token: str | None = None):
        """Initialise the updater.

        Args:
            monorepo: ``owner/repo`` of the monorepo.
            charm_name: This charm's subdir/name; scopes matching releases.
            github_token: Optional token to authenticate release API calls.
        """
        self._monorepo = monorepo
        self._charm_name = charm_name
        self._token = github_token

    # -- release resolution ------------------------------------------------

    def _api_get(self, path: str) -> object:
        url = f"{GITHUB_API}/repos/{self._monorepo}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "infinicharms-base",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise UpdateError(f"GitHub API request failed: {exc}") from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise UpdateError(f"GitHub API returned invalid JSON: {exc}") from exc

    def latest_release(self, tag: str | None = None) -> Release | None:
        """Return the newest release matching this charm, or a specific tag."""
        releases = self._api_get("/releases")
        if not isinstance(releases, list):
            raise UpdateError("unexpected releases payload")

        candidates: list[Release] = []
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            rel_tag = str(rel.get("tag_name") or "")
            version = SemVer.parse_tag(rel_tag, self._charm_name)
            if version is None:
                continue
            if tag is not None and rel_tag != tag:
                continue
            asset = self._select_asset(rel)
            if asset is None:
                continue
            candidates.append(
                Release(
                    tag=rel_tag,
                    version=version,
                    asset_url=asset[0],
                    asset_name=asset[1],
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.version)

    def _select_asset(self, rel: dict) -> tuple[str, str] | None:
        """Pick the ``.charm`` asset from a release payload."""
        for asset in rel.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if name.endswith(".charm"):
                url = str(asset.get("browser_download_url") or "")
                if url:
                    return url, name
        return None

    # -- apply -------------------------------------------------------------

    def apply(self, *, tag: str | None = None, force: bool = False) -> dict[str, object]:
        """Resolve, download and apply the latest (or specified) release.

        Returns a small summary. Idempotent; refuses downgrades unless ``force``.
        """
        st = state.State.load()
        release = self.latest_release(tag=tag)
        if release is None:
            return {"updated": False, "reason": "no matching release"}

        current = SemVer.parse_tag(st.applied_tag, self._charm_name) if st.applied_tag else None
        if not force and current is not None and release.version <= current:
            return {
                "updated": False,
                "reason": "already up to date",
                "applied_tag": st.applied_tag,
            }

        self._download_and_swap(release)
        st.applied_tag = release.tag
        st.save()
        logger.info("Applied release %s", release.tag)
        return {"updated": True, "applied_tag": release.tag}

    def _download_and_swap(self, release: Release) -> None:
        """Download the .charm zip, verify it, and swap targets in place."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / release.asset_name
            self._download(release.asset_url, archive_path)

            if not zipfile.is_zipfile(archive_path):
                raise UpdateError("downloaded artifact is not a valid .charm zip")

            extract_dir = tmp_path / "unpacked"
            with zipfile.ZipFile(archive_path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise UpdateError(f"corrupt entry in .charm zip: {bad}")
                zf.extractall(extract_dir)

            self._swap_targets(extract_dir)

    def _download(self, url: str, dest: Path) -> None:
        headers = {"User-Agent": "infinicharms-base", "Accept": "application/octet-stream"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                dest.write_bytes(response.read())
        except urllib.error.URLError as exc:
            raise UpdateError(f"failed to download release asset: {exc}") from exc

    def _swap_targets(self, extract_dir: Path) -> None:
        """Overwrite SWAP_TARGETS under the charm dir from the unpacked release."""
        charm_dir = state.charm_dir()
        for target in SWAP_TARGETS:
            source = extract_dir / target
            if not source.exists():
                continue
            destination = charm_dir / target
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            logger.info("Swapped %s from release", target)

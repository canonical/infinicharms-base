# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Option A hot-patch / self-update.

On each run (gated by ``auto-update``), resolve the latest matching release for
this charm from the monorepo, download the packed ``.charm`` (a zip), and
unpack it in full into ``state_dir()/evolved`` (see ``infinicharms.state``).
Nothing under ``$JUJU_CHARM_DIR`` itself is ever modified: ``infinicharms.shim``
dynamically loads the checkout's ``src/charm.py`` at dispatch time instead, so
a newly-fetched release can take effect immediately, within the very dispatch
that fetched it -- see ``charm.main()``.

Important caveats (PLAN.md §2.4): this is **not durable** across ``juju refresh``
or pod churn — Juju restores the controller-stored revision, which wipes
``.infinicharms/`` along with it. That is acceptable for the hackathon demo; the
mechanism is self-contained and requires no controller credentials.

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
        logger.info("GET %s (authenticated=%s)", url, bool(self._token))
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                status = getattr(response, "status", None)
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning(
                "GitHub API request failed: %s %s -> HTTP %s: %s",
                request.method,
                url,
                exc.code,
                detail,
            )
            raise UpdateError(f"GitHub API request failed: {exc}") from exc
        except urllib.error.URLError as exc:
            logger.warning("GitHub API request failed: %s %s -> %s", request.method, url, exc)
            raise UpdateError(f"GitHub API request failed: {exc}") from exc
        logger.info("GET %s -> HTTP %s, %d bytes", url, status, len(body))
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("GitHub API returned invalid JSON from %s: %s", url, exc)
            raise UpdateError(f"GitHub API returned invalid JSON: {exc}") from exc

    def latest_release(self, tag: str | None = None) -> Release | None:
        """Return the newest release matching this charm, or a specific tag."""
        releases = self._api_get("/releases")
        if not isinstance(releases, list):
            logger.warning(
                "Unexpected /releases payload from %s (got %s, expected a list)",
                self._monorepo,
                type(releases).__name__,
            )
            raise UpdateError("unexpected releases payload")
        logger.info(
            "Fetched %d release(s) from %s (charm_name=%s, tag_filter=%s)",
            len(releases),
            self._monorepo,
            self._charm_name,
            tag,
        )

        candidates: list[Release] = []
        skipped_no_asset: list[str] = []
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
                skipped_no_asset.append(rel_tag)
                continue
            candidates.append(
                Release(
                    tag=rel_tag,
                    version=version,
                    asset_url=asset[0],
                    asset_name=asset[1],
                )
            )
        if skipped_no_asset:
            logger.warning(
                "Ignoring %d release(s) matching charm_name=%s with no .charm asset: %s",
                len(skipped_no_asset),
                self._charm_name,
                ", ".join(skipped_no_asset),
            )
        if not candidates:
            logger.info(
                "No release matches charm_name=%s (tag_filter=%s) in %s out of %d fetched",
                self._charm_name,
                tag,
                self._monorepo,
                len(releases),
            )
            return None
        winner = max(candidates, key=lambda r: r.version)
        logger.info(
            "Resolved %d matching candidate(s) for charm_name=%s; latest is %s (asset=%s)",
            len(candidates),
            self._charm_name,
            winner.tag,
            winner.asset_name,
        )
        return winner

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
        logger.info(
            "apply() starting: monorepo=%s charm_name=%s tag=%s force=%s",
            self._monorepo,
            self._charm_name,
            tag,
            force,
        )
        st = state.State.load()
        release = self.latest_release(tag=tag)
        if release is None:
            logger.info(
                "No matching release to apply for charm_name=%s in %s (currently applied=%s)",
                self._charm_name,
                self._monorepo,
                st.applied_tag,
            )
            return {"updated": False, "reason": "no matching release"}

        current = SemVer.parse_tag(st.applied_tag, self._charm_name) if st.applied_tag else None
        if not force and current is not None and release.version <= current:
            logger.info(
                "Already up to date: applied=%s, latest available=%s", st.applied_tag, release.tag
            )
            return {
                "updated": False,
                "reason": "already up to date",
                "applied_tag": st.applied_tag,
            }

        logger.info(
            "Applying release %s (previously applied=%s)", release.tag, st.applied_tag or "none"
        )
        try:
            self._download_and_extract(release)
        except UpdateError:
            logger.warning(
                "Failed to apply release %s; leaving evolved checkout untouched", release.tag
            )
            raise
        st.applied_tag = release.tag
        st.save()
        logger.info("Applied release %s", release.tag)
        return {"updated": True, "applied_tag": release.tag}

    def _download_and_extract(self, release: Release) -> None:
        """Download the .charm zip, verify it, and extract it into the evolved dir."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_path = tmp_path / release.asset_name
            self._download(release.asset_url, archive_path)

            if not zipfile.is_zipfile(archive_path):
                logger.warning(
                    "Downloaded artifact for %s (%s) is not a valid .charm zip",
                    release.tag,
                    release.asset_name,
                )
                raise UpdateError("downloaded artifact is not a valid .charm zip")

            extract_dir = tmp_path / "unpacked"
            with zipfile.ZipFile(archive_path) as zf:
                bad = zf.testzip()
                if bad is not None:
                    logger.warning("Corrupt entry in .charm zip for %s: %s", release.tag, bad)
                    raise UpdateError(f"corrupt entry in .charm zip: {bad}")
                logger.info(
                    "Extracting %d entries from %s into %s",
                    len(zf.infolist()),
                    release.asset_name,
                    extract_dir,
                )
                zf.extractall(extract_dir)

            self._replace_evolved(extract_dir)

    def _download(self, url: str, dest: Path) -> None:
        headers = {"User-Agent": "infinicharms-base", "Accept": "application/octet-stream"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        logger.info("Downloading release asset from %s", url)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                data = response.read()
        except urllib.error.URLError as exc:
            logger.warning("Failed to download release asset from %s: %s", url, exc)
            raise UpdateError(f"failed to download release asset: {exc}") from exc
        dest.write_bytes(data)
        logger.info("Downloaded %d bytes from %s to %s", len(data), url, dest)

    def _replace_evolved(self, extract_dir: Path) -> None:
        """Atomically replace ``state_dir()/evolved`` with the unpacked release.

        Extracts to a sibling staging directory first and renames it into place,
        so a hook that's interrupted mid-extract never leaves ``evolved`` half
        written for the next dispatch to load.
        """
        target = state.evolved_dir()
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".new")
        if staging.exists():
            logger.info(
                "Removing stale staging dir %s from a previous interrupted update", staging
            )
            shutil.rmtree(staging)
        shutil.copytree(extract_dir, staging)
        replaced_existing = target.exists()
        if replaced_existing:
            shutil.rmtree(target)
        staging.rename(target)
        logger.info(
            "Extracted release into %s (%s previous checkout)",
            target,
            "replaced existing" if replaced_existing else "no",
        )

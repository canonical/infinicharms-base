# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""A thin wrapper around the ``gh`` CLI for creating and commenting on issues.

``gh`` is not preinstalled on either substrate's charm container. To be robust
and substrate-agnostic (see PLAN.md §2.3), we bootstrap it the same way on both
machine and Kubernetes:

1. If ``gh`` is already on ``PATH`` (or previously bootstrapped), use it.
2. Otherwise download the official release tarball for the unit's architecture
   straight from ``github.com/cli/cli/releases`` over HTTPS (stdlib ``urllib``)
   into ``$JUJU_CHARM_DIR/.infinicharms/bin/gh``.

The fine-grained token is passed via the ``GH_TOKEN`` environment variable and
never appears on ``argv`` or in logs.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import state

logger = logging.getLogger(__name__)

# A pinned, known-good gh version for the offline/bootstrap path. The exact
# version is not important for the demo; any recent release works.
GH_VERSION = "2.62.0"
GH_RELEASE_URL = (
    "https://github.com/cli/cli/releases/download/v{version}/gh_{version}_linux_{arch}.tar.gz"
)


class GitHubError(Exception):
    """Raised when a gh invocation fails."""


def _bin_dir() -> Path:
    return state.state_dir() / "bin"


def _arch() -> str:
    """Map the machine architecture to gh's release naming."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


class GitHubClient:
    """Create and comment on GitHub issues via the ``gh`` CLI."""

    def __init__(self, repo: str, token: str):
        """Initialise the client.

        Args:
            repo: ``owner/repo`` of the monorepo.
            token: Fine-grained GitHub token (monorepo-scoped).
        """
        self._repo = repo
        self._token = token
        self._gh_path: str | None = None

    # -- bootstrap ---------------------------------------------------------

    def ensure_gh(self) -> str:
        """Return a usable ``gh`` path, bootstrapping the binary if needed."""
        if self._gh_path and Path(self._gh_path).exists():
            return self._gh_path

        found = shutil.which("gh")
        if found:
            self._gh_path = found
            return found

        bootstrapped = _bin_dir() / "gh"
        if bootstrapped.exists():
            self._gh_path = str(bootstrapped)
            return self._gh_path

        self._gh_path = self._download_gh()
        return self._gh_path

    def _download_gh(self) -> str:
        """Download and unpack the gh release tarball for this arch."""
        url = GH_RELEASE_URL.format(version=GH_VERSION, arch=_arch())
        logger.info("Bootstrapping gh CLI from %s", url)
        bin_dir = _bin_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            tarball = Path(tmp) / "gh.tar.gz"
            urllib.request.urlretrieve(url, tarball)  # noqa: S310 - trusted GitHub host
            with tarfile.open(tarball) as archive:
                member = _find_gh_member(archive)
                if member is None:
                    raise GitHubError("gh binary not found in release tarball")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise GitHubError("could not read gh binary from tarball")
                target = bin_dir / "gh"
                target.write_bytes(extracted.read())
                target.chmod(0o755)
        return str(bin_dir / "gh")

    # -- issue operations --------------------------------------------------

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> int:
        """Create an issue and return its number.

        The agent uses custom labels (``type:*``, ``charm:*``, ``severity:*``)
        that may not exist in the target monorepo. ``gh issue create`` fails hard
        on an unknown label, so we ensure each label exists first (idempotently)
        before creating the issue. See DEBUGGING.md ("could not add label").
        """
        gh = self.ensure_gh()
        wanted = labels or []
        self._ensure_labels(wanted)
        args = [
            gh,
            "issue",
            "create",
            "--repo",
            self._repo,
            "--title",
            title,
            "--body",
            body,
        ]
        for label in wanted:
            args += ["--label", label]
        output = self._run(args)
        return _parse_issue_number(output)

    def _ensure_labels(self, labels: list[str]) -> None:
        """Create any labels that don't yet exist (best-effort, idempotent).

        ``gh label create --force`` upserts, so it never fails if the label
        already exists. A failure to create a label (e.g. token lacks the scope)
        is logged and swallowed so we still attempt to file the issue -- an issue
        with fewer labels is better than no issue at all.
        """
        gh = self.ensure_gh()
        for label in labels:
            args = [
                gh,
                "label",
                "create",
                label,
                "--repo",
                self._repo,
                "--color",
                _label_color(label),
                "--description",
                "Created by the InfiniCharms failure agent",
                "--force",
            ]
            try:
                self._run(args)
            except GitHubError as exc:
                logger.warning("Could not ensure label %r (continuing): %s", label, exc)

    def comment_issue(self, issue_number: int, body: str) -> None:
        """Add a comment to an existing issue."""
        gh = self.ensure_gh()
        args = [
            gh,
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            self._repo,
            "--body",
            body,
        ]
        self._run(args)

    def issue_is_open(self, issue_number: int) -> bool:
        """Return True if the issue exists and is open."""
        gh = self.ensure_gh()
        args = [
            gh,
            "issue",
            "view",
            str(issue_number),
            "--repo",
            self._repo,
            "--json",
            "state",
            "-q",
            ".state",
        ]
        try:
            output = self._run(args)
        except GitHubError:
            return False
        return output.strip().upper() == "OPEN"

    # -- internals ---------------------------------------------------------

    def _run(self, args: list[str]) -> str:
        """Run a gh command with GH_TOKEN in the environment. Never logs token."""
        env = dict(os.environ)
        env["GH_TOKEN"] = self._token
        # Ensure our bootstrapped bin dir is discoverable for any child lookups.
        env["PATH"] = f"{_bin_dir()}{os.pathsep}{env.get('PATH', '')}"
        try:
            completed = subprocess.run(  # noqa: S603 - args are constructed, not shell
                args,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise GitHubError(f"gh not found: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            raise GitHubError(f"gh failed ({exc.returncode}): {exc.stderr.strip()}") from exc
        return completed.stdout


def _label_color(label: str) -> str:
    """Pick a stable 6-hex color for a label based on its namespace."""
    prefix = label.split(":", 1)[0]
    return {
        "type": "d73a4a",  # red-ish
        "charm": "0e8a16",  # green
        "severity": "fbca04",  # amber
    }.get(prefix, "ededed")


def _find_gh_member(archive: tarfile.TarFile) -> tarfile.TarInfo | None:
    """Find the ``bin/gh`` member inside a gh release tarball."""
    for member in archive.getmembers():
        if member.isfile() and member.name.endswith("bin/gh"):
            return member
    return None


def _parse_issue_number(gh_output: str) -> int:
    """Parse the issue number from ``gh issue create`` output.

    ``gh issue create`` prints the issue URL, e.g.
    ``https://github.com/owner/repo/issues/42``.
    """
    url = gh_output.strip().splitlines()[-1] if gh_output.strip() else ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError as exc:
        raise GitHubError(f"could not parse issue number from: {url!r}") from exc

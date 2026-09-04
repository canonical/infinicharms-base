# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""InfinityCharms base charm support package.

This package contains the baked-in capabilities of the InfinityCharms base
(template) charm:

* :mod:`infinitycharms.failure_agent` -- collect diagnostics, summarize a hook
  failure with an LLM, and file/update a GitHub issue on the monorepo.
* :mod:`infinitycharms.github_client` -- a thin wrapper around the ``gh`` CLI,
  including bootstrapping the binary on either substrate.
* :mod:`infinitycharms.updater` -- Option A hot-patch: download the latest
  matching release, unpack it, and swap in the new charm code.
* :mod:`infinitycharms.monitor` -- record every hook run and status for context.
* :mod:`infinitycharms.llm` -- an OpenAI-SDK chat client (OpenRouter by default,
  any OpenAI-compatible provider via ``base_url``).
* :mod:`infinitycharms.diagnostics` -- environment/log/context collection.
* :mod:`infinitycharms.state` -- read/write ``.infinitycharms/state.json``.
* :mod:`infinitycharms.exceptions` -- charm-specific exception types.
"""

from . import (
    diagnostics,
    exceptions,
    failure_agent,
    github_client,
    llm,
    monitor,
    state,
    updater,
)

__all__ = [
    "diagnostics",
    "exceptions",
    "failure_agent",
    "github_client",
    "llm",
    "monitor",
    "state",
    "updater",
]

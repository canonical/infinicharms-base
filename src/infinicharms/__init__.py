# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""InfiniCharms base charm support package.

This package contains the baked-in capabilities of the InfiniCharms base
(template) charm:

* :mod:`infinicharms.failure_agent` -- collect diagnostics, summarize a hook
  failure with an LLM, and file/update a GitHub issue on the monorepo.
* :mod:`infinicharms.github_client` -- a thin wrapper around the ``gh`` CLI,
  including bootstrapping the binary on either substrate.
* :mod:`infinicharms.updater` -- Option A hot-patch: download the latest
  matching release, unpack it, and swap in the new charm code.
* :mod:`infinicharms.monitor` -- record every hook run and status for context.
* :mod:`infinicharms.llm` -- a Pydantic AI chat client (OpenRouter by default,
  any OpenAI-compatible provider via ``base_url``).
* :mod:`infinicharms.diagnostics` -- environment/log/context collection.
* :mod:`infinicharms.state` -- read/write ``.infinicharms/state.json``.
* :mod:`infinicharms.exceptions` -- charm-specific exception types.
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

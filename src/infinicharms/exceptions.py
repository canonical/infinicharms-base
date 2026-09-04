# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Charm-specific exception types.

The failure agent inspects the *type* of the exception that escaped a hook to
decide which issue template/label to use. See PLAN.md §2.2.
"""

from __future__ import annotations


class InfiniCharmsError(Exception):
    """Base class for all InfiniCharms charm exceptions."""


class NotImplementedFeature(InfiniCharmsError):  # noqa: N818 - intentional public API name
    """A scaffolded feature has not been implemented yet.

    Scaffolded handlers raise this to signal "please implement X". The failure
    agent recognizes this type and files a ``type:not-implemented`` issue rather
    than a ``type:error`` issue.
    """

    def __init__(self, feature: str, detail: str | None = None):
        self.feature = feature
        self.detail = detail
        message = f"Feature not implemented: {feature}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)

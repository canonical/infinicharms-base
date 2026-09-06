# Copyright 2026 Ubuntu
# See LICENSE file for licensing details.

"""Dynamic loading of the self-updated ("evolved") charm, and per-hook guarding.

``infinicharms.updater`` extracts the latest matching GitHub release in full
into ``state_dir()/evolved`` (never touching ``$JUJU_CHARM_DIR``). This module:

1. Dynamically imports that checkout's ``src/charm.py`` and finds its
   ``ops.CharmBase`` subclass, falling back to the base template's own
   baked-in behaviour if no checkout exists yet (or it fails to load).
2. Wraps whichever class wins with a guard so that any exception escaping an
   *observed* hook or action handler is reported via a callback before it is
   re-raised, so the failure agent can attribute a failure to the exact
   handler that raised it (PLAN.md §2.1/§2.4), without waiting for the whole
   dispatch to unwind back to ``main()``.

See ``charm.main()`` for how this is wired together.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Callable

import ops

from . import state

logger = logging.getLogger(__name__)

# A fixed, non-clashing name for the dynamically-loaded module: the evolved
# checkout is very likely to *also* be named "charm" on disk (it's an updated
# copy of this very file), so it must not collide with our own already-loaded
# `charm` module in `sys.modules`.
_EVOLVED_MODULE_NAME = "infinicharms_evolved_charm"
_REPORTED_ATTR = "_infinicharms_reported"

ReportFailure = Callable[[tuple], None]


def evolved_charm_path() -> Path:
    """Return the path to the evolved checkout's charm entrypoint module."""
    return state.evolved_dir() / "src" / "charm.py"


def load_delegate_class(default: type[ops.CharmBase]) -> type[ops.CharmBase]:
    """Return the ``CharmBase`` subclass ``main()`` should actually run.

    Imports the evolved checkout's ``src/charm.py`` and returns the
    ``CharmBase`` subclass defined in it, if one exists and can be loaded.
    Falls back to ``default`` (the base template's own baked-in behaviour) if
    no checkout exists yet, or importing/finding it fails for any reason --
    a broken checkout must degrade to "no checkout", never break dispatch.
    """
    path = evolved_charm_path()
    if not path.exists():
        logger.info(
            "No evolved checkout at %s yet; using baked-in default %s", path, default.__name__
        )
        return default
    try:
        found = _import_charm_class(path)
    except Exception:  # noqa: BLE001 - a broken checkout must not break dispatch
        logger.exception("Failed to load evolved charm from %s; using baked-in default", path)
        return default
    if found is None:
        logger.warning(
            "Evolved checkout at %s defines no CharmBase subclass; using baked-in default %s",
            path,
            default.__name__,
        )
        return default
    logger.info("Using evolved charm class %s from %s", found.__qualname__, path)
    return found


def _import_charm_class(path: Path) -> type[ops.CharmBase] | None:
    """Import ``path`` as a fresh module and return its ``CharmBase`` subclass."""
    src_dir = str(path.parent)
    if src_dir not in sys.path:
        logger.info("Adding %s to sys.path for the evolved checkout's own imports", src_dir)
        sys.path.insert(0, src_dir)
    spec = importlib.util.spec_from_file_location(_EVOLVED_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        logger.warning("Could not build an import spec for %s", path)
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    logger.info("Importing evolved checkout module %s from %s", spec.name, path)
    spec.loader.exec_module(module)
    candidates = [
        obj
        for obj in vars(module).values()
        if (
            isinstance(obj, type)
            and issubclass(obj, ops.CharmBase)
            and obj is not ops.CharmBase
            and obj.__module__ == module.__name__
        )
    ]
    if len(candidates) > 1:
        logger.warning(
            "Evolved checkout at %s defines %d CharmBase subclasses (%s); using the first one",
            path,
            len(candidates),
            ", ".join(c.__qualname__ for c in candidates),
        )
    return candidates[0] if candidates else None


def mark_reported(exc: BaseException | None) -> None:
    """Flag an exception as already reported by the per-hook guard.

    ``charm.main()``'s outer safety-net ``except`` checks this (via
    ``already_reported``) so a failure the guard already reported to the
    failure agent is never reported to it a second time.
    """
    if exc is not None:
        setattr(exc, _REPORTED_ATTR, True)


def already_reported(exc: BaseException | None) -> bool:
    """Return True if ``mark_reported`` was already called for ``exc``."""
    return bool(getattr(exc, _REPORTED_ATTR, False))


def _guard_observe(framework: ops.Framework, report_failure: ReportFailure):
    """Wrap every observer registered on ``framework`` while installed.

    ``ops.Framework.observe`` requires a bound method, and dispatch re-resolves
    the handler *by name* via ``getattr(observer_obj, method_name)`` rather
    than calling back into whatever was passed to ``observe()`` (see
    ``Framework._reemit``). So the guard can't just pass a wrapping function
    to ``observe()`` -- it installs the wrapper as a uniquely-named attribute
    on the observing object itself, and registers that.
    """
    original_observe = framework.observe

    def guarded(bound_event: ops.BoundEvent, observer: types.MethodType):
        target_self = observer.__self__
        original_func = observer.__func__
        wrapper_name = f"_infinicharms_guarded_{original_func.__name__}"
        logger.info(
            "Guarding observer %s.%s for event kind %s",
            type(target_self).__qualname__,
            original_func.__name__,
            bound_event.event_kind,
        )

        def wrapper(self: object, event: ops.EventBase) -> None:
            try:
                original_func(self, event)
            except Exception as exc:
                logger.warning(
                    "Guarded handler %s.%s raised %s: %s (event kind %s); reporting before "
                    "re-raise",
                    type(self).__qualname__,
                    original_func.__name__,
                    type(exc).__name__,
                    exc,
                    bound_event.event_kind,
                )
                report_failure(sys.exc_info())
                raise

        wrapper.__name__ = wrapper_name
        bound_wrapper = types.MethodType(wrapper, target_self)
        setattr(target_self, wrapper_name, bound_wrapper)
        return original_observe(bound_event, bound_wrapper)

    framework.observe = guarded
    return original_observe


def build_guarded_charm_class(
    delegate_cls: type[ops.CharmBase], report_failure: ReportFailure
) -> type[ops.CharmBase]:
    """Return a subclass of ``delegate_cls`` that guards every observed handler.

    Every hook/action handler that ``delegate_cls.__init__`` observes (directly
    or transitively, e.g. via a helper method) is wrapped so an escaping
    exception is reported via ``report_failure(exc_info)`` *before* it is
    re-raised: Juju must still see the hook as failed (PLAN.md §2.1), the
    guard only adds attribution and lets the failure agent run immediately.
    """

    class _GuardedCharm(delegate_cls):  # type: ignore[misc]
        def __init__(self, framework: ops.Framework) -> None:
            logger.info("Constructing guarded charm around %s", delegate_cls.__qualname__)
            original_observe = _guard_observe(framework, report_failure)
            try:
                super().__init__(framework)
            finally:
                framework.observe = original_observe

    _GuardedCharm.__name__ = f"Guarded{delegate_cls.__name__}"
    _GuardedCharm.__qualname__ = _GuardedCharm.__name__
    return _GuardedCharm


def resolve_charm_class(
    default: type[ops.CharmBase], report_failure: ReportFailure
) -> type[ops.CharmBase]:
    """Resolve the (guarded) charm class ``main()`` should hand to ``ops.main()``.

    Loads the evolved checkout's charm class if one exists, else falls back to
    ``default``, and wraps whichever class wins with the per-hook guard.
    """
    delegate_cls = load_delegate_class(default)
    logger.info("Resolved charm class for this dispatch: %s", delegate_cls.__qualname__)
    return build_guarded_charm_class(delegate_cls, report_failure)

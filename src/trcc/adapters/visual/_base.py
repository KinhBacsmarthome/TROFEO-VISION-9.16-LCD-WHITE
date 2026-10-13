"""The audio visuals registry: every visual, by the key in its class line.

The same idiom as ``DEVICES`` and ``PLATFORMS``: one shared ``Registry``,
and a visual registers itself by naming its key where it is defined::

    class TrccPipes(BaseVisual, key="trcc-pipes"): ...

A new visual is one new class.  Nothing that plays visuals names one: the
Commands take a key, the platform turns it into an engine here, and the
Audio Visuals page draws its gallery and sliders from each class's ``INFO``.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from ...core.factory import Registry, Reject
from ...core.models import VisualInfo
from ...core.ports import VisualEngine, ranked_visuals

log = logging.getLogger(__name__)

VISUALS: Registry[str, type[BaseVisual]] = Registry(
    "audio visual", on_missing=Reject(OSError),
)


class BaseVisual(VisualEngine):
    """An audio visual: what it is (``INFO``), and whether this machine can
    draw it.  ``key=None`` in a class line means an intermediate base."""

    INFO: ClassVar[VisualInfo]

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate visual base, not registered",
                      cls.__name__)
            return
        log.debug("%s: registering as visual %s", cls.__name__, key)
        VISUALS.register(key)(cls)


def catalog() -> tuple[VisualInfo, ...]:
    """Every visual this machine can draw, in gallery order -- the same
    ranking the platform's gallery uses."""
    infos = ranked_visuals(VISUALS.values())
    log.info("catalog: %s", [info.kind for info in infos])
    return infos

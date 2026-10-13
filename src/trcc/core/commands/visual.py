"""Audio visuals: what the computer is playing, drawn as a panel's background.

A visual is a key in the ``VISUALS`` registry; these Commands never name
one.  ``PlayVisual`` starts it on the screencast's chain -- a reader draws on
its own thread, ``VisualDriver`` takes the newest picture each tick
(``CaptureVisualFrame``), ``SendScreencastFrame`` puts the theme's overlay on
top -- exactly as a web stream runs.  The choice is a display source in
Settings, exclusive with the others, so another source taking the panel
ends the visual within a tick, and a restart resumes it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ..errors import DeviceNotFoundError
from ..events import ErrorOccurred
from ..models import Capability, VisualInfo, VisualSpec
from ..ports import CaptureNotReady
from ..results import (
    ScreencastResult,
    VisualResult,
    VisualsResult,
    VisualTileResult,
)
from ._base import Command, Query
from .device import PlayVideo, SendScreencastFrame, StopVideo, _publish_background

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)

#: The largest gallery tile a client may ask for, in pixels a side.
MAX_TILE = 512


def _resolve(app: App, visual: str,
             preset: str) -> tuple[VisualInfo | None, Path | None, str]:
    """The visual *visual* names and its *preset* file: ``(info, path,
    problem)``, *problem* "" when both are there."""
    info = next((v for v in app.platform.visual_catalog()
                 if v.kind == visual), None)
    path = (app.platform.visual_class(visual).preset(
        app.platform.paths().visuals_dir(), preset)
        if info is not None and info.uses_presets else None)
    problem = (f"no audio visual {visual!r} on this computer" if info is None
               else f"no preset {preset!r} in the visuals' folder"
               if info.uses_presets and path is None else "")
    log.debug("_resolve: %s %r -> %s %r", visual, preset, path, problem)
    return info, path, problem


def _sound_problem(app: App) -> str:
    """Why this computer's sound cannot be heard -- "" when it can.  Every
    audio visual hears it, so this is every visual's problem."""
    made = type(app.platform).SOUND_INPUT
    problem = (f"TRCC cannot hear this computer's sound on "
               f"{app.platform.distro_name()} yet" if made is None
               else made.problem())
    log.debug("_sound_problem: %r", problem)
    return problem


@dataclass(frozen=True, slots=True)
class ListVisuals(Query[VisualsResult]):
    """Every audio visual this machine can draw, and the preset files for
    the ones that draw presets -- the Audio Visuals page's gallery."""

    def execute(self, app: App) -> VisualsResult:
        problem = _sound_problem(app)
        visuals = tuple(replace(v, problem=problem)
                        for v in app.platform.visual_catalog())
        base = app.platform.paths().visuals_dir()
        presets = tuple(name for v in visuals if v.uses_presets
                        for name in app.platform.visual_class(v.kind).presets(base))
        center = app.settings.visual_center()
        log.info("ListVisuals: %s, %d preset(s), centre %s",
                 [v.kind for v in visuals], len(presets), center)
        return VisualsResult(ok=True, visuals=visuals, presets=presets,
                             center=str(center or ""))


@dataclass(frozen=True, slots=True)
class PlayVisual(Command[VisualResult]):
    """Draw the panel's background with the audio visual *visual* (a
    ``VISUALS`` key), from what is playing.  *preset* is the preset file,
    relative to the visuals' folder, for a visual that draws presets;
    *settings* its slider values by name.  Whatever played before stops
    first.  Refused before anything changes for an unknown visual, a
    missing preset, or a panel that has not reported its size."""
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str
    visual: str
    preset: str = ""
    settings: dict[str, float] = field(default_factory=dict)

    def execute(self, app: App) -> VisualResult:
        from ...services.visual_driver import VisualDriver

        log.info("PlayVisual: %s <- %s %r %s", self.key, self.visual,
                 self.preset, self.settings)
        info, preset, problem = _resolve(app, self.visual, self.preset)
        try:
            profile = app.get(self.key).profile
        except DeviceNotFoundError as e:
            return self._refuse(app, str(e))
        if not problem and profile is None:
            problem = "the panel has not reported its size yet"
        if not problem:
            problem = _sound_problem(app)
        if problem:
            return self._refuse(app, problem)
        assert info is not None and profile is not None
        width, height = profile.resolution
        spec = VisualSpec(self.visual, width, height, preset=preset,
                          center=app.settings.visual_center(),
                          settings=dict(self.settings))
        app.dispatch(StopVideo(key=self.key))
        app.settings.set_visual(self.key, self.visual, self.preset,
                                self.settings)
        app.media.open_visual(self.key, spec, app.platform.sound_input,
                              app.platform.visual_class(self.visual))
        app.add_task(VisualDriver(app, self.key))
        _publish_background(app, self.key)
        log.info("PlayVisual: %s playing %s", self.key,
                 self.preset or self.visual)
        return VisualResult(ok=True, key=self.key, preset=self.preset,
                            playing=True,
                            message=f"{info.title} on {self.key}")

    def _refuse(self, app: App, problem: str) -> VisualResult:
        """Refused before anything changed -- and said where the user is
        looking: every open UI hears the ``ErrorOccurred``."""
        log.warning("PlayVisual: %s refused %s: %s", self.key, self.visual,
                    problem)
        app.events.publish(ErrorOccurred(message=problem, kind="visual",
                                         key=self.key))
        return VisualResult(ok=False, key=self.key, preset=self.preset,
                            message=problem)


@dataclass(frozen=True, slots=True)
class StopVisual(Command[VisualResult]):
    """End the panel's audio visual: the theme's own background returns,
    its video playing again -- the App decides, no UI reloads a theme."""
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str

    def execute(self, app: App) -> VisualResult:
        from ...services.visual_driver import task_key

        if app.settings.for_device(self.key).visual is None:
            log.info("StopVisual: %s has no audio visual", self.key)
            return VisualResult(ok=True, key=self.key,
                                message=f"no audio visual on {self.key}")
        log.info("StopVisual: ending %s's audio visual", self.key)
        app.media.close_visual(self.key)
        app.remove_task(task_key(self.key))
        app.settings.set_visual(self.key, None)
        theme = app.active_themes.get(self.key)
        video = app.themes.video_path(theme) if theme is not None else None
        if video is not None:
            app.dispatch(PlayVideo(key=self.key, path=video))
        _publish_background(app, self.key)
        return VisualResult(ok=True, key=self.key,
                            message=f"audio visual ended on {self.key}")


@dataclass(frozen=True, slots=True)
class CaptureVisualFrame(Command[ScreencastResult]):
    """One tick of an audio visual: its newest picture, onto the panel.

    ``CaptureStreamFrame``'s twin.  It ends ITSELF when another source took
    the panel -- the display sources are exclusive in Settings, so the moment
    ``visual`` stops naming this reader's visual, the reader and the driver
    stop here, within a tick.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> ScreencastResult:
        from ...services.visual_driver import task_key

        reader = app.media.visual(self.key)
        chosen = app.settings.for_device(self.key).visual
        if reader is None or chosen != reader.spec.kind:
            log.info("CaptureVisualFrame: %s's visual is no longer its "
                     "source -- ending it", self.key)
            app.media.close_visual(self.key)
            app.remove_task(task_key(self.key))
            return ScreencastResult(ok=True, key=self.key,
                                    message=f"visual on {self.key} ended")
        if self.key in app.held:
            log.debug("CaptureVisualFrame: %s held by a pushed image", self.key)
            return ScreencastResult(ok=True, key=self.key, message="held")
        try:
            frame = reader.latest()
        except CaptureNotReady as e:
            log.debug("CaptureVisualFrame: %s", e)
            return ScreencastResult(ok=True, key=self.key, message=str(e))
        except OSError as e:
            log.warning("CaptureVisualFrame: %s -- ending the audio visual", e)
            app.media.close_visual(self.key)
            app.remove_task(task_key(self.key))
            app.settings.set_visual(self.key, None)
            _publish_background(app, self.key)
            app.events.publish(ErrorOccurred(message=str(e), kind="video",
                                             key=self.key))
            return ScreencastResult(ok=False, key=self.key, message=str(e))
        return app.dispatch(SendScreencastFrame(key=self.key, frame=frame))


@dataclass(frozen=True, slots=True)
class SetVisualCenter(Command[VisualsResult]):
    """The centre picture for audio visuals that show one -- any image the
    user picks; an empty *path* goes back to TRCC's icon.  A panel playing
    such a visual picks it up when the visual next starts."""
    path: str = ""

    def execute(self, app: App) -> VisualsResult:
        log.info("SetVisualCenter: %r", self.path)
        problem = next((app.platform.visual_class(v.kind)
                        .picture_problem(Path(self.path))
                        for v in app.platform.visual_catalog() if v.uses_center),
                       "") if self.path else ""
        if problem:
            log.warning("SetVisualCenter: refused -- %s", problem)
            return VisualsResult(ok=False, message=problem)
        app.settings.set_visual_center(self.path or None)
        listed = app.dispatch(ListVisuals())
        return VisualsResult(ok=True, visuals=listed.visuals,
                             presets=listed.presets, center=listed.center,
                             message=("centre picture set" if self.path
                                      else "centre picture: TRCC's icon"))


@dataclass(frozen=True, slots=True)
class VisualTile(Query[VisualTileResult]):
    """The gallery tile for *visual* (and its *preset*): what it draws for
    noise, *size* pixels a side, as PNG -- the same whatever is playing, so
    every UI's gallery shows each visual the same way."""
    visual: str
    preset: str = ""
    size: int = 120

    def execute(self, app: App) -> VisualTileResult:
        from ...services.visual_stream import snapshot

        log.info("VisualTile: %s %r at %d", self.visual, self.preset, self.size)
        _info, preset, problem = _resolve(app, self.visual, self.preset)
        if not 16 <= self.size <= MAX_TILE:
            problem = f"a tile is 16 to {MAX_TILE} pixels a side"
        if not problem:
            spec = VisualSpec(self.visual, self.size, self.size, preset=preset,
                              center=app.settings.visual_center())
            try:
                frame = snapshot(spec, app.platform.visual_class(self.visual)())
            except OSError as e:
                problem = f"{self.visual} could not draw: {e}"
        if problem:
            log.warning("VisualTile: %s %r refused: %s", self.visual,
                        self.preset, problem)
            return VisualTileResult(ok=False, visual=self.visual,
                                    preset=self.preset, message=problem)
        png = app.renderer.encode_png(app.renderer.from_raw_rgb24(frame))
        return VisualTileResult(ok=True, visual=self.visual, preset=self.preset,
                                image=png, media_type="image/png",
                                message=f"{len(png)} bytes")

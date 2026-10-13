"""An audio visual as a live frame source -- the web stream's twin.

A web stream is ffmpeg turning a URL into frames; this is the computer's
sound turned into frames by a visual engine (projectM).  The reader runs on
its own thread -- the engine draws with OpenGL, whose context belongs to the
thread that made it -- and keeps only the NEWEST picture; the App's
``VisualDriver`` takes it each tick (``CaptureVisualFrame``) and
``SendScreencastFrame`` puts the theme's overlay on top.

The sound paces it: each picture asks for 1/VISUAL_FPS of audio, and reading
that much waits that long.  No timer, and a silent computer still draws.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace

import numpy as np

from ..core.logs import per_frame
from ..core.models import (
    SOUND_CHANNELS,
    SOUND_RATE,
    VISUAL_FPS,
    RawFrame,
    VisualSpec,
)
from ..core.ports import CaptureNotReady, SoundInput, VisualEngine

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


#: Below this a sample is silence; this long of nothing else is "hearing
#: nothing".
_QUIET = 1e-4
_SILENCE_S = 3.0


class VisualReader:
    """The visual *spec* names, drawn from what *sound* hears; the newest
    picture, on demand."""

    def __init__(self, spec: VisualSpec,
                 sound: Callable[[], SoundInput],
                 engine: Callable[[], VisualEngine]) -> None:
        self.spec = spec
        self.name = spec.preset.name if spec.preset else spec.kind
        log.info("VisualReader: %s at %dx%d", self.name, spec.width,
                 spec.height)
        self._make_sound, self._make_engine = sound, engine
        self._sound: SoundInput | None = None
        self._lock = threading.Lock()
        self._adjusted: Mapping[str, float] | None = None
        self._heard_at = time.monotonic()
        #: What the App last told the UIs -- ``CaptureVisualFrame`` keeps it.
        self.announced = True
        self._latest: RawFrame | None = None
        self._error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="trcc-visual")
        self._thread.start()

    def latest(self) -> RawFrame:
        """The newest picture; ``CaptureNotReady`` while starting,
        ``OSError`` once it has failed."""
        if self._error:
            raise OSError(self._error)
        if self._latest is None:
            raise CaptureNotReady(f"{self.name}: starting")
        frame_log.debug("VisualReader.latest: %s", self.name)
        return self._latest

    @property
    def hearing(self) -> bool:
        """Whether it heard anything above silence in the last few seconds."""
        hearing = time.monotonic() - self._heard_at < _SILENCE_S
        frame_log.debug("VisualReader.hearing: %s %s", self.name, hearing)
        return hearing

    def adjust(self, settings: Mapping[str, float]) -> None:
        """New slider values: the engine takes them on its own thread before
        its next picture, and keeps playing."""
        log.info("VisualReader.adjust: %s %s", self.name, dict(settings))
        with self._lock:
            self.spec = replace(self.spec, settings=dict(settings))
            self._adjusted = dict(settings)

    def close(self) -> None:
        """Stop listening and drawing (idempotent)."""
        log.info("VisualReader.close: %s", self.name)
        self._stop.set()
        if self._sound is not None:
            self._sound.close()          # a blocked read returns
        self._thread.join(timeout=3)

    def _run(self) -> None:
        """Hear, feed, draw -- until closed or the sound ends."""
        log.debug("_run: %s", self.name)
        engine: VisualEngine | None = None
        try:
            self._sound = sound = self._make_sound()
            sound.open()
            engine = self._make_engine()
            engine.open(self.spec)
            per_picture = SOUND_RATE // VISUAL_FPS
            pictures = 0
            while not self._stop.is_set():
                pcm = sound.read(per_picture)
                if not pcm:
                    if not self._stop.is_set():
                        self._error = "the sound stopped"
                        log.warning("VisualReader: %s -- %s",
                                    self.name, self._error)
                    break
                with self._lock:
                    adjusted, self._adjusted = self._adjusted, None
                if adjusted is not None:
                    engine.adjust(adjusted)
                if np.abs(np.frombuffer(pcm, dtype="<f4")).max(initial=0.0) > _QUIET:
                    self._heard_at = time.monotonic()
                engine.feed(pcm)
                self._latest = engine.render()
                pictures += 1
                if pictures == 1:
                    log.info("VisualReader: %s drew its first picture",
                             self.name)
        except OSError as e:
            self._error = str(e)
            log.warning("VisualReader: %s could not run -- %s",
                        self.name, e)
        finally:
            if engine is not None:
                engine.close()
            if self._sound is not None:
                self._sound.close()


#: The gallery tile: this many pictures of noise in bursts, a burst every
#: _TILE_BEAT pictures lasting _TILE_HIT -- the last picture is mid-burst.
_TILE_FRAMES = 3 * 16 + 7          # ends 7 pictures into the 4th burst
_TILE_BEAT = 16
_TILE_HIT = 8


def snapshot(spec: VisualSpec, engine: VisualEngine) -> RawFrame:
    """What *engine* draws for noise -- the gallery's tile, the same
    whatever the computer is playing.  White noise in bursts, like beats,
    since a visual that answers hits draws nothing for a steady hiss."""
    log.info("snapshot: %s at %dx%d", spec.preset.name if spec.preset
             else spec.kind, spec.width, spec.height)
    noise = np.random.default_rng(7)
    size = SOUND_RATE // VISUAL_FPS * SOUND_CHANNELS
    engine.open(spec)
    try:
        frame = engine.render()
        for i in range(_TILE_FRAMES):
            loud = 0.8 if i % _TILE_BEAT < _TILE_HIT else 0.05
            engine.feed((noise.standard_normal(size) * loud)
                        .astype("<f4").tobytes())
            frame = engine.render()
    finally:
        engine.close()
    return frame

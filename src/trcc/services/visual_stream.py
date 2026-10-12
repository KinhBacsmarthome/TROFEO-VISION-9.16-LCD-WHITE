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
from collections.abc import Callable

from ..core.logs import per_frame
from ..core.models import SOUND_RATE, VISUAL_FPS, RawFrame, VisualSpec
from ..core.ports import CaptureNotReady, SoundInput, VisualEngine

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


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

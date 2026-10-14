"""What an audio visual hears on Windows: what is playing, by WASAPI loopback.

Windows lets a program record what an output device plays -- *loopback* on
the shared-mode audio engine (Microsoft: "Loopback Recording",
https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording).
It is the canonical way: Rainmeter's AudioLevel plugin records the same way.
ffmpeg cannot (it has no WASAPI input), so this reads it through
**soundcard** by Bastian Bechtold (BSD-3-Clause,
https://github.com/bastibe/SoundCard), a small pure-Python cffi binding that
asks Windows to convert to our rate (``AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM``).

Two Windows facts shape it, both read in soundcard 0.4.6's source:

* **COM belongs to a thread.**  soundcard sets COM up when it is imported,
  on the importing thread -- and the GUI's thread already has Qt's apartment,
  where that quietly fails.  So soundcard is imported in ``open``, on the
  reader's own thread, which runs inside the platform's
  ``worker_thread_context()`` -- the COM apartment every worker gets.
* **Silence is no packets.**  A sound card with nothing playing delivers
  nothing; soundcard waits a few blocks and returns zeros, so a read stays
  paced and never hangs -- and the reader reports "hearing nothing".
"""
from __future__ import annotations

import importlib.util
import logging
from typing import Any

from ...core.logs import per_frame
from ...core.models import SOUND_CHANNELS, SOUND_RATE
from ...core.ports import SoundInput

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class LoopbackSoundInput(SoundInput):
    """The default output, as raw float32, through WASAPI loopback."""

    def __init__(self) -> None:
        log.debug("LoopbackSoundInput.__init__")
        self._recorder: Any = None

    @classmethod
    def problem(cls) -> str:
        """soundcard is what listens -- asked without importing it, so no
        COM is set up on the asking thread."""
        problem = ("" if importlib.util.find_spec("soundcard") is not None
                   else "soundcard is not installed -- it is what hears the "
                        "music for an audio visual (pip install soundcard)")
        log.debug("LoopbackSoundInput.problem: %r", problem)
        return problem

    def open(self) -> None:
        """Called on the reader's thread, inside its COM apartment: the
        default output's loopback."""
        log.info("LoopbackSoundInput.open")
        if problem := self.problem():
            raise OSError(problem)
        try:
            import soundcard  # pyright: ignore[reportMissingImports]

            speaker = soundcard.default_speaker()
            loopback = soundcard.get_microphone(id=str(speaker.id),
                                                include_loopback=True)
            recorder = loopback.recorder(samplerate=SOUND_RATE,
                                         channels=SOUND_CHANNELS)
            recorder.__enter__()
        except (RuntimeError, OSError, IndexError) as e:
            raise OSError(f"Windows would not share its sound: {e}") from e
        log.info("LoopbackSoundInput.open: listening to %s", speaker.name)
        self._recorder = recorder

    def read(self, frames: int) -> bytes:
        recorder = self._recorder
        if recorder is None:
            log.warning("LoopbackSoundInput.read: not open")
            return b""
        try:
            samples = recorder.record(numframes=frames)
        except RuntimeError as e:
            raise OSError(f"Windows stopped sharing its sound: {e}") from e
        frame_log.debug("LoopbackSoundInput.read: %d frame(s)", len(samples))
        return samples.astype("<f4").tobytes()

    def close(self) -> None:
        recorder, self._recorder = self._recorder, None
        log.info("LoopbackSoundInput.close: open=%s", recorder is not None)
        if recorder is not None:
            try:
                recorder.__exit__(None, None, None)
            except RuntimeError as e:
                log.warning("LoopbackSoundInput.close: %s", e)

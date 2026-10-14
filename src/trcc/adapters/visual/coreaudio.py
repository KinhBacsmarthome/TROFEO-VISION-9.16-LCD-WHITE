"""What an audio visual hears on a Mac: what is playing, by a Core Audio tap.

macOS 14.2 added *process taps*: a program may record what other programs
play, with no virtual audio driver to install -- Apple's sample "Capturing
system audio with Core Audio taps".  A global tap of every process's output
is what this reads, through **catap** by Stephen Betko (MIT,
https://github.com/sbetko/catap), which builds the tap and its private
aggregate device and streams the buffers to a callback.  ffmpeg cannot
(its macOS input lists input devices only).

Three Mac facts shape it:

* **Permission.**  The first recording prompts "System Audio Recording" for
  the app -- the bundle carries ``NSAudioCaptureUsageDescription``
  (``.github/workflows/macos.yml``).  Denied, macOS delivers ZEROED buffers,
  not an error, so the gallery's "hearing nothing" is how the user learns.
* **The device's own format.**  Buffers come at the output's rate (usually
  48 kHz), so each is turned into ours -- float32 stereo at ``SOUND_RATE``
  -- by :func:`to_ours`.
* **Its own thread.**  The callback runs on catap's worker; ``read`` takes
  what it left, paced: a read waits for its share and, if the buffers stop,
  answers with silence rather than hanging.

Not verified on a Mac (none in this project's reach): the tests run a
stand-in catap on Linux.
"""
from __future__ import annotations

import importlib.util
import logging
import platform
import threading
from functools import partial
from typing import Any

import numpy as np

from ...core.logs import per_frame
from ...core.models import SOUND_CHANNELS, SOUND_RATE
from ...core.ports import SoundInput

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Core Audio taps arrived in macOS 14.2.
_TAPS_FROM = (14, 2)
#: Bytes per frame of OUR format: interleaved stereo float32.
_FRAME = 4 * SOUND_CHANNELS
#: At most this much sound waits for a reader -- older is dropped.
_KEEP_S = 1.0


def to_ours(data: bytes, rate: float, channels: int, bits: int,
            is_float: bool) -> np.ndarray:
    """One buffer in the device's format -> float32 stereo at ``SOUND_RATE``,
    shape (frames, 2).  Mono is doubled; more channels keep the first two."""
    frame_log.debug("to_ours: %d bytes %g Hz %dch %d-bit %s", len(data), rate,
                    channels, bits, "float" if is_float else "int")
    if is_float and bits == 32:
        samples = np.frombuffer(data, dtype="<f4")
    elif not is_float and bits in (16, 32):
        kind = "<i2" if bits == 16 else "<i4"
        samples = np.frombuffer(data, dtype=kind) / float(2 ** (bits - 1))
    else:
        raise OSError(f"the Mac's sound comes as {bits}-bit "
                      f"{'float' if is_float else 'integer'}, which TRCC "
                      f"cannot read")
    frames = samples[: samples.size - samples.size % channels].reshape(-1, channels)
    stereo = (np.repeat(frames, 2, axis=1) if channels == 1
              else frames[:, :2]).astype(np.float32)
    if round(rate) == SOUND_RATE or len(stereo) < 2:
        return stereo
    count = max(1, round(len(stereo) * SOUND_RATE / rate))
    at = np.linspace(0.0, len(stereo) - 1, count)
    index = np.arange(len(stereo))
    return np.stack([np.interp(at, index, stereo[:, c]) for c in range(2)],
                    axis=1).astype(np.float32)


class TapSoundInput(SoundInput):
    """Every process's output on this Mac, through a Core Audio tap."""

    def __init__(self) -> None:
        log.debug("TapSoundInput.__init__")
        self._session: Any = None
        self._pending = bytearray()
        self._ready = threading.Condition()
        self._failed = ""

    @classmethod
    def problem(cls) -> str:
        """macOS 14.2 or later, and catap -- asked without importing it."""
        release = platform.mac_ver()[0]
        version = tuple(int(part) for part in release.split(".")[:2]
                        if part.isdigit())
        problem = (f"hearing what a Mac plays needs macOS 14.2 or later "
                   f"(this is {release or 'unknown'})"
                   if version < _TAPS_FROM else
                   "catap is not installed -- it is what hears the music for "
                   "an audio visual (pip install catap; Python 3.11 or later)"
                   if importlib.util.find_spec("catap") is None else "")
        log.debug("TapSoundInput.problem: %r", problem)
        return problem

    def open(self) -> None:
        log.info("TapSoundInput.open")
        if problem := self.problem():
            raise OSError(problem)
        try:
            from catap import (  # pyright: ignore[reportMissingImports]
                record_system_audio,
            )

            session = record_system_audio(on_buffer=self._on_buffer)
            session.start()
        except (ImportError, RuntimeError, OSError) as e:
            raise OSError(f"the Mac would not share its sound: {e}") from e
        self._session = session

    def _on_buffer(self, buffer: Any) -> None:
        """catap's worker thread: convert, keep, wake the reader."""
        fmt = buffer.format
        try:
            ours = to_ours(buffer.data, fmt.sample_rate, fmt.num_channels,
                           fmt.bits_per_sample, fmt.is_float).tobytes()
        except OSError as e:
            log.warning("TapSoundInput: %s", e)
            ours, self._failed = b"", str(e)
        frame_log.debug("TapSoundInput._on_buffer: %d frame(s)", buffer.frame_count)
        with self._ready:
            self._pending += ours
            over = len(self._pending) - int(_KEEP_S * SOUND_RATE) * _FRAME
            if over > 0:
                del self._pending[:over - over % _FRAME]
            self._ready.notify()

    def read(self, frames: int) -> bytes:
        """*frames* of what the Mac plays -- or silence, if it stopped."""
        if self._session is None:
            log.warning("TapSoundInput.read: not open")
            return b""
        want = frames * _FRAME
        with self._ready:
            self._ready.wait_for(partial(self._has, want),
                                 timeout=2 * frames / SOUND_RATE)
            if self._failed:
                raise OSError(self._failed)
            got = bytes(self._pending[:want])
            del self._pending[:want]
        frame_log.debug("TapSoundInput.read: %d of %d byte(s)", len(got), want)
        return got + bytes(want - len(got))

    def _has(self, want: int) -> bool:
        """Enough sound for a read -- or a failure to report.  Holds the
        lock: ``wait_for`` asks it."""
        frame_log.debug("TapSoundInput._has: %d of %d", len(self._pending), want)
        return len(self._pending) >= want or bool(self._failed)

    def close(self) -> None:
        session, self._session = self._session, None
        log.info("TapSoundInput.close: open=%s", session is not None)
        if session is not None:
            try:
                session.stop()
            except (RuntimeError, OSError) as e:
                log.warning("TapSoundInput.close: %s", e)

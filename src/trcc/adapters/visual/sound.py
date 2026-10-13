"""What an audio visual hears on Linux: what is playing, through ffmpeg.

PulseAudio -- and PipeWire through its PulseAudio server, the default on
current distributions -- gives every output a *monitor*: an input that
carries exactly what is being played.  ``@DEFAULT_MONITOR@`` names the
default output's, so this follows the speakers the user picked.  ffmpeg
already ships with TRCC (videos, web streams) and reads it with its ``pulse``
input: no new dependency, nothing recorded to disk.
"""
from __future__ import annotations

import logging
import subprocess

from ...core import toolchain
from ...core.logs import per_frame
from ...core.models import SOUND_CHANNELS, SOUND_RATE
from ...core.ports import SoundInput

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Bytes per sample frame: interleaved stereo float32.
FRAME_BYTES = 4 * SOUND_CHANNELS


class MonitorSoundInput(SoundInput):
    """The default output's monitor, as raw float32 from ffmpeg."""

    #: The PulseAudio source: the monitor of whatever output is the default.
    SOURCE = "@DEFAULT_MONITOR@"

    def __init__(self) -> None:
        log.debug("MonitorSoundInput.__init__")
        self._proc: subprocess.Popen[bytes] | None = None

    def command(self) -> list[str]:
        """ffmpeg reading the monitor live, writing samples to its stdout."""
        log.debug("MonitorSoundInput.command: %s", self.SOURCE)
        return [toolchain.executable("ffmpeg"),
                "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "pulse", "-i", self.SOURCE,
                "-f", "f32le", "-ac", str(SOUND_CHANNELS),
                "-ar", str(SOUND_RATE), "pipe:1"]

    @classmethod
    def problem(cls) -> str:
        """ffmpeg is what listens: without it there is nothing to hear."""
        problem = ("" if toolchain.present("ffmpeg")
                   else "ffmpeg is not installed -- it is what hears the "
                        "music for an audio visual")
        log.debug("MonitorSoundInput.problem: %r", problem)
        return problem

    def open(self) -> None:
        log.info("MonitorSoundInput.open: %s", self.SOURCE)
        if problem := self.problem():
            raise OSError(problem)
        self._proc = subprocess.Popen(
            self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False)

    def read(self, frames: int) -> bytes:
        proc = self._proc
        if proc is None or proc.stdout is None:
            log.warning("MonitorSoundInput.read: not open")
            return b""
        data = proc.stdout.read(frames * FRAME_BYTES)
        frame_log.debug("MonitorSoundInput.read: %d byte(s)", len(data))
        return data

    def close(self) -> None:
        proc, self._proc = self._proc, None
        log.info("MonitorSoundInput.close: running=%s",
                 proc is not None and proc.poll() is None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

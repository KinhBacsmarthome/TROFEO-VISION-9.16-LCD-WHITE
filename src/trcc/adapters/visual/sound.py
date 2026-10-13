"""What an audio visual hears on Linux: what is playing.

PulseAudio -- and PipeWire through its PulseAudio server, the default on
current distributions -- gives every output a *monitor*: an input that
carries exactly what is being played.  ``@DEFAULT_MONITOR@`` names the
default output's, so this follows the speakers the user picked.  ffmpeg
already ships with TRCC (videos, web streams) and reads it with its ``pulse``
input: no new dependency, nothing recorded to disk.

ffmpeg has no PipeWire input, so a system running PipeWire WITHOUT its Pulse
server is read with PipeWire's own ``pw-record``, capturing the default
output (``stream.capture.sink``) -- the same raw float32 on stdout.  Both
were run on PipeWire 1.6.9 (2026-10-12).  A system with only ALSA has no
monitor at all without hand-made loopback routing, so it is told so.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from ...core import toolchain
from ...core.logs import per_frame
from ...core.models import SOUND_CHANNELS, SOUND_RATE
from ...core.ports import SoundInput

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Bytes per sample frame: interleaved stereo float32.
FRAME_BYTES = 4 * SOUND_CHANNELS


def pulse_listens() -> bool:
    """Whether a PulseAudio server answers here -- PulseAudio itself or
    PipeWire's Pulse layer -- found the way libpulse finds it:
    ``PULSE_SERVER``, else the socket in the user's runtime directory."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    listens = bool(os.environ.get("PULSE_SERVER")) or (
        Path(runtime) / "pulse" / "native").exists()
    log.debug("pulse_listens: %s", listens)
    return listens


class MonitorSoundInput(SoundInput):
    """The default output's monitor, as raw float32 -- from ffmpeg where a
    PulseAudio server listens, else from PipeWire's ``pw-record``."""

    #: The PulseAudio source: the monitor of whatever output is the default.
    SOURCE = "@DEFAULT_MONITOR@"

    def __init__(self) -> None:
        log.debug("MonitorSoundInput.__init__")
        self._proc: subprocess.Popen[bytes] | None = None

    @staticmethod
    def tool() -> str:
        """``ffmpeg``, ``pw-record``, or "" when neither can listen here."""
        tool = ("ffmpeg" if pulse_listens() and toolchain.present("ffmpeg")
                else "pw-record" if toolchain.present("pw-record") else "")
        log.debug("MonitorSoundInput.tool: %r", tool)
        return tool

    def command(self) -> list[str]:
        """The tool reading the monitor live, writing samples to stdout."""
        tool = self.tool()
        log.debug("MonitorSoundInput.command: %s", tool)
        if tool == "pw-record":
            return [toolchain.executable("pw-record"),
                    "-P", "{ stream.capture.sink=true }", "--raw",
                    "--format=f32", f"--rate={SOUND_RATE}",
                    f"--channels={SOUND_CHANNELS}", "-"]
        return [toolchain.executable("ffmpeg"),
                "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "pulse", "-i", self.SOURCE,
                "-f", "f32le", "-ac", str(SOUND_CHANNELS),
                "-ar", str(SOUND_RATE), "pipe:1"]

    @classmethod
    def problem(cls) -> str:
        """Nothing to listen with: ffmpeg with a PulseAudio server (or
        PipeWire's Pulse layer), or PipeWire's pw-record."""
        problem = ("" if cls.tool() else
                   "TRCC hears the music through ffmpeg with PulseAudio (or "
                   "PipeWire's PulseAudio layer), or through PipeWire's "
                   "pw-record -- neither is here")
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

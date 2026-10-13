"""TRCC Pipes: the Windows 3D Pipes screen saver, as a stereo's equaliser.

Eight pipes run out from the centre picture like the spokes of a
wheel, one per frequency band, every 360 / 8 = 45 degrees -- the bass at
twelve o'clock, round through the mids to the treble.  Each reaches from the
centre toward the edge as far as its band is loud, a peak marker hangs at its
highest reach and sinks back as an old graphic equaliser's did, and the beat
swells every pipe.  When the music goes quiet the round ends: new materials,
and the wheel leans a new way.

**Lit the way the screen saver lit its pipes**, from its source -- the OpenGL
sample Microsoft published in the Windows NT 4.0 SDK
(``MSTOOLS/SAMPLES/OPENGL/SCRSAVE/PIPES`` and ``COMMON/MATERIAL.C``):

* its sixteen "good" materials, ambient / diffuse / specular / shininess, one
  per pipe -- the table it took from SGI's GLAUX teapots demo;
* its one white directional light at (90, 90, 150), light ambient 0.1 over a
  global ambient of 1.0, viewer at infinity -- OpenGL's fixed-function sum;
* its proportions: pipe radius 1 in a 7-unit cell, ball joints and caps
  sqrt(2) x the pipe, a 90-degree perspective from 75 units back for a scene
  56 units across -- so the near pipes are big and the far ones small;
* its turn of the scene by 9.73156 degrees at every reset -- here the
  direction the wheel leans.

The numbers are taken; no code is.  The wheel, the bands, the peaks, the beat
and the centre picture are TRCC's.  Drawn into a depth buffer as strings of
lit spheres, CPU only (numpy) -- every OS.

The centre picture is the user's -- anything; TRCC's icon until they pick
one.  It rocks toward the viewer and away, swells with the bass, and with
Fire up it burns.
"""
from __future__ import annotations

import logging
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ...core.logs import per_frame
from ...core.models import (
    SOUND_CHANNELS,
    SOUND_RATE,
    VISUAL_FPS,
    RawFrame,
    VisualAdjustment,
    VisualInfo,
    VisualSpec,
)
from ._base import BaseVisual

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: TRCC's own icon: the centre picture until the user picks one.
DEFAULT_CENTER = (Path(__file__).resolve().parents[2] / "assets" / "icons"
                  / "trcc_256x256.png")

_GRID = 13                    # cells per side
_BANDS = 8                    # one pipe per frequency band: few, so each is big
_BASS_HZ = 180.0              # the bands below this are the bass, for the beat
_RING = 1.5                   # where the pipes start: right at the centre
_ZENITH_CELLS = 6.2           # how far out a pipe reaches at full volume, in cells
_STEPS = 14                   # spheres per cell of pipe
_ROLL = 20 * VISUAL_FPS       # frames, at least, between new rounds
#: The motion below was tuned by eye at 30 frames a second.  At another rate
#: a per-frame KEEP becomes keep ** _STEP and a per-frame STEP step * _STEP,
#: so the pipes build, fall and glow at the same speed in real time.
_TUNED_FPS = 30
_STEP = _TUNED_FPS / VISUAL_FPS


def _keep(kept: float) -> float:
    """What is kept of a value per frame now, given *kept* at 30 fps."""
    frame_log.debug("_keep: %.4f at %d fps", kept, VISUAL_FPS)
    return kept ** _STEP


_FFT = 8192                   # FFT size: 5.4 Hz bins, so the 40-57 Hz band has some
_LIFT = 2.0                   # a hit this far over the band's average is the zenith
_BUILD = _STEP / 7.0          # most a pipe grows in a frame: built out, not shot
_FALL = 1 - _keep(0.88)       # how fast a pipe falls back toward the centre
_ELBOW = math.radians(45.0)   # each pipe turns this far at its one elbow ...
_BEND = (0.3, 0.5)            # ... somewhere in this part of its length
_LEAN = math.radians(30.0)    # how far the whole wheel leans, a new way each round
_PIPE_HEAT = 0.15             # how hot a pipe's flames are, the picture's being 1
_PIPE_GLOW = 0.3              # how far a pipe turns to embers, the picture's being 1

# ── the screen saver's numbers (NT 4.0 SDK, SCRSAVE/PIPES) ─────────────────
_RADIUS = 1.0 / 7.0           # STATE.CXX radius 1.0 in VIEW.CXX divSize 7.0
_BALL = math.sqrt(2.0)        # NSTATE.CXX: joints and caps are ROOT_TWO*radius
_VIEW = math.radians(90.0)    # VIEW.CXX persp.viewAngle
_DISTANCE = 75.0 / 56.0       # VIEW.CXX zTrans -75 over the half of 16 x 7
_TURN = math.radians(9.73156)  # VIEW.CXX IncrementSceneRotation
#: STATE.CXX GLInit: light 0 is white, directional, from (90, 90, 150) in eye
#: space -- y flipped here, since the sprites count rows down the screen.
_LIGHT = np.array([90.0, -90.0, 150.0], dtype=np.float32)
_LIGHT /= np.linalg.norm(_LIGHT)
_HALF = _LIGHT + np.array([0.0, 0.0, 1.0], dtype=np.float32)   # viewer at infinity
_HALF /= np.linalg.norm(_HALF)
#: The global ambient 1.0 plus light 0's own 0.1, both falling on a material's
#: ambient colour.
_AMBIENT = 1.1


@dataclass(frozen=True, slots=True)
class _Material:
    """One of the screen saver's materials, as fixed-function OpenGL lights
    it: ``ambient + diffuse * N.L + specular * (N.H) ** shininess``."""
    name: str
    ambient: np.ndarray
    diffuse: np.ndarray
    specular: np.ndarray
    shininess: float


def _material(name: str, ka: tuple[float, float, float],
              kd: tuple[float, float, float], ks: tuple[float, float, float],
              spec_exp: float) -> _Material:
    """A row of MATERIAL.C's ``teaMaterialData``, ready to light."""
    log.debug("_material: %s", name)
    return _Material(name, np.array(ka, dtype=np.float32) * _AMBIENT,
                     np.array(kd, dtype=np.float32),
                     np.array(ks, dtype=np.float32), spec_exp * 128.0)


#: MATERIAL.C's ``goodMaterials``, in its order, with its numbers.
_MATERIALS = tuple(_material(*row) for row in (
    ("emerald", (0.0215, 0.1745, 0.0215), (0.07568, 0.61424, 0.07568),
     (0.633, 0.727811, 0.633), 0.6),
    ("jade", (0.135, 0.2225, 0.1575), (0.54, 0.89, 0.63),
     (0.316228, 0.316228, 0.316228), 0.1),
    ("pearl", (0.25, 0.20725, 0.20725), (1.0, 0.829, 0.829),
     (0.296648, 0.296648, 0.296648), 0.088),
    ("ruby", (0.1745, 0.01175, 0.01175), (0.61424, 0.04136, 0.04136),
     (0.727811, 0.626959, 0.626959), 0.6),
    ("turquoise", (0.1, 0.18725, 0.1745), (0.396, 0.74151, 0.69102),
     (0.297254, 0.30829, 0.306678), 0.1),
    ("brass", (0.329412, 0.223529, 0.027451), (0.780392, 0.568627, 0.113725),
     (0.992157, 0.941176, 0.807843), 0.21794872),
    ("bronze", (0.2125, 0.1275, 0.054), (0.714, 0.4284, 0.18144),
     (0.393548, 0.271906, 0.166721), 0.2),
    ("copper", (0.19125, 0.0735, 0.0225), (0.7038, 0.27048, 0.0828),
     (0.256777, 0.137622, 0.086014), 0.1),
    ("gold", (0.24725, 0.1995, 0.0745), (0.75164, 0.60648, 0.22648),
     (0.628281, 0.555802, 0.366065), 0.4),
    ("silver", (0.19225, 0.19225, 0.19225), (0.50754, 0.50754, 0.50754),
     (0.508273, 0.508273, 0.508273), 0.4),
    ("cyan plastic", (0.0, 0.1, 0.06), (0.0, 0.50980392, 0.50980392),
     (0.50196078, 0.50196078, 0.50196078), 0.25),
    ("white plastic", (0.0, 0.0, 0.0), (0.55, 0.55, 0.55),
     (0.7, 0.7, 0.7), 0.25),
    ("yellow plastic", (0.0, 0.0, 0.0), (0.5, 0.5, 0.0),
     (0.6, 0.6, 0.5), 0.25),
    ("cyan rubber", (0.0, 0.05, 0.05), (0.4, 0.5, 0.5),
     (0.04, 0.7, 0.7), 0.078125),
    ("green rubber", (0.0, 0.05, 0.0), (0.4, 0.5, 0.4),
     (0.04, 0.7, 0.04), 0.078125),
    ("white rubber", (0.05, 0.05, 0.05), (0.5, 0.5, 0.5),
     (0.7, 0.7, 0.7), 0.078125),
))
#: The peak markers: the table's white plastic, whatever the pipe is made of.
_PEAK = _MATERIALS[11]



def band_edges() -> np.ndarray:
    """The frequency bands, spaced like pitch: 40 Hz to 14 kHz."""
    log.debug("band_edges: %d bands", _BANDS)
    return np.geomspace(40.0, 14000.0, _BANDS + 1)


class _Sprites:
    """A sphere at each pixel radius, lit in each material: coverage, the
    lit colour, and how far its surface stands toward the camera."""

    def __init__(self) -> None:
        log.debug("_Sprites.__init__")
        self._cache: dict[tuple[int, str], tuple[np.ndarray, ...]] = {}

    def get(self, radius: int, material: _Material) -> tuple[np.ndarray, ...]:
        frame_log.debug("_Sprites.get: %d %s", radius, material.name)
        key = (radius, material.name)
        if key not in self._cache:
            span = np.arange(-radius, radius + 1, dtype=np.float32) / (radius + 0.5)
            xx, yy = np.meshgrid(span, span)
            d2 = xx * xx + yy * yy
            nz = np.sqrt(np.clip(1.0 - d2, 0.0, 1.0))
            n_l = np.clip(xx * _LIGHT[0] + yy * _LIGHT[1] + nz * _LIGHT[2],
                          0.0, 1.0)
            n_h = np.clip(xx * _HALF[0] + yy * _HALF[1] + nz * _HALF[2],
                          0.0, 1.0)
            lit = (material.ambient + n_l[..., None] * material.diffuse
                   + (n_h ** material.shininess)[..., None] * material.specular)
            self._cache[key] = (d2 <= 1.0, lit, nz)
        return self._cache[key]


class _Pipe:
    """One pipe: its band, its material, its path from the centre out as
    screen points, and the index of its elbow.  ``reach`` is how much of the
    path the music shows now."""

    def __init__(self, band: int, material: _Material,
                 points: np.ndarray, elbow: int) -> None:
        log.debug("_Pipe: band %d, %s, %d point(s), elbow at %d", band,
                  material.name, len(points), elbow)
        self.band, self.material = band, material
        self.points, self.elbow = points, elbow
        self.reach = 0.0
        self.peak = 0.0          # the old graphic EQ's peak marker


class TrccPipes(BaseVisual, key="trcc-pipes"):
    """TRCC Pipes -- the first visual in the gallery."""

    INFO = VisualInfo(
        kind="trcc-pipes", title="TRCC Pipes", category="TRCC",
        uses_center=True,
        adjustments=(
            VisualAdjustment("growth", "Growth", 0.3, 3.0, 1.0),
            VisualAdjustment("thickness", "Thickness", 0.5, 4.0, 2.8),
            VisualAdjustment("center_size", "Centre picture", 0.0, 0.5, 0.3),
            VisualAdjustment("fire", "Fire", 0.0, 1.0, 0.0),
        ))

    @classmethod
    def picture_problem(cls, path: Path) -> str:
        """A centre picture must be an image Qt can read."""
        from PySide6.QtGui import QImage
        problem = ("" if path.is_file() and not QImage(str(path)).isNull()
                   else f"no picture at {path}")
        log.info("TrccPipes.picture_problem: %s -> %r", path, problem)
        return problem

    def __init__(self) -> None:
        log.debug("TrccPipes.__init__")
        self._sprites = _Sprites()
        self._edges = band_edges()
        self._band_peak = np.full(_BANDS, 1e-3)
        self._bands = np.zeros(_BANDS)
        self._band_avg = np.zeros(_BANDS)
        self._bass_bands = max(1, int(np.count_nonzero(self._edges[1:] <= _BASS_HZ)))
        self._bass = self._beat = 0.0
        self._peak_bass = 1e-3
        self._tick = 0.0
        self._turn = random.uniform(0.0, 2 * math.pi)
        self._w = self._h = 0
        self._center: Any = None          # the picture, as loaded
        self._shown: Any = None           # ... as drawn: red-hot with Fire up
        self._growth = self._thickness = 1.0
        self._center_size = 0.3
        self._fire = 0.0

    # ── VisualEngine ─────────────────────────────────────────────────

    def open(self, spec: VisualSpec) -> None:
        log.info("TrccPipes.open: %dx%d centre=%s settings=%s", spec.width,
                 spec.height, spec.center, dict(spec.settings))
        self._w, self._h = spec.width, spec.height
        self.adjust(spec.settings)
        picture = spec.center if spec.center is not None else DEFAULT_CENTER
        if picture.is_file():
            from PySide6.QtGui import QImage
            image = QImage(str(picture))
            if image.isNull():
                log.warning("TrccPipes.open: %s is not a picture", picture)
            else:
                self._center = image
        self._prepare_center()
        self._new_round()

    def adjust(self, settings: Mapping[str, float]) -> None:
        """The sliders: each at *settings*' value, else its default."""
        values = {a.name: a.default for a in self.INFO.adjustments}
        values.update(settings)
        log.info("TrccPipes.adjust: %s", values)
        self._growth = float(values["growth"])
        self._thickness = float(values["thickness"])
        self._center_size = float(values["center_size"])
        fire, self._fire = self._fire, float(values["fire"])
        if self._center is not None and fire != self._fire:
            self._prepare_center()

    def feed(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype="<f4")
        frame_log.debug("TrccPipes.feed: %d sample(s)", samples.size)
        if samples.size < SOUND_CHANNELS * 64:
            return
        mono = samples[: samples.size - samples.size % SOUND_CHANNELS]
        mono = mono.reshape(-1, SOUND_CHANNELS).mean(axis=1)
        size = max(_FFT, mono.size)
        spectrum = np.abs(np.fft.rfft(mono, size))
        hz = np.fft.rfftfreq(size, 1.0 / SOUND_RATE)
        levels = np.zeros(_BANDS)
        for i in range(_BANDS):
            inside = (hz >= self._edges[i]) & (hz < self._edges[i + 1])
            if inside.any():
                levels[i] = float(spectrum[inside].mean())
        # Each band against its own recent peak, so the treble is not
        # drowned by the bass and a quiet song still grows pipes.
        self._band_peak = np.maximum(levels, self._band_peak * _keep(0.996))
        held = _keep(0.55)
        self._bands = (held * self._bands
                       + (1 - held) * levels / np.maximum(self._band_peak, 1e-6))
        # ... and its recent average, about a second: a pipe goes out when
        # its band rises over that -- a hit -- not merely because it is loud.
        usual = _keep(0.96)
        self._band_avg = usual * self._band_avg + (1 - usual) * self._bands
        bass = float(levels[:self._bass_bands].mean())
        self._peak_bass = max(bass, self._peak_bass * _keep(0.995))
        bass_now = bass / self._peak_bass
        self._beat = (max(bass_now - self._bass, 0.0) * 2.5
                      + self._beat * _keep(0.8))
        kept = _keep(0.6)
        self._bass = kept * self._bass + (1 - kept) * bass_now

    def render(self) -> RawFrame:
        frame_log.debug("TrccPipes.render: frame %d", self._frame)
        self._frame += 1
        self._follow_the_music()
        self._redraw()
        gain = 1.0 + 0.35 * min(self._beat, 1.0)
        out = self._color * gain
        placed = (self._center_placement()
                  if self._shown is not None and self._center_size > 0 else None)
        if self._fire > 0:
            # The pipes burn as the picture does: red-hot, flames rising off
            # every pipe and off the picture's outline.
            pipes = np.isfinite(self._depth)
            seed = pipes[::2, ::2].astype(np.float32) * _PIPE_HEAT
            if placed is not None:
                seed = np.maximum(seed, self._picture_outline(placed))
            burning = self._burn(out[pipes], _PIPE_GLOW)
            out = np.maximum(out, self._flames(seed))
            out[pipes] = burning            # the pipe itself, in front
        frame = np.clip(out * 255.0, 0, 255).astype(np.uint8)
        data = (self._with_center(frame, placed) if placed is not None
                else frame.tobytes())
        return RawFrame(data=data, width=self._w, height=self._h)

    def close(self) -> None:
        log.info("TrccPipes.close")
        self._center = self._shown = None

    # ── the screen saver ─────────────────────────────────────────────

    def _new_round(self) -> None:
        """New materials -- eight of the sixteen, dealt one to a pipe, as the screen
        saver picked one at random per pipe -- and the wheel leaning a new
        way, turned on as the screen saver turned its scene each reset."""
        self._frame = self._rolled = getattr(self, "_frame", 0)
        w, h = self._w, self._h
        self._color = np.zeros((h, w, 3), dtype=np.float32)
        self._depth = np.full((h, w), np.inf, dtype=np.float32)
        self._turn += _TURN
        axis = np.array([math.cos(self._turn), math.sin(self._turn), 0.0])
        cross = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]],
                          [-axis[1], axis[0], 0.0]])
        self._rot = (math.cos(_LEAN) * np.eye(3) + math.sin(_LEAN) * cross
                     + (1 - math.cos(_LEAN)) * np.outer(axis, axis)
                     ).astype(np.float32)
        self._dist = _GRID / 2 * _DISTANCE
        self._focal = min(w, h) / 2 / math.tan(_VIEW / 2)
        materials = random.sample(_MATERIALS, _BANDS)
        self._pipes = [self._spoke(band, materials[band])
                       for band in range(_BANDS)]
        log.info("TrccPipes: new round, leaning toward %.0f deg, %s",
                 math.degrees(self._turn) % 360, [m.name for m in materials])

    def _spoke(self, band: int, material: _Material) -> _Pipe:
        """A pipe from the centre out at its band's angle -- 360 / 8 =
        every 45 degrees, the bass at twelve o'clock -- straight, then one
        elbow of 45 degrees left or right at half its length or less, then
        straight to the zenith; as screen points, with the elbow's index."""
        frame_log.debug("_spoke: band %d", band)
        mid = (_GRID - 1) / 2
        angle = band / _BANDS * 2 * math.pi - math.pi / 2
        turned = angle + random.choice((-1, 1)) * _ELBOW
        along = np.array([math.cos(angle), -math.sin(angle), 0.0], dtype=np.float32)
        after = np.array([math.cos(turned), -math.sin(turned), 0.0], dtype=np.float32)
        start = np.full(3, mid, dtype=np.float32) + along * _RING
        length = _ZENITH_CELLS - _RING
        elbow = round(random.uniform(*_BEND) * length * _STEPS)
        corner = start + along * (elbow / _STEPS)
        points = [start + along * (i / _STEPS) if i <= elbow
                  else corner + after * ((i - elbow) / _STEPS)
                  for i in range(int(length * _STEPS) + 1)]
        projected = np.array([self._project(p) for p in points], dtype=np.float32)
        return _Pipe(band, material, projected, elbow)

    def _follow_the_music(self) -> None:
        """Each pipe reaches out as far as its band rises over its own
        average -- built out from the centre a piece at a time, as the screen
        saver laid its pipes, then falling back.  A new round comes in a
        quiet moment, no more often than every ``_ROLL`` frames."""
        frame_log.debug("_follow_the_music")
        for pipe in self._pipes:
            now = float(self._bands[pipe.band])
            usual = float(self._band_avg[pipe.band])
            lift = (now - usual) / max(1.0 - usual, 0.15)
            target = min(max(lift * _LIFT * self._growth, 0.0), 1.0)
            if target > pipe.reach:
                pipe.reach = min(target, pipe.reach + _BUILD)
            else:
                pipe.reach += (target - pipe.reach) * _FALL
            # The peak marker hangs at the highest reach, then sinks back --
            # as an old stereo's graphic equaliser did.
            pipe.peak = max(pipe.reach, pipe.peak - 0.012 * _STEP)
        quiet = all(p.reach < 0.12 for p in self._pipes)
        if quiet and self._frame - self._rolled > _ROLL:
            self._new_round()

    def _project(self, point: np.ndarray) -> tuple[float, float, float]:
        frame_log.debug("_project")
        q = self._rot @ (point - (_GRID - 1) / 2.0)
        z = float(q[2]) + self._dist
        return (self._w / 2 + self._focal * float(q[0]) / z,
                self._h / 2 - self._focal * float(q[1]) / z, z)

    def _redraw(self) -> None:
        """Every pipe as far as it reaches, into a fresh depth buffer --
        swollen by the beat: a cylinder to the elbow and one past it, the
        screen saver's balls on the root, the elbow and the tip, and a ball
        for the peak marker."""
        frame_log.debug("_redraw: %d pipe(s)", len(self._pipes))
        self._color.fill(0.0)
        self._depth.fill(np.inf)
        radius = (_RADIUS * self._thickness
                  * (1.0 + 0.45 * min(self._beat, 1.0)))
        for pipe in self._pipes:
            tip = round(pipe.reach * (len(pipe.points) - 1))
            bend = min(pipe.elbow, tip)
            self._cylinder(pipe.points[0], pipe.points[bend], radius,
                           pipe.material)
            if tip > pipe.elbow:
                self._cylinder(pipe.points[pipe.elbow], pipe.points[tip],
                               radius, pipe.material)
            for j in {0, bend, tip}:
                sx, sy, z = pipe.points[j]
                self._stamp(float(sx), float(sy), float(z), radius * _BALL,
                            pipe.material)
            top = round(pipe.peak * (len(pipe.points) - 1))
            if top > tip:
                sx, sy, z = pipe.points[top]
                self._stamp(float(sx), float(sy), float(z), radius * _BALL,
                            _PEAK)

    def _cylinder(self, start: np.ndarray, end: np.ndarray, radius: float,
                  material: _Material) -> None:
        """A straight pipe from *start* to *end* (screen x, y and depth) into
        the depth buffer -- one cylinder, as the screen saver's
        ``PIPE_OBJECT``, its near end wider than its far end, lit across its
        width the way a tube is."""
        x0, y0, z0 = (float(v) for v in start)
        x1, y1, z1 = (float(v) for v in end)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        frame_log.debug("_cylinder: %.0f,%.0f -> %.0f,%.0f", x0, y0, x1, y1)
        if length < 0.5:
            return
        r0, r1 = self._focal * radius / z0, self._focal * radius / z1
        pad = max(r0, r1) + 1.0
        bx0, by0 = max(int(min(x0, x1) - pad), 0), max(int(min(y0, y1) - pad), 0)
        bx1 = min(int(max(x0, x1) + pad) + 1, self._w)
        by1 = min(int(max(y0, y1) + pad) + 1, self._h)
        if bx0 >= bx1 or by0 >= by1:
            return
        ys, xs = np.ogrid[by0:by1, bx0:bx1]
        px, py = xs.astype(np.float32) - x0, ys.astype(np.float32) - y0
        t = (px * dx + py * dy) / (length * length)
        across = (py * dx - px * dy) / length / (r0 + (r1 - r0) * t)
        depth = z0 + (z1 - z0) * t
        zbuf = self._depth[by0:by1, bx0:bx1]
        inside = (t >= 0.0) & (t <= 1.0) & (np.abs(across) < 1.0)
        nz = np.sqrt(np.clip(1.0 - across * across, 0.0, 1.0))
        depth = depth - nz * radius
        hit = inside & (depth < zbuf)
        if not hit.any():
            return
        o, n_z = across[hit], nz[hit]
        nx, ny = -dy / length * o, dx / length * o
        n_l = np.clip(nx * _LIGHT[0] + ny * _LIGHT[1] + n_z * _LIGHT[2], 0.0, 1.0)
        n_h = np.clip(nx * _HALF[0] + ny * _HALF[1] + n_z * _HALF[2], 0.0, 1.0)
        self._color[by0:by1, bx0:bx1][hit] = (
            material.ambient + n_l[:, None] * material.diffuse
            + (n_h ** material.shininess)[:, None] * material.specular)
        zbuf[hit] = depth[hit]

    def _stamp(self, sx: float, sy: float, z: float, radius: float,
               material: _Material) -> None:
        """One lit sphere into the depth buffer -- behind what is nearer."""
        px = max(1, round(self._focal * radius / z))
        frame_log.debug("_stamp: %.0f,%.0f r%d", sx, sy, px)
        mask, lit, nz = self._sprites.get(px, material)
        x0, y0 = round(sx) - px, round(sy) - px
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x0 + 2 * px + 1, self._w), min(y0 + 2 * px + 1, self._h)
        if cx0 >= cx1 or cy0 >= cy1:
            return
        sl = (slice(cy0 - y0, cy1 - y0), slice(cx0 - x0, cx1 - x0))
        depth = z - nz[sl] * radius
        zbuf = self._depth[cy0:cy1, cx0:cx1]
        hit = mask[sl] & (depth < zbuf)
        if not hit.any():
            return
        self._color[cy0:cy1, cx0:cx1][hit] = lit[sl][hit]
        zbuf[hit] = depth[hit]

    def _with_center(self, frame: np.ndarray, placed: Any) -> bytes:
        """The centre picture drawn onto the finished *frame* by Qt --
        already red-hot with Fire up (``_shown``)."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        frame_log.debug("_with_center: fire=%.2f", self._fire)
        transform, size = placed
        image = QImage(frame.data, self._w, self._h, 3 * self._w,
                       QImage.Format.Format_RGB888)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setTransform(transform)
        painter.drawImage(QRectF(-size / 2, -size / 2, size, size), self._shown)
        painter.end()
        return frame.tobytes()

    def _burn(self, rgb: np.ndarray, share: float) -> np.ndarray:
        """*rgb* glowing red-hot, as far as Fire is up times *share*: its
        brightness kept, its colour turned to embers."""
        frame_log.debug("_burn: %d pixel(s) x%.2f", rgb.size // 3, share)
        heat = self._fire * share
        hot = np.array([1.0, 0.22, 0.05], dtype=np.float32)
        glow = rgb.mean(axis=-1, keepdims=True) * 0.7 + 0.3
        return rgb * (1 - heat) + hot * glow * heat * 1.25

    def _center_placement(self) -> tuple[Any, float]:
        """This frame's place for the picture: rocking toward the viewer and
        away, swelling with the bass -- a transform, and the picture's size."""
        from PySide6.QtGui import QTransform
        self._tick += 1.6 / VISUAL_FPS
        tilt = 0.35 + 0.65 * abs(math.cos(self._tick))
        size = (min(self._w, self._h) * self._center_size
                * (1.0 + 0.22 * min(self._bass, 1.5)))
        frame_log.debug("_center_placement: tilt %.2f size %.0f", tilt, size)
        return (QTransform().translate(self._w / 2, self._h / 2).scale(tilt, 1.0),
                size)

    def _picture_outline(self, placed: tuple[Any, float]) -> np.ndarray:
        """Where the picture covers, at the fire's half resolution -- what
        the flames rise from."""
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QImage, QPainter, QTransform
        transform, size = placed
        height, width = (self._h + 1) // 2, (self._w + 1) // 2
        mask = QImage(width, height, QImage.Format.Format_Alpha8)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.setTransform(transform * QTransform().scale(0.5, 0.5))
        painter.drawImage(QRectF(-size / 2, -size / 2, size, size), self._shown)
        painter.end()
        alpha = np.frombuffer(mask.constBits(), dtype=np.uint8)
        frame_log.debug("_picture_outline: %dx%d", width, height)
        return alpha.reshape(height, mask.bytesPerLine())[:, :width] / np.float32(255)

    def _prepare_center(self) -> None:
        """The picture as it is drawn: red-hot as far as Fire is up --
        worked out when the picture or the Fire slider changes, never per
        frame."""
        from PySide6.QtGui import QImage
        if self._center is None:
            log.debug("_prepare_center: no picture")
            self._shown = None
            return
        if self._fire <= 0:
            log.debug("_prepare_center: as it is")
            self._shown = self._center
            return
        source = self._center.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = source.width(), source.height()
        rgba = np.frombuffer(source.constBits(), dtype=np.uint8).reshape(
            h, source.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
        hot = self._burn(rgba[..., :3] / np.float32(255), 1.0)
        burned = np.concatenate(
            [np.clip(hot * 255, 0, 255).astype(np.uint8), rgba[..., 3:]], axis=2)
        self._burned_pixels = np.ascontiguousarray(burned)
        self._shown = QImage(self._burned_pixels.data, w, h, 4 * w,
                             QImage.Format.Format_RGBA8888).copy()
        log.debug("_prepare_center: %dx%d red-hot at fire %.2f", w, h, self._fire)

    def _flames(self, seed: np.ndarray) -> np.ndarray:
        """The demoscene fire: heat from the picture's outline rises, spreads
        and cools, coloured black, red, orange, yellow, white -- worked at
        half resolution (*seed* is), a quarter of the work."""
        frame_log.debug("_flames")
        heat = getattr(self, "_heat", None)
        if heat is None or heat.shape != seed.shape:
            heat = np.zeros_like(seed)
        flicker = np.random.random(seed.shape).astype(np.float32)
        heat = np.maximum(heat, seed * (0.6 + 0.4 * flicker)
                          * (0.7 + 0.5 * min(self._bass, 1.3)))
        lift = max(1, round(2 * _STEP))
        rise = np.roll(heat, -lift, axis=0)
        rise[-lift:] = 0
        spread = (rise + np.roll(rise, 1, axis=1) + np.roll(rise, -1, axis=1)
                  + np.roll(rise, -1, axis=0)) / 4.0
        self._heat = heat = spread * (0.955 - 0.03 * flicker) ** _STEP
        t = np.clip(heat * 1.4, 0.0, 1.0)[..., None] * self._fire
        colour = np.concatenate([np.clip(t * 3.0, 0, 1), np.clip(t * 3.0 - 1, 0, 1),
                                 np.clip(t * 3.0 - 2, 0, 1)], axis=2)
        # Simulated at half resolution -- flames are blurry -- shown at full.
        return colour.repeat(2, axis=0).repeat(2, axis=1)[:self._h, :self._w]

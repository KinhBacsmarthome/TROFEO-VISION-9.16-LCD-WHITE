"""Audio visuals: the registry, TRCC Pipes, and the Commands every UI uses.

A visual is a key in ``VISUALS``; ``PlayVisual`` runs it on the screencast's
chain (reader -> ``VisualDriver`` -> ``CaptureVisualFrame`` ->
``SendScreencastFrame``), exactly as a web stream runs.  The stand-in
platform hears a scripted beat and scripts MilkDrop, so no test reaches the
host's sound card or GPU.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PySide6.QtGui import QImage

from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform, ScriptedSound
from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.visual import VISUALS, catalog
from trcc.adapters.visual import projectm as projectm_module
from trcc.adapters.visual.pipes import TrccPipes
from trcc.app import App
from trcc.core.commands import (
    CaptureVisualFrame,
    ConnectDevice,
    LcdSnapshot,
    ListVisuals,
    PlayVisual,
    RestoreDeviceState,
    SetMediaPlayer,
    SetVisualCenter,
    SetVisualSettings,
    StopVisual,
    VisualTile,
)
from trcc.core.events import ErrorOccurred
from trcc.core.models import SOUND_RATE, VISUAL_FPS, RawFrame, VisualSpec
from trcc.core.ports import CaptureNotReady
from trcc.services.media import MediaService
from trcc.services.visual_driver import task_key

_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}


# ── The registry ─────────────────────────────────────────────────────

def test_every_visual_registers_by_the_key_in_its_class_line() -> None:
    assert list(VISUALS) == ["trcc-pipes", "milkdrop"]     # Pipes first
    assert all(VISUALS[k].INFO.kind == k for k in VISUALS)


class _FakeProjectM:
    """libprojectM that starts (or not) without OpenGL."""

    def __init__(self, starts: bool) -> None:
        self.starts, self.destroyed = starts, 0

    def projectm_create(self) -> int:
        return 7 if self.starts else 0

    def projectm_destroy(self, handle: int) -> None:
        self.destroyed += 1


class _FakeSurface:
    closed = 0

    def __init__(self, width: int, height: int) -> None:
        pass

    def close(self) -> None:
        _FakeSurface.closed += 1


@pytest.mark.parametrize(("installed", "loads", "starts", "offered"), [
    (False, True, True, False),      # no libprojectM 4
    (True, False, True, False),      # installed, but no OpenGL to draw with
    (True, True, False, False),      # e.g. a GLES build in a GL core context
    (True, True, True, True),
])
def test_milkdrop_is_offered_only_where_projectm_can_draw(
        monkeypatch: pytest.MonkeyPatch, installed: bool, loads: bool,
        starts: bool, offered: bool) -> None:
    """MUTATION CHECK: return ``find_library(...) is not None`` again."""
    lib = _FakeProjectM(starts)

    def load() -> _FakeProjectM:
        if not loads:
            raise OSError("OpenGL (EGL) is not available")
        return lib

    monkeypatch.setattr(projectm_module.MilkdropVisual, "_draws", None)
    monkeypatch.setattr(projectm_module.ctypes.util, "find_library",
                        lambda name: "libprojectM-4.so.4" if installed else None)
    monkeypatch.setattr(projectm_module, "load_projectm", load)
    monkeypatch.setattr(projectm_module, "_EglSurface", _FakeSurface)
    kinds = [info.kind for info in catalog()]
    assert kinds == (["trcc-pipes", "milkdrop"] if offered else ["trcc-pipes"])
    assert lib.destroyed == (1 if offered else 0)


# ── TRCC Pipes ───────────────────────────────────────────────────────

def _pipes(**settings: float) -> TrccPipes:
    engine = TrccPipes()
    engine.open(VisualSpec("trcc-pipes", 160, 160, settings=settings))
    return engine


def _lit(frame: RawFrame) -> int:
    pixels = np.frombuffer(frame.data, dtype=np.uint8).reshape(-1, 3)
    return int((pixels.max(axis=1) > 40).sum())


def _beat_pcm(seconds: float) -> list[bytes]:
    sound = ScriptedSound()
    sound.open()
    per = SOUND_RATE // VISUAL_FPS
    chunks = []
    for _ in range(int(seconds * VISUAL_FPS)):
        chunks.append(_no_sleep_read(sound, per))
    return chunks


def _no_sleep_read(sound: ScriptedSound, frames: int) -> bytes:
    """The scripted beat without its real-time pacing."""
    import tests.mock_platform as mp
    real = mp.time.sleep
    mp.time.sleep = lambda s: None            # type: ignore[assignment]
    try:
        return sound.read(frames)
    finally:
        mp.time.sleep = real                  # type: ignore[assignment]


def test_pipes_reach_out_with_the_music_and_draw_back_in_silence() -> None:
    """Each pipe reaches along its path as far as its band is loud; in
    silence they pull back to their roots at the centre.

    MUTATION CHECK: make ``_follow_the_music`` ignore the bands."""
    engine = _pipes(center_size=0.0)
    reached = 0
    for chunk in _beat_pcm(2.0):
        engine.feed(chunk)
        reached = max(reached, _lit(engine.render()))
    for _ in range(90):
        engine.feed(b"\x00" * (SOUND_RATE // VISUAL_FPS) * 8)
        silent = engine.render()
    assert reached > 3 * max(_lit(silent), 1)
    assert all(pipe.reach < 0.05 for pipe in engine._pipes)  # pyright: ignore[reportPrivateUsage]


def test_the_centre_picture_sits_in_the_middle_and_can_burn() -> None:
    plain = _pipes(center_size=0.4)
    frame = plain.render()
    middle = (80 * 160 + 80) * 3
    assert sum(frame.data[middle:middle + 3]) > 60
    burning = _pipes(center_size=0.4, fire=1.0)
    for _ in range(20):
        frame = burning.render()
    above = np.frombuffer(frame.data, dtype=np.uint8).reshape(160, 160, 3)[20:50, 60:100]
    hot = above[above.max(axis=2) > 60]
    assert hot.size and (hot[:, 0] >= hot[:, 2]).all()      # flames: red over blue


# ── The Commands, on the App ─────────────────────────────────────────

class _FakeReader:
    """What ``VisualReader`` promises, scripted -- no thread, no sound."""

    def __init__(self, spec: VisualSpec, sound: Any, engine: Any) -> None:
        self.spec, self.name, self.closed = spec, spec.kind, False
        self.fail, self.ready = "", True

    def latest(self) -> RawFrame:
        if self.fail:
            raise OSError(self.fail)
        if not self.ready:
            raise CaptureNotReady("starting")
        return RawFrame(data=b"\x40" * (self.spec.width * self.spec.height * 3),
                        width=self.spec.width, height=self.spec.height)

    def close(self) -> None:
        self.closed = True

    def adjust(self, settings: Any) -> None:
        self.adjusted = dict(settings)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    monkeypatch.setattr(MediaService, "visual_reader", _FakeReader)
    tasks: list[str] = []
    monkeypatch.setattr(App, "add_task", lambda self, task: tasks.append(task.key))
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.tasks = tasks                        # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    yield app
    app.close()


def _reader(app: App) -> Any:
    return app.media.visual(_KEY)


def _wire(app: App) -> list[Any]:
    return app.devices[_KEY]._transport.sent   # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]


def _preset(app: App, name: str = "Fractal/one.milk") -> str:
    path = app.platform.paths().visuals_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[preset00]\n")
    return name


def test_the_gallery_lists_every_visual_and_the_preset_files(app: App) -> None:
    _preset(app)
    listed = app.dispatch(ListVisuals())
    assert [v.kind for v in listed.visuals] == ["trcc-pipes", "milkdrop"]
    assert [a.name for a in listed.visuals[0].adjustments] == [
        "growth", "thickness", "center_size", "fire"]
    assert listed.presets == ("Fractal/one.milk",)


def test_a_visual_plays_as_the_panel_s_background(app: App) -> None:
    result = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                                     settings={"fire": 1.0}))
    assert (result.ok, result.playing) == (True, True)
    spec = _reader(app).spec
    profile = app.devices[_KEY].profile
    assert profile is not None
    assert (spec.width, spec.height) == profile.resolution
    assert dict(spec.settings) == {"fire": 1.0}
    s = app.settings.for_device(_KEY)
    assert (s.visual, s.visual_settings) == ("trcc-pipes", {"fire": 1.0})
    assert app.dispatch(LcdSnapshot(key=_KEY)).display_source == "visual"
    assert app.tasks == [task_key(_KEY)]       # type: ignore[attr-defined]
    observer = app._render_observer             # pyright: ignore[reportPrivateUsage]
    assert observer._cadence_owner(_KEY) == "visual"


@pytest.mark.parametrize(("visual", "preset", "problem"), [
    ("disco", "", "no audio visual 'disco' on this computer"),
    ("milkdrop", "", "no preset '' in the visuals' folder"),
    ("milkdrop", "../../escape.milk", "no preset '../../escape.milk' in the "
                                      "visuals' folder"),
])
def test_a_visual_that_cannot_play_changes_nothing(
        app: App, visual: str, preset: str, problem: str) -> None:
    """MUTATION CHECK: drop the is_under check in ``_preset_path``."""
    escape = app.platform.paths().visuals_dir().parent.parent / "escape.milk"
    escape.parent.mkdir(parents=True, exist_ok=True)
    escape.write_text("[preset00]\n")
    result = app.dispatch(PlayVisual(key=_KEY, visual=visual, preset=preset))
    assert (result.ok, result.message) == (False, problem)
    assert _reader(app) is None
    assert app.settings.for_device(_KEY).visual is None


def test_a_milkdrop_preset_plays_from_the_visuals_folder(app: App) -> None:
    name = _preset(app)
    assert app.dispatch(PlayVisual(key=_KEY, visual="milkdrop", preset=name)).ok
    assert _reader(app).spec.preset == app.platform.paths().visuals_dir() / name


def test_a_tick_puts_the_visual_s_picture_on_the_panel(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    sent = len(_wire(app))
    result = app.dispatch(CaptureVisualFrame(key=_KEY))
    assert result.ok, result.message
    assert len(_wire(app)) > sent


def test_another_source_ends_the_visual_within_a_tick(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    reader = _reader(app)
    app.settings.set_media_player_uri(_KEY, "https://example.test/a.m3u8")
    assert app.settings.for_device(_KEY).visual is None       # exclusive
    app.dispatch(CaptureVisualFrame(key=_KEY))
    assert reader.closed and _reader(app) is None


def test_a_failed_visual_ends_and_says_why(app: App) -> None:
    heard: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, heard.append)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    _reader(app).fail = "the sound stopped"
    assert not app.dispatch(CaptureVisualFrame(key=_KEY)).ok
    assert [e.message for e in heard] == ["the sound stopped"]
    assert _reader(app) is None
    assert app.settings.for_device(_KEY).visual is None


def test_starting_up_is_not_a_failure(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    _reader(app).ready = False
    assert app.dispatch(CaptureVisualFrame(key=_KEY)).ok
    assert _reader(app) is not None


def test_stopping_gives_the_panel_back_to_its_theme(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    reader = _reader(app)
    assert app.dispatch(StopVisual(key=_KEY)).ok
    assert reader.closed and _reader(app) is None
    assert app.dispatch(LcdSnapshot(key=_KEY)).display_source == "background"


def test_a_restart_resumes_the_visual_with_its_settings(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"growth": 2.0}))
    app.media.close_visual(_KEY)                 # what a restart leaves
    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok
    assert dict(_reader(app).spec.settings) == {"growth": 2.0}


def test_a_media_player_replaces_the_visual(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    app.dispatch(SetMediaPlayer(key=_KEY, uri="https://example.test/a.m3u8"))
    assert app.settings.for_device(_KEY).visual is None


def test_the_centre_picture_is_the_user_s(app: App, tmp_path: Path) -> None:
    missing = app.dispatch(SetVisualCenter(path=str(tmp_path / "none.png")))
    assert (missing.ok, missing.message) == (
        False, f"no picture at {tmp_path / 'none.png'}")
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"png")
    assert not app.dispatch(SetVisualCenter(path=str(junk))).ok
    picture = tmp_path / "bob.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))
    assert app.dispatch(SetVisualCenter(path=str(picture))).center == str(picture)
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    assert _reader(app).spec.center == picture
    assert app.dispatch(SetVisualCenter(path="")).center == ""


def test_the_api_plays_lists_and_confines_the_centre_picture(
        app: App, tmp_path_factory: pytest.TempPathFactory) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    client = loopback_client(build_app(app))
    played = client.post(f"/devices/{_KEY}/display/visual",
                         json={"visual": "trcc-pipes", "sliders": {"fire": 1.0}})
    assert (played.status_code, played.json()["playing"]) == (200, True)
    assert dict(_reader(app).spec.settings) == {"fire": 1.0}
    kinds = [v["kind"] for v in client.get("/display/visuals").json()["visuals"]]
    assert kinds[0] == "trcc-pipes"

    stranger = tmp_path_factory.mktemp("not_trcc") / "real.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(stranger))
    outside = client.post("/display/visual-center", json={"path": str(stranger)})
    assert outside.status_code == 400            # a real picture, not TRCC's
    picture = app.platform.paths().user_content_dir() / "bob.png"
    picture.parent.mkdir(parents=True, exist_ok=True)
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))
    inside = client.post("/display/visual-center", json={"path": str(picture)})
    assert (inside.status_code, inside.json()["center"]) == (200, str(picture.resolve()))

    stopped = client.post(f"/devices/{_KEY}/display/visual", json={"visual": ""})
    assert stopped.status_code == 200
    assert app.settings.for_device(_KEY).visual is None



def test_the_gallery_lists_trcc_pipes_first_and_one_tile_per_preset() -> None:
    """Both windows build their tiles here, so they cannot list differently."""
    from trcc.core.models import VisualInfo
    from trcc.core.results import VisualsResult
    from trcc.ui.presentation.visual_gallery import VisualItem, gallery_items

    listed = VisualsResult(ok=True, visuals=(
        VisualInfo("trcc-pipes", "TRCC Pipes", "TRCC"),
        VisualInfo("milkdrop", "MilkDrop", "MilkDrop", uses_presets=True)),
        presets=("geiss/Swirl.milk", "Flexi - Rain.milk"))
    assert gallery_items(listed) == [
        VisualItem("trcc-pipes", "", "TRCC Pipes"),
        VisualItem("milkdrop", "geiss/Swirl.milk", "Swirl"),
        VisualItem("milkdrop", "Flexi - Rain.milk", "Flexi - Rain")]


def test_a_tile_is_what_the_visual_draws_for_noise(app: App) -> None:
    tile = app.dispatch(VisualTile(visual="trcc-pipes", size=64))
    assert (tile.ok, tile.media_type) == (True, "image/png")
    image = QImage.fromData(tile.image)
    assert (image.width(), image.height()) == (64, 64)
    lit = {image.pixel(x, y) for x in range(0, 64, 4) for y in range(0, 64, 4)}
    assert len(lit) > 8                                # pipes, not a blank tile


def test_a_tile_is_refused_for_an_unknown_visual_or_size(app: App) -> None:
    assert app.dispatch(VisualTile(visual="nope")).message == (
        "no audio visual 'nope' on this computer")
    assert app.dispatch(VisualTile(visual="trcc-pipes", size=4096)).message == (
        "a tile is 16 to 512 pixels a side")


def test_the_api_serves_a_tile_as_png(app: App) -> None:
    from tests.conftest import loopback_client
    from trcc.ui.api.main import build_app

    client = loopback_client(build_app(app))
    tile = client.get("/display/visual-tile", params={"visual": "trcc-pipes",
                                                      "size": 32})
    assert (tile.status_code, tile.headers["content-type"]) == (200, "image/png")
    assert client.get("/display/visual-tile",
                      params={"visual": "nope"}).status_code == 404


def test_a_computer_that_cannot_hear_says_so_up_front(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows/macOS/BSD before their sound inputs: the gallery says why on
    every tile, and a click is refused -- out loud -- before anything
    changes.  MUTATION CHECK: drop the sound check in PlayVisual."""
    monkeypatch.setattr(MockPlatform, "SOUND_INPUT", None)
    why = (f"TRCC cannot hear this computer's sound on "
           f"{app.platform.distro_name()} yet")
    assert {v.problem for v in app.dispatch(ListVisuals()).visuals} == {why}
    heard: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, heard.append)

    played = app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))

    assert (played.ok, played.message) == (False, why)
    assert [e.message for e in heard] == [why]
    assert app.settings.for_device(_KEY).visual is None
    assert app.dispatch(VisualTile(visual="trcc-pipes", size=32)).ok


def test_without_ffmpeg_there_is_nothing_to_hear(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.visual import sound

    monkeypatch.setattr(sound.toolchain, "present", lambda name: False)
    assert sound.MonitorSoundInput.problem() == (
        "ffmpeg is not installed -- it is what hears the music for an audio "
        "visual")


def test_the_gallery_carries_why_a_visual_cannot_play() -> None:
    from trcc.core.models import VisualInfo
    from trcc.core.results import VisualsResult
    from trcc.ui.presentation.visual_gallery import gallery_items

    listed = VisualsResult(ok=True, visuals=(
        VisualInfo("trcc-pipes", "TRCC Pipes", "TRCC", problem="no sound"),))
    assert [i.problem for i in gallery_items(listed)] == ["no sound"]



# ── live sliders, from every UI ──────────────────────────────────────

def test_sliders_change_the_playing_visual_live(app: App) -> None:
    """Merged with what was set, kept in range, saved, handed to the reader
    -- which keeps playing -- and announced so every UI follows."""
    from trcc.core.events import BackgroundChanged

    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"growth": 2.0}))
    reader = _reader(app)
    heard: list[BackgroundChanged] = []
    app.events.subscribe(BackgroundChanged, heard.append)

    moved = app.dispatch(SetVisualSettings(key=_KEY, settings={"fire": 9.0}))

    assert moved.ok and moved.playing
    assert reader.adjusted == {"growth": 2.0, "fire": 1.0}       # clamped
    assert _reader(app) is reader                                # not restarted
    assert app.settings.for_device(_KEY).visual_settings == reader.adjusted
    assert [e.key for e in heard] == [_KEY]
    listed = app.dispatch(ListVisuals(key=_KEY))
    assert (listed.playing, listed.sliders) == ("trcc-pipes", reader.adjusted)


def test_a_slider_the_visual_lacks_is_refused_out_loud(app: App) -> None:
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes"))
    heard: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, heard.append)

    refused = app.dispatch(SetVisualSettings(key=_KEY, settings={"warp": 1.0}))

    assert (refused.ok, refused.message) == (
        False, "TRCC Pipes has no slider warp")
    assert [e.message for e in heard] == [refused.message]
    assert app.settings.for_device(_KEY).visual_settings == {}


def test_sliders_need_a_playing_visual(app: App) -> None:
    refused = app.dispatch(SetVisualSettings(key=_KEY, settings={"fire": 1.0}))
    assert (refused.ok, refused.message) == (
        False, f"no audio visual plays on {_KEY}")


def test_a_new_centre_picture_shows_at_once(app: App, tmp_path: Path) -> None:
    """The playing visual starts again with it, sliders kept."""
    app.dispatch(PlayVisual(key=_KEY, visual="trcc-pipes",
                            settings={"fire": 0.5}))
    before = _reader(app)
    picture = tmp_path / "bob.png"
    QImage(4, 4, QImage.Format.Format_ARGB32).save(str(picture))

    assert app.dispatch(SetVisualCenter(path=str(picture))).ok

    after = _reader(app)
    assert after is not before and before.closed
    assert (after.spec.center, dict(after.spec.settings)) == (picture,
                                                               {"fire": 0.5})


def test_the_reader_hands_sliders_to_its_engine_between_pictures() -> None:
    """On the engine's own thread, before the next picture -- never while
    it draws.  The real VisualReader, a scripted beat, a recording engine."""
    import threading

    from trcc.services.visual_stream import VisualReader

    seen: list[tuple[str, object]] = []
    adjusted = threading.Event()

    class Recording(TrccPipes):
        def adjust(self, settings: Any) -> None:
            seen.append((threading.current_thread().name, dict(settings)))
            super().adjust(settings)
            if seen[-1][1] == {"fire": 1.0}:
                adjusted.set()

    reader = VisualReader(VisualSpec("trcc-pipes", 64, 64), ScriptedSound,
                          Recording)
    try:
        reader.adjust({"fire": 1.0})
        assert adjusted.wait(5)
    finally:
        reader.close()
    assert seen[-1] == ("trcc-visual", {"fire": 1.0})
    assert dict(reader.spec.settings) == {"fire": 1.0}

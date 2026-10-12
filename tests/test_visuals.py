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
    StopVisual,
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


def test_milkdrop_is_offered_only_where_libprojectm_is_installed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projectm_module.ctypes.util, "find_library",
                        lambda name: None)
    assert [info.kind for info in catalog()] == ["trcc-pipes"]
    monkeypatch.setattr(projectm_module.ctypes.util, "find_library",
                        lambda name: "libprojectM-4.so.4")
    assert [info.kind for info in catalog()] == ["trcc-pipes", "milkdrop"]


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

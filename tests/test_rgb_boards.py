"""Finding motherboard RGB the way OpenRGB does -- matching, naming, Find."""
from __future__ import annotations

from pathlib import Path

from trcc.adapters.rgb import make_board_driver
from trcc.app import App
from trcc.core.commands import RgbLights, ScanRgbLights
from trcc.core.models import (
    BoardIdentity,
    FoundBoard,
    HidInterface,
    LightKind,
    RgbControllerId,
    RgbMirrorDevice,
)
from trcc.core.ports import RgbMirror
from trcc.services.rgb_boards import MotherboardFinder, matches, spot

from .mock_platform import (
    AURA_INTERFACE,
    MAINTAINER_BOARD,
    MockPlatform,
    ScriptedRgbHardware,
)

MSI = HidInterface(0x1462, 0x7E06, 0, 0x0001, 0x0000, "MSI MYSTIC LIGHT")
MSI_BOARD = BoardIdentity("Micro-Star International Co., Ltd.",
                          "MAG B650 TOMAHAWK WIFI (MS-7D75)")


def _platform(root: Path, *interfaces: HidInterface,
              board: BoardIdentity | None = MAINTAINER_BOARD) -> MockPlatform:
    platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], root)
    platform.rgb = ScriptedRgbHardware(board, interfaces)
    return platform


# ── Matching, as OpenRGB's HID detectors match ─────────────────────────────

def test_a_row_matches_only_on_the_fields_it_names() -> None:
    row = RgbControllerId(0x0B05, 0x19AF, "ASUS Aura Motherboard", "d",
                          usage_page=0xFF72, usage=0x00A1)
    assert matches(row, AURA_INTERFACE)
    other_page = HidInterface(0x0B05, 0x19AF, 2, 0x0001, 0x00A1)
    assert not matches(row, other_page)     # the keyboard-like interface
    any_field = RgbControllerId(0x0B05, 0x19AF, "x", "d")
    assert matches(any_field, other_page)


def test_spot_names_a_controller_as_openrgb_does_once_per_device() -> None:
    """The brand, then the board's DMI name; a second interface of the same
    device is the same controller."""
    second = HidInterface(0x0B05, 0x19AF, 3, 0xFF72, 0x00A1)
    found = spot((AURA_INTERFACE, second), MAINTAINER_BOARD)
    assert [(b.label, b.controller.name) for b in found] == [
        ("ASUS PRIME Z790-V AX", "ASUS Aura Motherboard")]
    assert spot((MSI,), None)[0].label == "MSI Mystic Light MS_7E06"
    assert spot((HidInterface(0x413C, 0x2113, 0, 1, 6),), MAINTAINER_BOARD) == ()


def test_only_the_aura_motherboard_gets_a_trcc_driver() -> None:
    aura, msi = spot((AURA_INTERFACE, MSI), MSI_BOARD)
    opened: list = []
    driver = make_board_driver(aura, open_transport=lambda *a, **k: opened,
                               argb_leds=lambda: {1: 8})
    assert driver is not None and opened == []        # made, not yet opened
    assert make_board_driver(msi, open_transport=lambda *a, **k: opened,
                             argb_leds=dict) is None


# ── Find, through the App ──────────────────────────────────────────────────

def test_find_lists_the_boards_lights_and_a_restart_remembers_them(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The maintainer's board: its LED and three ARGB headers, read with the
    two read requests and nothing else; an MSI controller listed, not driven.

    MUTATION CHECK: drop the BOARD/FOUND save in ScanRgbLights."""
    platform = _platform(tmp_path, AURA_INTERFACE, MSI)
    app = App(platform)
    found = app.dispatch(ScanRgbLights())
    board = [(light.kind, light.ref, light.name, light.led_count)
             for light in found.lights
             if light.kind in (LightKind.BOARD, LightKind.FOUND)]
    assert board == [
        (LightKind.BOARD, "aura/onboard", "ASUS PRIME Z790-V AX -- board LEDs", 1),
        (LightKind.BOARD, "aura/argb1", "ASUS PRIME Z790-V AX -- ARGB header 1", 0),
        (LightKind.BOARD, "aura/argb2", "ASUS PRIME Z790-V AX -- ARGB header 2", 0),
        (LightKind.BOARD, "aura/argb3", "ASUS PRIME Z790-V AX -- ARGB header 3", 0),
        (LightKind.FOUND, "usb/1462:7e06",
         "MSI PRIME Z790-V AX (MSI Mystic Light MS_7E06)", 0)]
    assert found.message.endswith(
        "; motherboard: 4 light(s), 1 controller(s) TRCC does not drive")
    assert [bytes(w[1])[:1] for w in platform.aura.writes] == [b"\x82", b"\xb0"]
    assert not platform.aura.is_open                   # Find leaves nothing open
    app.close()
    again = App(_platform(tmp_path))
    listed = again.dispatch(RgbLights())
    assert [(light.ref, light.remembered) for light in listed.lights
            if light.kind in (LightKind.BOARD, LightKind.FOUND)] == [
        ("aura/onboard", True), ("aura/argb1", True), ("aura/argb2", True),
        ("aura/argb3", True), ("usb/1462:7e06", True)]
    again.close()


class _Unreadable(RgbMirror):
    """A driver whose controller will not answer."""

    def __init__(self) -> None:
        self.closed = False

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        raise OSError("permission denied: /dev/hidraw0")

    def show(self, device: RgbMirrorDevice, colors) -> None:  # type: ignore[no-untyped-def]
        raise AssertionError("Find never sends colours")

    def close(self) -> None:
        self.closed = True


def test_a_board_that_cannot_be_read_is_listed_as_found_and_says_why() -> None:
    unreadable = _Unreadable()

    def driver(board: FoundBoard) -> RgbMirror:
        return unreadable

    finder = MotherboardFinder(lambda: ScriptedRgbHardware(), driver)
    scan = finder.scan()
    assert scan.driven == ()
    assert [light.ref for light in scan.found] == ["usb/0b05:19af"]
    assert scan.error == ("ASUS PRIME Z790-V AX: permission denied: "
                          "/dev/hidraw0")
    assert unreadable.closed
    assert finder.last() == scan


def test_a_failed_board_read_keeps_the_last_find(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = App(_platform(tmp_path, AURA_INTERFACE))
    app.dispatch(ScanRgbLights())
    saved = app.settings.found_lights(LightKind.BOARD)
    app.rgb_boards = MotherboardFinder(lambda: ScriptedRgbHardware(),
                                       lambda board: _Unreadable())
    failed = app.dispatch(ScanRgbLights())
    assert not failed.ok
    assert failed.message.endswith("; motherboard: ASUS PRIME Z790-V AX: "
                                   "permission denied: /dev/hidraw0")
    assert app.settings.found_lights(LightKind.BOARD) == saved
    app.close()


def test_no_board_rgb_says_none_found(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = App(_platform(tmp_path))
    found = app.dispatch(ScanRgbLights())
    assert found.message.endswith("; motherboard: none found")
    assert not [light for light in found.lights
                if light.kind in (LightKind.BOARD, LightKind.FOUND)]
    app.close()

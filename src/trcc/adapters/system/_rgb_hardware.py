"""Spotting RGB controllers on Linux from sysfs -- no device is opened.

``/sys/bus/hid/devices`` already holds every HID interface's ids, name and
report descriptor, readable by any user; it is what hidapi's own hidraw
backend lists from.  Reading it touches no device -- unlike hidapi's libusb
backend, whose enumeration detaches the kernel driver to fetch a descriptor.
``/sys/class/dmi/id`` names the board, as OpenRGB's ``DMIInfo`` reads it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ...core.models import BoardIdentity, HidInterface
from ...core.ports import RgbHardware

log = logging.getLogger(__name__)

_USB_BUS = 0x0003
#: Short-item prefixes, size bits masked off: Usage Page (global, tag 0),
#: Usage (local, tag 0), Collection (main, tag 0xA); 0xFE starts a long item.
_USAGE_PAGE, _USAGE, _COLLECTION, _LONG = 0x04, 0x08, 0xA0, 0xFE


def first_usage(descriptor: bytes) -> tuple[int, int]:
    """The (usage page, usage) before the first collection -- the pair
    hidapi reports for an interface; 0 for one not given."""
    page = usage = 0
    at = 0
    while at < len(descriptor):
        prefix = descriptor[at]
        if prefix == _LONG:
            at += 3 + (descriptor[at + 1] if at + 1 < len(descriptor) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        value = int.from_bytes(descriptor[at + 1:at + 1 + size], "little")
        match prefix & 0xFC:
            case 0x04:
                page = value
            case 0x08:
                usage = value & 0xFFFF
            case 0xA0:
                break
        at += 1 + size
    log.debug("first_usage: page 0x%04x usage 0x%04x", page, usage)
    return page, usage


class SysfsRgbHardware(RgbHardware):
    """The board's DMI names and the HID interfaces, as sysfs lists them."""

    def __init__(self, sys_root: Path = Path("/sys")) -> None:
        log.debug("SysfsRgbHardware.__init__: %s", sys_root)
        self._sys = sys_root

    def board(self) -> BoardIdentity | None:
        dmi = self._sys / "class" / "dmi" / "id"
        try:
            vendor = (dmi / "board_vendor").read_text().strip()
            name = (dmi / "board_name").read_text().strip()
        except OSError as e:
            log.info("SysfsRgbHardware.board: no DMI board names (%s)", e)
            return None
        log.info("SysfsRgbHardware.board: %r %r", vendor, name)
        return BoardIdentity(vendor, name) if name else None

    def hid_interfaces(self) -> tuple[HidInterface, ...]:
        devices = self._sys / "bus" / "hid" / "devices"
        found = tuple(i for d in sorted(devices.glob("*"))
                      if (i := self._interface(d)) is not None)
        log.info("SysfsRgbHardware.hid_interfaces: %d USB HID interface(s)",
                 len(found))
        return found

    def _interface(self, device: Path) -> HidInterface | None:
        """One HID device's entry; None for one not on USB or unreadable."""
        try:
            fields = dict(line.split("=", 1) for line in
                          (device / "uevent").read_text().splitlines()
                          if "=" in line)
            bus, vid, pid = (int(x, 16) for x in fields["HID_ID"].split(":"))
            descriptor = (device / "report_descriptor").read_bytes()
            number = (device.resolve().parent / "bInterfaceNumber").read_text()
        except (OSError, KeyError, ValueError) as e:
            log.debug("_interface: %s skipped (%s)", device.name, e)
            return None
        if bus != _USB_BUS:
            log.debug("_interface: %s is not on USB", device.name)
            return None
        page, usage = first_usage(descriptor)
        nodes = sorted((device / "hidraw").glob("hidraw*"))
        return HidInterface(vid, pid, int(number, 16), page, usage,
                            fields.get("HID_NAME", ""),
                            f"/dev/{nodes[0].name}" if nodes else "")

#!/usr/bin/env python3
"""Generate ``src/trcc/core/rgb_catalog.py`` from OpenRGB's detectors.

Find lights lists the motherboard RGB controllers OpenRGB can find, the way
OpenRGB finds them: by USB HID ids.  The ids are not ours to invent -- they
are OpenRGB's ``REGISTER_HID_DETECTOR*`` registrations, read from its source
and written out as data, so a regeneration is the whole update.

Only the detect functions in ``MOTHERBOARD_DETECTORS`` are kept: a family
folder mixes kinds (ASUS Aura's registers its keyboards, mice and monitors
beside the boards), and the detect function is what says which controller a
registration builds.  ``*_DISABLED`` registrations are skipped -- OpenRGB
does not run them either.  SMBus motherboard controllers are not here at
all: finding them means probing the chipset bus.

**Credit.**  The ids and names are OpenRGB's
(https://gitlab.com/CalcProgrammer1/OpenRGB, GPL-2.0-or-later), by Adam Honse
(CalcProgrammer1) and its contributors.  The authors each detect file names
in its header are copied into the generated file, as given, so the notices
travel with the data.

    git clone --depth 1 https://gitlab.com/CalcProgrammer1/OpenRGB.git /tmp/orgb
    PYTHONPATH=src python3 dev/gen_rgb_catalog.py /tmp/orgb

Deterministic -- rows sorted, no timestamp -- so the committed file changes
only when OpenRGB's ids do.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OUT = _REPO / "src" / "trcc" / "core" / "rgb_catalog.py"

#: The detect functions whose controllers OpenRGB types as a motherboard.
MOTHERBOARD_DETECTORS = frozenset({
    "DetectAsusAuraUSBMotherboards",
    "DetectAsusAuraUSBAddressable",
    "DetectMSIMotherboardControllers",
    "DetectGigabyteRGBFusion2USBControllers",
    "DetectPolychromeUSBControllers",
    "DetectLenovoMotherboardControllers",
    "DetectJGINYUEInternalUSBController",
    "DetectJGINYUEInternalUSBV2Controller",
    "DetectHPOmen30LController",
})

#: What follows ``vid, pid`` in each registration macro.
_TAIL = {"": (), "_I": ("interface",), "_P": ("usage_page",),
         "_IP": ("interface", "usage_page"), "_PU": ("usage_page", "usage"),
         "_IPU": ("interface", "usage_page", "usage")}

_CALL = re.compile(r"\bREGISTER_HID_DETECTOR(_[A-Z]+)?\s*\((.*?)\)\s*;", re.S)
_DEFINE = re.compile(r"^\s*#define\s+(\w+)\s+(.+?)\s*$", re.M)
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_HEADER = re.compile(r"/\*-+\*?\\?(.*?)\\?\*-+\*/", re.S)


def _authors(source: str) -> list[str]:
    """The author lines of an OpenRGB file header: the paragraphs between its
    description and "This file is part of the OpenRGB project"."""
    match = _HEADER.search(source)
    if match is None:
        return []
    lines = [" ".join(line.strip().strip("|").split())
             for line in match.group(1).splitlines()]
    paragraphs, current = [], []
    for line in [*lines, ""]:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    end = next((i for i, p in enumerate(paragraphs)
                if p[0].startswith("This file is part of")), len(paragraphs))
    return [line for p in paragraphs[2:end] for line in p]


def _defines(folder: Path) -> dict[str, str]:
    """Every ``#define NAME value`` in a family's sources."""
    found: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix in (".cpp", ".h"):
            text = _COMMENT.sub("", path.read_text(errors="replace"))
            found.update(_DEFINE.findall(text))
    return found


def _resolve(token: str, defines: dict[str, str]) -> str:
    """*token* with its ``#define`` chain followed."""
    seen: set[str] = set()
    while token in defines and token not in seen:
        seen.add(token)
        token = defines[token]
    return token


def _number(token: str, defines: dict[str, str]) -> int:
    return int(_resolve(token, defines), 0)


def _split(args: str) -> list[str]:
    """Top-level comma split; commas inside a string stay."""
    parts, current, quoted = [], "", False
    for ch in args:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    return parts


def _rows(controllers: Path) -> tuple[list[str], set[str], dict[str, list[str]]]:
    """One ``RgbControllerId(...)`` source line per kept registration, the
    detect functions they came from, and each source file's named authors."""
    rows: set[str] = set()
    seen: set[str] = set()
    authors: dict[str, list[str]] = {}
    for folder in sorted(p for p in controllers.iterdir() if p.is_dir()):
        defines = _defines(folder)
        for path in sorted(folder.rglob("*.cpp")):
            source = path.read_text(errors="replace")
            text = _COMMENT.sub("", source)
            for suffix, args in _CALL.findall(text):
                if suffix not in _TAIL:
                    continue                 # _DISABLED, _ONLY: not run
                name, func, vid, pid, *tail = _split(args)
                if func not in MOTHERBOARD_DETECTORS:
                    continue
                seen.add(func)
                if named := _authors(source):
                    authors[path.name] = named
                label = _resolve(name, defines).strip('"')
                extra = "".join(
                    f", {field}=0x{_number(value, defines):04X}"
                    for field, value in zip(_TAIL[suffix], tail, strict=True))
                rows.add(f"    RgbControllerId(0x{_number(vid, defines):04X}, "
                         f"0x{_number(pid, defines):04X}, {label!r}, "
                         f"{func!r}{extra}),")
    return sorted(rows), seen, authors


def _commit(openrgb: Path) -> str:
    return subprocess.run(["git", "-C", str(openrgb), "log", "-1",
                           "--format=%H %ad", "--date=short"],
                          capture_output=True, text=True, check=True,
                          shell=False).stdout.strip()


def render(openrgb: Path) -> str:
    rows, seen, authors = _rows(openrgb / "Controllers")
    commit = _commit(openrgb)
    missing = MOTHERBOARD_DETECTORS - seen
    if missing:
        raise SystemExit(f"no registrations found for {sorted(missing)}")
    return (
        '"""Motherboard RGB controllers OpenRGB finds by USB HID ids.\n\n'
        "GENERATED by ``dev/gen_rgb_catalog.py`` -- do not edit by hand.\n\n"
        "The ids and names are OpenRGB's ``REGISTER_HID_DETECTOR*``\n"
        "registrations (https://gitlab.com/CalcProgrammer1/OpenRGB,\n"
        "GPL-2.0-or-later), by Adam Honse (CalcProgrammer1) and its\n"
        "contributors, read at the commit below.  The authors the detect\n"
        "files read name, as they give them:\n\n"
        + "".join(f"* ``{name}``: " + "; ".join(lines) + "\n"
                  for name, lines in sorted(authors.items()))
        + '"""\n'
        "from __future__ import annotations\n\n"
        "from .models import RgbControllerId\n\n"
        f"OPENRGB_COMMIT = {commit!r}\n\n"
        "MOTHERBOARD_CONTROLLERS: tuple[RgbControllerId, ...] = (\n"
        + "\n".join(rows) + "\n)\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate src/trcc/core/rgb_catalog.py from OpenRGB.")
    parser.add_argument("openrgb", type=Path, help="an OpenRGB source checkout")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed file differs")
    args = parser.parse_args()
    text = render(args.openrgb)
    if args.check:
        stale = _OUT.read_text() != text
        print(f"{_OUT.relative_to(_REPO)}: {'STALE' if stale else 'current'}")
        return int(stale)
    _OUT.write_text(text)
    print(f"wrote {_OUT.relative_to(_REPO)}: {text.count('RgbControllerId(') - 1} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

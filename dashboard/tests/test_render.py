import unittest
from datetime import datetime

from lcd_dashboard.config import Config
from lcd_dashboard.demo import demo_snapshot
from lcd_dashboard.models import Snapshot, TempRow
from lcd_dashboard.render import (
    fmt_mbps,
    fmt_pair,
    fmt_temp,
    load_color,
    pretty_name,
    render,
    temp_color,
)
from PIL import Image


def draw(snap: Snapshot, width: int = 1920, height: int = 462, background: str = "") -> Image.Image:
    d = Config().display
    return render(snap, width, height, font_regular=d.font_regular, font_bold=d.font_bold,
                  background=background)


class FormatTest(unittest.TestCase):
    def test_fmt_pair(self) -> None:
        gb, tb = 2**30, 2**40
        self.assertEqual(fmt_pair(3.7 * gb, 8 * gb), "3.7 / 8 GB")
        self.assertEqual(fmt_pair(58 * gb, 93 * gb), "58 / 93 GB")
        self.assertEqual(fmt_pair(5.2 * tb, 7.3 * tb), "5.2 / 7.3 TB")
        self.assertEqual(fmt_pair(None, 8 * gb), "--")

    def test_fmt_misc(self) -> None:
        self.assertEqual(fmt_temp(52.4), "52°C")
        self.assertEqual(fmt_temp(None), "--")
        self.assertEqual(fmt_mbps(92.44), "92.4 Mbps")
        self.assertEqual(fmt_mbps(2500), "2.50 Gbps")
        self.assertEqual(fmt_mbps(None), "-- Mbps")

    def test_pretty_name(self) -> None:
        self.assertEqual(pretty_name("NAS SYNOLOGY"), "NAS Synology")
        self.assertEqual(pretty_name("HOME ASSISTANT"), "Home Assistant")
        self.assertEqual(pretty_name("Máy chủ"), "Máy chủ")

    def test_colors(self) -> None:
        base = (1, 2, 3)
        self.assertEqual(load_color(50, base), base)
        self.assertNotEqual(load_color(75, base), base)
        self.assertNotEqual(load_color(95, base), load_color(75, base))
        self.assertEqual(temp_color(0.0), temp_color(0.3))   # vùng xanh
        self.assertEqual(temp_color(2.0), temp_color(1.0))   # chặn trên


class RenderTest(unittest.TestCase):
    def test_demo_frame_size(self) -> None:
        img = draw(demo_snapshot(Config()))
        self.assertEqual((img.size, img.mode), ((1920, 462), "RGB"))

    def test_empty_snapshot_does_not_crash(self) -> None:
        snap = Snapshot(now=datetime(2026, 10, 14, 8, 24),
                        temps=[TempRow("A", None), TempRow("B", 200, "sensor.x"),
                               TempRow("C", 40, "local:gpu"), TempRow("D", 10, "nas:disk")])
        img = draw(snap)
        self.assertEqual(img.size, (1920, 462))

    def test_custom_size_is_resized(self) -> None:
        img = draw(demo_snapshot(Config()), 960, 231)
        self.assertEqual(img.size, (960, 231))

    def test_missing_background_falls_back(self) -> None:
        img = draw(demo_snapshot(Config()), background="/khong/co/anh.png")
        self.assertEqual(img.size, (1920, 462))


if __name__ == "__main__":
    unittest.main()

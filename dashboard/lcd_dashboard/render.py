"""Vẽ dashboard 1920x462 bằng Pillow.

Toạ độ trong file này là toạ độ "thiết kế" ở 1920x462. ``Painter`` vẽ ở
độ phân giải gấp ``SS`` lần rồi thu nhỏ (supersampling) để nét cong mượt.
"""

from __future__ import annotations

import logging
import math
import random
import threading
from collections.abc import Callable, Sequence
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import HostStats, NetStats, Snapshot, TempRow, Weather

log = logging.getLogger(__name__)

SS = 2  # hệ số supersampling

# Vòng lặp gửi ảnh và trang cấu hình có thể vẽ cùng lúc; font FreeType dùng chung
# giữa các luồng nên chỉ cho một luồng vẽ tại một thời điểm.
RENDER_LOCK = threading.Lock()

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

# Bảng màu
BG = (3, 6, 14)
CARD_FILL = (9, 17, 34, 235)
CARD_BORDER = (32, 72, 128, 255)
WHITE = (240, 246, 255)
MUTED = (150, 175, 210)
CYAN = (0, 214, 255)
BLUE = (40, 140, 255)
GREEN = (40, 220, 90)
ORANGE = (255, 150, 30)
RED = (255, 70, 60)
YELLOW = (255, 210, 60)
PINK = (225, 80, 230)
TRACK = (28, 40, 64)
ONLINE = (60, 220, 90)
OFFLINE = (255, 80, 70)

WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")

# Mã thời tiết WMO (Open-Meteo) → (mô tả, loại biểu tượng)
WMO: dict[int, tuple[str, str]] = {
    0: ("Trời quang", "sun"), 1: ("Ít mây", "partly"), 2: ("Nhiều mây", "partly"),
    3: ("Âm u", "cloud"), 45: ("Sương mù", "fog"), 48: ("Sương mù", "fog"),
    51: ("Mưa phùn", "rain"), 53: ("Mưa phùn", "rain"), 55: ("Mưa phùn", "rain"),
    56: ("Mưa phùn", "rain"), 57: ("Mưa phùn", "rain"),
    61: ("Mưa nhỏ", "rain"), 63: ("Mưa vừa", "rain"), 65: ("Mưa to", "rain"),
    66: ("Mưa lạnh", "rain"), 67: ("Mưa lạnh", "rain"),
    71: ("Tuyết", "snow"), 73: ("Tuyết", "snow"), 75: ("Tuyết", "snow"), 77: ("Tuyết", "snow"),
    80: ("Mưa rào", "rain"), 81: ("Mưa rào", "rain"), 82: ("Mưa rào to", "rain"),
    85: ("Mưa tuyết", "snow"), 86: ("Mưa tuyết", "snow"),
    95: ("Dông", "storm"), 96: ("Dông, mưa đá", "storm"), 99: ("Dông, mưa đá", "storm"),
}


# --------------------------------------------------------------------- helpers


def fmt_num(v: float) -> str:
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}".removesuffix(".0")


def fmt_pair(used: float | None, total: float | None) -> str:
    """'3.7 / 8 GB' — cùng đơn vị (nhị phân) cho cả hai số."""
    if used is None or total is None:
        return "--"
    unit, div = ("TB", 2**40) if total >= 2**40 else ("GB", 2**30)
    return f"{fmt_num(used / div)} / {fmt_num(total / div)} {unit}"


def pretty_name(title: str) -> str:
    """'NAS SYNOLOGY' → 'NAS Synology' (từ ≤3 chữ giữ nguyên chữ hoa)."""
    if not title.isupper():
        return title
    return " ".join(w if len(w) <= 3 else w.capitalize() for w in title.split())


def fmt_temp(v: float | None) -> str:
    return "--" if v is None else f"{v:.0f}°C"


def fmt_mbps(v: float | None) -> str:
    if v is None:
        return "-- Mbps"
    return f"{v / 1000:.2f} Gbps" if v >= 1000 else f"{v:.1f} Mbps"


def load_color(pct: float | None, base: RGB) -> RGB:
    """Màu vòng tải: màu riêng của chỉ số, cam khi ≥70%, đỏ khi ≥90%."""
    if pct is None:
        return base
    return RED if pct >= 90 else ORANGE if pct >= 70 else base


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def temp_color(frac: float) -> RGB:
    """Xanh lá → vàng → cam → đỏ theo tỉ lệ nhiệt độ / nhiệt độ tối đa."""
    stops = ((0.0, GREEN), (0.45, GREEN), (0.55, YELLOW), (0.62, ORANGE), (0.85, RED),
             (1.0, RED))
    frac = min(max(frac, 0.0), 1.0)
    for (t0, c0), (t1, c1) in pairwise(stops):
        if frac <= t1:
            return lerp(c0, c1, 0 if t1 == t0 else (frac - t0) / (t1 - t0))
    return RED


# --------------------------------------------------------------------- painter


class Painter:
    """Bọc ``ImageDraw`` ở độ phân giải SS lần, nhận toạ độ thiết kế."""

    def __init__(self, width: int, height: int, font_regular: str, font_bold: str) -> None:
        self.w, self.h = width, height
        self.img = Image.new("RGBA", (width * SS, height * SS), (*BG, 255))
        self.draw = ImageDraw.Draw(self.img)
        self.font_regular = font_regular
        self.font_bold = font_bold

    # -- toạ độ
    @staticmethod
    def s(v: float) -> int:
        return round(v * SS)

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (self.s(x0), self.s(y0), self.s(x1), self.s(y1))

    # -- font
    def font(self, size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
        return _font(self.font_bold if bold else self.font_regular, self.s(size))

    # -- lớp trong suốt
    def layer(self, fn: Callable[[ImageDraw.ImageDraw], None], blur: float = 0,
              bbox: tuple[float, float, float, float] | None = None) -> None:
        """Vẽ lên một lớp trong suốt rồi trộn alpha vào ảnh chính.

        ``bbox`` (toạ độ thiết kế) giới hạn vùng làm mờ/trộn cho nhanh.
        """
        lay = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        fn(ImageDraw.Draw(lay))
        if bbox is None:
            region = (0, 0, *self.img.size)
        else:
            m = blur * 3
            region = (max(self.s(bbox[0] - m), 0), max(self.s(bbox[1] - m), 0),
                      min(self.s(bbox[2] + m), self.img.width),
                      min(self.s(bbox[3] + m), self.img.height))
        part = lay.crop(region)
        if blur:
            part = part.filter(ImageFilter.GaussianBlur(blur * SS))
        self.img.alpha_composite(part, dest=region[:2])

    # -- chữ
    def text(self, x: float, y: float, s: str, size: float, color: Sequence[int],
             bold: bool = False, anchor: str = "la") -> float:
        """Vẽ chữ, trả về độ rộng (toạ độ thiết kế)."""
        f = self.font(size, bold)
        self.draw.text((self.s(x), self.s(y)), s, font=f, fill=tuple(color), anchor=anchor)
        return self.text_width(s, size, bold)

    def text_width(self, s: str, size: float, bold: bool = False) -> float:
        return self.draw.textlength(s, font=self.font(size, bold)) / SS

    def fit_text(self, x: float, y: float, s: str, size: float, max_w: float,
                 color: Sequence[int], bold: bool = False, anchor: str = "la") -> None:
        """Như ``text`` nhưng thu nhỏ cỡ chữ cho vừa ``max_w``."""
        while size > 10 and self.text_width(s, size, bold) > max_w:
            size -= 1
        self.text(x, y, s, size, color, bold, anchor)

    # -- hình
    def rrect(self, x0: float, y0: float, x1: float, y1: float, r: float,
              fill: Sequence[int] | None = None, outline: Sequence[int] | None = None,
              width: float = 1) -> None:
        def paint(d: ImageDraw.ImageDraw) -> None:
            d.rounded_rectangle(self.box(x0, y0, x1, y1), radius=self.s(r),
                                fill=tuple(fill) if fill else None,
                                outline=tuple(outline) if outline else None,
                                width=max(1, self.s(width)))
        translucent = any(c is not None and len(c) == 4 and c[3] < 255 for c in (fill, outline))
        if translucent:
            self.layer(paint, bbox=(x0, y0, x1, y1))
        else:
            paint(self.draw)

    def gradient_bar(self, x0: float, y0: float, x1: float, y1: float, r: float,
                     c0: RGB, c1: RGB) -> None:
        """Thanh bo góc tô dải màu ngang từ ``c0`` sang ``c1``."""
        bx0, by0, bx1, by1 = self.box(x0, y0, x1, y1)
        w, h = max(bx1 - bx0, 1), max(by1 - by0, 1)
        line = Image.new("RGB", (w, 1))
        line.putdata([lerp(c0, c1, i / max(w - 1, 1)) for i in range(w)])
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=self.s(r), fill=255)
        self.img.paste(line.resize((w, h)), (bx0, by0), mask)

    def circle(self, cx: float, cy: float, r: float, fill: Sequence[int]) -> None:
        self.draw.ellipse(self.box(cx - r, cy - r, cx + r, cy + r), fill=tuple(fill))

    def line(self, pts: Sequence[tuple[float, float]], color: Sequence[int],
             width: float) -> None:
        self.draw.line([(self.s(x), self.s(y)) for x, y in pts], fill=tuple(color),
                       width=max(1, self.s(width)), joint="curve")

    def arc(self, cx: float, cy: float, r: float, start: float, end: float,
            color: Sequence[int], width: float) -> None:
        self.draw.arc(self.box(cx - r, cy - r, cx + r, cy + r), start, end,
                      fill=tuple(color), width=self.s(width))

    def poly(self, pts: Sequence[tuple[float, float]], fill: Sequence[int]) -> None:
        self.draw.polygon([(self.s(x), self.s(y)) for x, y in pts], fill=tuple(fill))

    def finish(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.Resampling.LANCZOS).convert("RGB")


@lru_cache(maxsize=64)
def _font(path: str, px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        log.warning("render: không mở được font %s — thử DejaVuSans", path)
        return ImageFont.truetype("DejaVuSans.ttf", px)


# ----------------------------------------------------------------------- icons


def icon_home(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    """Biểu tượng Home Assistant: ngôi nhà + cây nút."""
    p.poly([(x + s * .5, y + s * .05), (x + s * .97, y + s * .5), (x + s * .85, y + s * .5),
            (x + s * .85, y + s * .95), (x + s * .15, y + s * .95), (x + s * .15, y + s * .5),
            (x + s * .03, y + s * .5)], color)
    w = s * .06
    cx = x + s * .5
    p.line([(cx, y + s * .9), (cx, y + s * .45)], WHITE, w)
    p.line([(cx, y + s * .7), (cx - s * .17, y + s * .55)], WHITE, w)
    p.line([(cx, y + s * .78), (cx + s * .17, y + s * .62)], WHITE, w)
    for dx, dy in ((0, .42), (-.19, .53), (.19, .6)):
        p.circle(cx + s * dx, y + s * dy, s * .065, WHITE)


def icon_nas(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    p.rrect(x + s * .08, y + s * .05, x + s * .92, y + s * .95, s * .12, fill=color)
    for i in range(3):
        bx = x + s * (.25 + i * .2)
        p.rrect(bx, y + s * .2, bx + s * .1, y + s * .62, s * .03, fill=WHITE)
        p.circle(bx + s * .05, y + s * .78, s * .045, WHITE)


def icon_thermo(p: Painter, x: float, y: float, s: float) -> None:
    p.rrect(x + s * .36, y, x + s * .64, y + s * .72, s * .14, outline=RED, width=s * .07)
    p.circle(x + s * .5, y + s * .78, s * .2, RED)
    p.line([(x + s * .5, y + s * .3), (x + s * .5, y + s * .75)], RED, s * .1)


def icon_wifi(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    cx, cy = x + s * .5, y + s * .9
    for r in (.85, .58, .32):
        p.arc(cx, cy, s * r, 225, 315, color, s * .1)
    p.circle(cx, cy - s * .04, s * .08, color)


def icon_chip(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    w = s * .07
    for i in range(3):
        o = s * (.3 + i * .2)
        p.line([(x + o, y), (x + o, y + s)], color, w)
        p.line([(x, y + o), (x + s, y + o)], color, w)
    p.rrect(x + s * .15, y + s * .15, x + s * .85, y + s * .85, s * .1, fill=(9, 17, 34))
    p.rrect(x + s * .15, y + s * .15, x + s * .85, y + s * .85, s * .1, outline=color, width=w)
    p.rrect(x + s * .35, y + s * .35, x + s * .65, y + s * .65, s * .05, fill=color)


def icon_gpu(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    p.rrect(x, y + s * .2, x + s, y + s * .8, s * .1, outline=color, width=s * .08)
    for cx in (.32, .7):
        p.arc(x + s * cx, y + s * .5, s * .15, 0, 360, color, s * .07)


def icon_hdd(p: Painter, x: float, y: float, s: float, color: RGB = (70, 180, 255)) -> None:
    p.rrect(x + s * .1, y, x + s * .9, y + s, s * .12, outline=color, width=s * .08)
    p.arc(x + s * .5, y + s * .42, s * .2, 0, 360, color, s * .08)
    p.circle(x + s * .5, y + s * .82, s * .05, color)


def icon_drop(p: Painter, x: float, y: float, s: float, color: RGB = (60, 160, 255)) -> None:
    p.poly([(x + s * .5, y), (x + s * .82, y + s * .6), (x + s * .18, y + s * .6)], color)
    p.circle(x + s * .5, y + s * .64, s * .33, color)


def icon_wind(p: Painter, x: float, y: float, s: float, color: RGB = MUTED) -> None:
    w = s * .08
    for dy, length in ((.3, .75), (.55, .95), (.8, .6)):
        p.line([(x, y + s * dy), (x + s * length, y + s * dy)], color, w)
    p.arc(x + s * .75, y + s * .18, s * .12, 180, 90, color, w)
    p.arc(x + s * .95, y + s * .43, s * .12, 180, 90, color, w)


def icon_arrow(p: Painter, x: float, y: float, s: float, color: RGB, up: bool) -> None:
    cx = x + s * .5
    top, bot = (y + s * .08, y + s * .95) if up else (y + s * .92, y + s * .05)
    p.line([(cx, bot), (cx, top)], color, s * .13)
    d = s * .3 if up else -s * .3
    p.line([(cx - s * .32, top + d), (cx, top), (cx + s * .32, top + d)], color, s * .13)


def _cloud(p: Painter, x: float, y: float, s: float, color: RGB) -> None:
    p.circle(x + s * .32, y + s * .62, s * .2, color)
    p.circle(x + s * .55, y + s * .5, s * .27, color)
    p.circle(x + s * .78, y + s * .65, s * .18, color)
    p.rrect(x + s * .12, y + s * .6, x + s * .95, y + s * .83, s * .11, fill=color)


def _sun(p: Painter, cx: float, cy: float, r: float) -> None:
    for i in range(8):
        a = math.radians(i * 45)
        p.line([(cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35),
                (cx + math.cos(a) * r * 1.8, cy + math.sin(a) * r * 1.8)], YELLOW, r * .28)
    p.circle(cx, cy, r, (255, 196, 40))


def _moon(p: Painter, cx: float, cy: float, r: float) -> None:
    p.circle(cx, cy, r, (235, 235, 210))
    p.circle(cx + r * .45, cy - r * .3, r * .85, (9, 17, 34))


def icon_weather(p: Painter, x: float, y: float, s: float, kind: str, day: bool) -> None:
    cloud_c = (225, 232, 245)
    if kind == "sun":
        (_sun if day else _moon)(p, x + s * .5, y + s * .5, s * .26)
        return
    if kind in ("partly", "cloud", "rain", "storm", "snow") and kind != "cloud":
        (_sun if day else _moon)(p, x + s * .62, y + s * .3, s * .18)
    if kind == "fog":
        for i in range(4):
            yy = y + s * (.3 + i * .15)
            p.line([(x + s * (.1 + i % 2 * .1), yy), (x + s * .9, yy)], cloud_c, s * .07)
        return
    _cloud(p, x, y, s, (150, 160, 180) if kind in ("rain", "storm") else cloud_c)
    if kind == "rain":
        for dx in (.3, .52, .74):
            p.line([(x + s * dx, y + s * .9), (x + s * (dx - .06), y + s * 1.02)], BLUE, s * .06)
    elif kind == "storm":
        p.poly([(x + s * .55, y + s * .78), (x + s * .4, y + s * 1.0), (x + s * .52, y + s * .98),
                (x + s * .45, y + s * 1.15), (x + s * .66, y + s * .9), (x + s * .54, y + s * .92)],
               YELLOW)
    elif kind == "snow":
        for dx in (.32, .55, .78):
            p.circle(x + s * dx, y + s * .97, s * .045, WHITE)


# ------------------------------------------------------------------------ parts


def card(p: Painter, x0: float, y0: float, x1: float, y1: float) -> None:
    p.rrect(x0, y0, x1, y1, 18, fill=CARD_FILL)
    p.rrect(x0, y0, x1, y1, 18, outline=CARD_BORDER, width=1.5)


def header(p: Painter, x: float, y: float, title: str, icon: Callable[..., None],
           max_w: float) -> None:
    icon(p, x, y, 34)
    p.fit_text(x + 46, y + 17, title, 25, max_w - 46, WHITE, bold=True, anchor="lm")


def gauge(p: Painter, cx: float, cy: float, r: float, pct: float | None,
          color: RGB, sub: str) -> None:
    ring = 11
    p.arc(cx, cy, r, 0, 360, TRACK, ring)
    if pct is not None and pct > 0:
        end = -90 + 360 * min(pct, 100) / 100
        p.layer(lambda d: d.arc(p.box(cx - r, cy - r, cx + r, cy + r), -90, end,
                                fill=(*color, 150), width=p.s(ring + 6)), blur=4,
                bbox=(cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8))
        p.arc(cx, cy, r, -90, end, color, ring)
    if pct is None:
        p.text(cx, cy - 4, "--", 34, WHITE, bold=True, anchor="mm")
    else:
        num = f"{pct:.0f}"
        wn = p.text_width(num, 34, True)
        wp = p.text_width("%", 18, True)
        left = cx - (wn + wp) / 2
        p.text(left, cy + 8, num, 34, WHITE, bold=True, anchor="ls")
        p.text(left + wn + 1, cy + 8, "%", 18, WHITE, bold=True, anchor="ls")
    p.text(cx, cy + 28, sub, 15, color, anchor="mm")


def sparkline(p: Painter, x0: float, y0: float, x1: float, y1: float,
              values: Sequence[float], color: RGB, vmax: float | None = None,
              vmin_top: float = 1.0) -> None:
    """Đường biểu đồ + vùng tô mờ bên dưới + quầng sáng.

    Trục dọc tự co giãn theo giá trị lớn nhất (tối thiểu ``vmin_top``), trừ khi
    truyền ``vmax`` cố định.
    """
    if len(values) < 2:
        values = [values[0], values[0]] if values else [0.0, 0.0]
    top = vmax if vmax is not None else max(max(values) * 1.2, vmin_top)
    n = len(values)
    pts = [(x0 + (x1 - x0) * i / (n - 1),
            y1 - (y1 - y0) * min(max(v / top, 0.0), 1.0)) for i, v in enumerate(values)]

    # Vùng tô: màu ``color`` với alpha giảm dần từ trên xuống, cắt theo đa giác.
    bx0, by0, bx1, by1 = p.box(x0, y0, x1, y1)
    bw, bh = max(bx1 - bx0, 1), max(by1 - by0, 1)
    alpha = Image.linear_gradient("L").rotate(180).resize((bw, bh))
    alpha = alpha.point([v * 110 // 255 for v in range(256)])
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).polygon(
        [(p.s(x) - bx0, p.s(y) - by0) for x, y in [(x0, y1), *pts, (x1, y1)]], fill=255)
    solid = Image.new("RGBA", (bw, bh), (*color, 0))
    solid.putalpha(Image.composite(alpha, Image.new("L", (bw, bh), 0), mask))
    p.img.alpha_composite(solid, dest=(bx0, by0))
    flat = [(p.s(x), p.s(y)) for x, y in pts]
    p.layer(lambda d: d.line(flat, fill=(*color, 200), width=p.s(5), joint="curve"), blur=3,
            bbox=(x0, y0, x1, y1))
    p.line(pts, color, 2)


def host_card(p: Painter, x0: float, y0: float, x1: float, y1: float, title: str,
              icon: Callable[..., None], st: HostStats, hist: Sequence[float],
              colors: tuple[RGB, RGB, RGB], chart_color: RGB) -> None:
    card(p, x0, y0, x1, y1)
    w = x1 - x0
    header(p, x0 + 22, y0 + 18, title, icon, w - 44)
    cy, r = y0 + 132, 52
    cols = [
        ("CPU", st.cpu_pct, fmt_temp(st.cpu_temp), colors[0]),
        ("RAM", st.ram_pct, fmt_pair(st.ram_used, st.ram_total), colors[1]),
        ("DISK", st.disk_pct, fmt_pair(st.disk_used, st.disk_total), colors[2]),
    ]
    for i, (label, pct, detail, base) in enumerate(cols):
        cx = x0 + w * (1 + 2 * i) / 6
        gauge(p, cx, cy, r, pct, load_color(pct, base), label)
        p.text(cx, cy + r + 30, label, 21, WHITE, bold=True, anchor="mm")
        p.fit_text(cx, cy + r + 58, detail, 18, w / 3 - 8, MUTED, anchor="mm")
    sep_y = y0 + 300
    p.line([(x0 + 20, sep_y), (x1 - 20, sep_y)], (25, 45, 80), 1)
    sparkline(p, x0 + 20, sep_y + 14, x1 - 20, y1 - 46, hist, chart_color, vmin_top=20)
    p.text(x0 + 22, y1 - 22, "CPU Load (24h)", 17, BLUE, anchor="lm")
    cur = "--" if st.cpu_pct is None else f"{st.cpu_pct:.0f}%"
    p.text(x1 - 22, y1 - 22, cur, 18, WHITE, bold=True, anchor="rm")


def _landscape(width: int, height: int, seed: int = 7) -> Image.Image:
    """Bầu trời hoàng hôn + núi + hồ, vẽ thủ tục (dùng khi không có ảnh nền)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    horizon = int(height * .62)
    sky = ((8, 14, 40), (30, 30, 90), (110, 60, 130), (235, 120, 90))
    for yy in range(horizon):
        t = yy / horizon * (len(sky) - 1)
        i = min(int(t), len(sky) - 2)
        d.line([(0, yy), (width, yy)], fill=lerp(sky[i], sky[i + 1], t - i))
    for _ in range(width // 6):
        sx, sy = rnd.randrange(width), rnd.randrange(int(horizon * .55))
        b = rnd.randrange(120, 255)
        d.point((sx, sy), fill=(b, b, b))
    for layer, (col, amp, base) in enumerate((((40, 40, 80), .22, .50), ((18, 22, 48), .16, .58),
                                              ((8, 12, 28), .10, .64))):
        pts = [(0, height)]
        x = 0
        while x <= width:
            pts.append((x, int(height * (base - amp * rnd.random()))))
            x += rnd.randrange(width // 14, width // 6)
        pts += [(width, int(height * base)), (width, height)]
        d.polygon(pts, fill=col)
        del layer
    lake = img.crop((0, 0, width, horizon)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    lake = Image.blend(lake, Image.new("RGB", lake.size, (5, 10, 24)), .65)
    img.paste(lake.crop((0, 0, width, height - int(height * .66))), (0, int(height * .66)))
    return img


def clock_card(p: Painter, x0: float, y0: float, x1: float, y1: float, snap: Snapshot,
               background: str) -> None:
    w, h = p.s(x1 - x0), p.s(y1 - y0)
    bg: Image.Image | None = None
    if background:
        try:
            bg = Image.open(Path(background).expanduser()).convert("RGB")
            bg = bg.resize((w, h), Image.Resampling.LANCZOS)
        except OSError as e:
            log.warning("render: không mở được ảnh nền %s: %s", background, e)
    if bg is None:
        bg = _landscape_cached(w, h)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=p.s(18), fill=255)
    p.img.paste(bg, (p.s(x0), p.s(y0)), mask)
    p.rrect(x0, y0, x1, y1, 18, outline=CARD_BORDER, width=1.5)

    cx = (x0 + x1) / 2
    now = snap.now
    p.layer(lambda d: d.text((p.s(cx), p.s(y0 + 80)), now.strftime("%H:%M"),
                             font=p.font(92, True), fill=(120, 190, 255, 160),
                             anchor="mm"), blur=6, bbox=(x0, y0 + 30, x1, y0 + 130))
    p.text(cx, y0 + 80, now.strftime("%H:%M"), 92, WHITE, bold=True, anchor="mm")
    p.text(cx, y0 + 152, f"{WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y}", 27, WHITE,
           anchor="mm")

    for i, (name, st, icon) in enumerate(((snap.ha_title, snap.ha, icon_home),
                                          (snap.nas_title, snap.nas, icon_nas))):
        top = y0 + 236 + i * 104
        p.rrect(x0 + 14, top, x1 - 14, top + 90, 16, fill=(10, 22, 48, 215))
        p.rrect(x0 + 14, top, x1 - 14, top + 90, 16, outline=(40, 90, 160, 255), width=1.2)
        icon(p, x0 + 32, top + 18, 54)
        p.fit_text(x0 + 104, top + 30, pretty_name(name), 23,
                   x1 - x0 - 130, WHITE, bold=True, anchor="lm")
        ok = st.online
        p.circle(x0 + 112, top + 62, 7, ONLINE if ok else OFFLINE)
        p.text(x0 + 126, top + 62, "Đang hoạt động" if ok else "Mất kết nối", 18,
               ONLINE if ok else OFFLINE, anchor="lm")


@lru_cache(maxsize=2)
def _landscape_cached(w: int, h: int) -> Image.Image:
    return _landscape(w, h)


def temp_icon(source: str) -> Callable[..., None]:
    """Biểu tượng theo nguồn: chip cho CPU, card cho GPU, ổ cứng cho disk, còn lại nhiệt kế."""
    if source.endswith(":gpu") or "gpu" in source:
        return icon_gpu
    if source.endswith(":disk") or any(k in source for k in ("drive", "disk", "hdd", "nvme")):
        return icon_hdd
    if source.endswith(":cpu") or any(k in source for k in ("cpu", "processor")):
        return icon_chip
    return lambda p, x, y, s: icon_thermo(p, x + s * .1, y, s * .85)


def temp_card(p: Painter, x0: float, y0: float, x1: float, y1: float,
              rows: Sequence[TempRow], tmax: float) -> None:
    card(p, x0, y0, x1, y1)
    header(p, x0 + 18, y0 + 18, "NHIỆT ĐỘ", icon_thermo, x1 - x0 - 36)
    n = max(len(rows), 1)
    step = (y1 - y0 - 90) / n
    for i, row in enumerate(rows):
        top = y0 + 80 + i * step
        temp_icon(row.source)(p, x0 + 20, top + 6, 26)
        p.fit_text(x0 + 56, top + 19, row.label, 20, x1 - x0 - 140, WHITE, anchor="lm")
        frac = 0.0 if row.value is None else row.value / tmax
        col = temp_color(frac) if row.value is not None else MUTED
        p.text(x1 - 18, top + 19, fmt_temp(row.value), 21, col, bold=True, anchor="rm")
        by = top + 50
        p.rrect(x0 + 20, by, x1 - 18, by + 12, 6, fill=TRACK)
        if row.value is not None:
            fill_w = max((x1 - x0 - 38) * min(frac, 1.0), 12)
            p.gradient_bar(x0 + 20, by, x0 + 20 + fill_w, by + 12, 6,
                           temp_color(frac * 0.6), col)
        if i < len(rows) - 1:
            p.line([(x0 + 16, top + step - 8), (x1 - 16, top + step - 8)], (25, 45, 80), 1)


def net_card(p: Painter, x0: float, y0: float, x1: float, y1: float, net: NetStats) -> None:
    card(p, x0, y0, x1, y1)
    header(p, x0 + 18, y0 + 18, "MẠNG", icon_wifi, x1 - x0 - 36)
    for i, (label, val, hist, color, up) in enumerate(
            (("Download", net.down_mbps, net.down_hist, BLUE, False),
             ("Upload", net.up_mbps, net.up_hist, PINK, True))):
        top = y0 + 76 + i * 180
        icon_arrow(p, x0 + 18, top + 2, 20, color, up)
        p.text(x0 + 46, top + 12, label, 19, color, anchor="lm")
        p.fit_text(x0 + 18, top + 52, fmt_mbps(val), 28, x1 - x0 - 36, WHITE, bold=True,
                   anchor="lm")
        sparkline(p, x0 + 18, top + 80, x1 - 18, top + 140, hist or [0.0], color)


def weather_card(p: Painter, x0: float, y0: float, x1: float, y1: float, w: Weather) -> None:
    card(p, x0, y0, x1, y1)
    desc, kind = WMO.get(w.code if w.code is not None else -1, ("--", "cloud"))
    header(p, x0 + 18, y0 + 18, "THỜI TIẾT",
           lambda pp, x, y, s: icon_weather(pp, x, y, s, kind, w.is_day), x1 - x0 - 36)
    cx = (x0 + x1) / 2
    p.fit_text(cx, y0 + 90, w.city, 21, x1 - x0 - 30, WHITE, anchor="mm")
    icon_weather(p, x0 + 16, y0 + 116, 64, kind, w.is_day)
    temp = "--" if w.temp is None else f"{w.temp:.0f}°C"
    p.fit_text(x1 - 16, y0 + 152, temp, 46, x1 - x0 - 100, WHITE, bold=True, anchor="rm")
    p.fit_text(x1 - 16, y0 + 200, desc, 19, x1 - x0 - 100, MUTED, anchor="rm")
    p.line([(x0 + 16, y0 + 236), (x1 - 16, y0 + 236)], (25, 45, 80), 1)
    icon_drop(p, x0 + 18, y0 + 262, 22)
    hum = "--" if w.humidity is None else f"{w.humidity:.0f}%"
    p.text(x0 + 50, y0 + 275, "Độ ẩm", 19, MUTED, anchor="lm")
    p.text(x1 - 16, y0 + 275, hum, 20, WHITE, bold=True, anchor="rm")
    icon_wind(p, x0 + 18, y0 + 314, 22)
    wind = "--" if w.wind_kmh is None else f"{w.wind_kmh:.0f} km/h"
    p.text(x0 + 50, y0 + 326, "Gió", 19, MUTED, anchor="lm")
    p.text(x1 - 16, y0 + 326, wind, 20, WHITE, bold=True, anchor="rm")


# ------------------------------------------------------------------------- main


def render(snap: Snapshot, width: int = 1920, height: int = 462, *,
           font_regular: str, font_bold: str, background: str = "") -> Image.Image:
    """Dựng toàn bộ dashboard. Bố cục co giãn theo ``width`` x ``height``."""
    log.debug("render: %dx%d lúc %s", width, height, snap.now)
    with RENDER_LOCK:
        return _render(snap, width, height, font_regular, font_bold, background)


def _render(snap: Snapshot, width: int, height: int, font_regular: str, font_bold: str,
            background: str) -> Image.Image:
    p = Painter(1920, 462, font_regular, font_bold)
    # Bề rộng các thẻ (tổng 1840 + 5 khe 12px + lề 10px mỗi bên = 1920)
    widths = (350, 425, 425, 235, 200, 205)
    xs = [10.0]
    for wd in widths:
        xs.append(xs[-1] + wd + 12)
    y0, y1 = 10, 452
    box = [(xs[i], xs[i] + widths[i]) for i in range(len(widths))]

    clock_card(p, box[0][0], y0, box[0][1], y1, snap, background)
    host_card(p, box[1][0], y0, box[1][1], y1, snap.ha_title, icon_home, snap.ha,
              snap.ha_hist, (CYAN, BLUE, GREEN), CYAN)
    host_card(p, box[2][0], y0, box[2][1], y1, snap.nas_title, icon_nas, snap.nas,
              snap.nas_hist, (CYAN, GREEN, GREEN), (0, 220, 200))
    temp_card(p, box[3][0], y0, box[3][1], y1, snap.temps, snap.temp_max)
    net_card(p, box[4][0], y0, box[4][1], y1, snap.net)
    weather_card(p, box[5][0], y0, box[5][1], y1, snap.weather)
    img = p.finish()
    if (width, height) != img.size:
        img = img.resize((width, height), Image.Resampling.LANCZOS)
    return img

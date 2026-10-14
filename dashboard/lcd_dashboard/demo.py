"""Số liệu giả để xem thử giao diện."""

from __future__ import annotations

import math
from datetime import datetime

from .config import Config
from .models import HostStats, NetStats, Snapshot, TempRow, Weather


def demo_snapshot(cfg: Config) -> Snapshot:
    """Số liệu giả giống ảnh mẫu — để xem thử giao diện khi chưa nối nguồn thật."""
    gb, tb = 2**30, 2**40
    wave = [30 + 12 * math.sin(i / 7) + 8 * math.sin(i / 2.3) for i in range(288)]
    net = [60 + 25 * math.sin(i / 4) + 15 * math.sin(i / 1.7) for i in range(60)]
    return Snapshot(
        now=datetime.now(),
        ha_title=cfg.home_assistant.title, nas_title=cfg.nas.title,
        ha=HostStats(True, 28, 52, 46, 3.7 * gb, 8 * gb, 62, 58 * gb, 93 * gb),
        nas=HostStats(True, 32, 48, 58, 4.6 * gb, 8 * gb, 71, 5.2 * tb, 7.3 * tb, 38),
        ha_hist=wave, nas_hist=[v + 4 for v in wave[::-1]],
        temps=[TempRow(label, v, src) for (label, src), v in
               zip(cfg.temperature.rows, (52, 46, 38, 41, 35), strict=False)],
        temp_max=cfg.temperature.max_temp,
        net=NetStats(92.4, 23.6, net, [v / 4 for v in net[::-1]]),
        weather=Weather(cfg.weather.city, 26, 78, 12, 2, True),
    )

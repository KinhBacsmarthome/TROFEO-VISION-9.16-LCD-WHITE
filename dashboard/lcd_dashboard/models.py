"""Dữ liệu một khung hình dashboard — chỉ dataclass, không I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HostStats:
    """Một máy (Home Assistant hoặc NAS)."""

    online: bool = False
    cpu_pct: float | None = None
    cpu_temp: float | None = None
    ram_pct: float | None = None
    ram_used: float | None = None    # byte
    ram_total: float | None = None   # byte
    disk_pct: float | None = None
    disk_used: float | None = None   # byte
    disk_total: float | None = None  # byte
    disk_temp: float | None = None


@dataclass
class TempRow:
    label: str
    value: float | None


@dataclass
class NetStats:
    down_mbps: float | None = None
    up_mbps: float | None = None
    down_hist: list[float] = field(default_factory=list)
    up_hist: list[float] = field(default_factory=list)


@dataclass
class Weather:
    city: str = ""
    temp: float | None = None
    humidity: float | None = None
    wind_kmh: float | None = None
    code: int | None = None
    is_day: bool = True


@dataclass
class Snapshot:
    now: datetime
    ha_title: str = "HOME ASSISTANT"
    nas_title: str = "NAS SYNOLOGY"
    ha: HostStats = field(default_factory=HostStats)
    nas: HostStats = field(default_factory=HostStats)
    ha_hist: list[float] = field(default_factory=list)
    nas_hist: list[float] = field(default_factory=list)
    temps: list[TempRow] = field(default_factory=list)
    temp_max: float = 90.0
    net: NetStats = field(default_factory=NetStats)
    weather: Weather = field(default_factory=Weather)

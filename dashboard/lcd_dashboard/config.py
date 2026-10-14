"""Đọc file cấu hình TOML thành các dataclass."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Debian 11 (Python 3.9/3.10)
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

log = logging.getLogger(__name__)


@dataclass
class DisplayConfig:
    device_key: str = "0416:5408"
    width: int = 1920
    height: int = 462
    interval: float = 5.0
    sender: str = "auto"          # auto | python | cli
    trcc_command: str = "trcc"
    font_regular: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font_bold: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    background_image: str = ""    # ảnh nền thẻ đồng hồ (để trống = tự vẽ)
    state_dir: str = "~/.local/state/trcc-dashboard"


@dataclass
class HomeAssistantConfig:
    enabled: bool = True
    title: str = "HOME ASSISTANT"
    url: str = "http://homeassistant.local:8123"
    token: str = ""
    verify_ssl: bool = True
    entities: dict[str, str] = field(default_factory=dict)


@dataclass
class NasConfig:
    enabled: bool = True
    title: str = "NAS SYNOLOGY"
    source: str = "synology"      # synology | home_assistant
    url: str = "https://synology.local:5001"
    username: str = ""
    password: str = ""
    verify_ssl: bool = False
    volume: str = ""              # để trống = cộng tất cả volume
    entities: dict[str, str] = field(default_factory=dict)


@dataclass
class TemperatureConfig:
    # Mỗi dòng: [nhãn, nguồn]. Nguồn: "local:cpu", "local:gpu", "nas:cpu",
    # "nas:disk", "ha:cpu" hoặc entity id Home Assistant ("sensor.xxx").
    rows: list[list[str]] = field(default_factory=lambda: [
        ["CPU", "local:cpu"], ["GPU", "local:gpu"], ["NAS HDD", "nas:disk"],
    ])
    max_temp: float = 90.0


@dataclass
class NetworkConfig:
    source: str = "local"         # local | home_assistant
    interface: str = ""           # để trống = tổng mọi card mạng (trừ lo)
    download_entity: str = ""
    upload_entity: str = ""


@dataclass
class WeatherConfig:
    enabled: bool = True
    city: str = "Hà Nội"
    latitude: float = 21.0285
    longitude: float = 105.8542
    refresh_minutes: float = 10.0


@dataclass
class Config:
    display: DisplayConfig = field(default_factory=DisplayConfig)
    home_assistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)
    nas: NasConfig = field(default_factory=NasConfig)
    temperature: TemperatureConfig = field(default_factory=TemperatureConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)

    @property
    def state_dir(self) -> Path:
        return Path(self.display.state_dir).expanduser()


_SECTIONS: dict[str, type] = {
    "display": DisplayConfig,
    "home_assistant": HomeAssistantConfig,
    "nas": NasConfig,
    "temperature": TemperatureConfig,
    "network": NetworkConfig,
    "weather": WeatherConfig,
}


def _fill(cls: type, raw: dict[str, Any], section: str) -> Any:
    known = {f.name for f in fields(cls)}
    for key in raw.keys() - known:
        log.warning("config: khoá lạ [%s].%s — bỏ qua", section, key)
    return cls(**{k: v for k, v in raw.items() if k in known})


def load_config(path: Path | None) -> Config:
    """Đọc ``path``; không có file thì dùng giá trị mặc định."""
    if path is None:
        log.info("config: không có file cấu hình, dùng mặc định")
        return Config()
    log.info("config: đọc %s", path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    kwargs = {name: _fill(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()}
    for key in raw.keys() - _SECTIONS.keys():
        log.warning("config: mục lạ [%s] — bỏ qua", key)
    return Config(**kwargs)

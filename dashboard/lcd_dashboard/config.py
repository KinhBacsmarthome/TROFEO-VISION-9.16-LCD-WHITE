"""Đọc file cấu hình TOML thành các dataclass."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Debian 11 (Python 3.9/3.10)
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("~/.config/trcc-dashboard/config.toml").expanduser()

# Các "ô" của một thẻ máy (Home Assistant / NAS) có thể gán entity.
HOST_SLOTS = ("cpu", "cpu_temp", "ram_pct", "ram_used", "ram_free", "ram_total",
              "disk_pct", "disk_used", "disk_free", "disk_total", "disk_temp")


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


class ConfigError(ValueError):
    pass


def _fill(cls: type, raw: dict[str, Any], section: str) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"[{section}] phải là một bảng")
    out: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        default = getattr(cls(), f.name)
        out[f.name] = _coerce(value, default, f"{section}.{f.name}")
    for key in raw.keys() - out.keys():
        log.warning("config: khoá lạ [%s].%s — bỏ qua", section, key)
    return cls(**out)


def _coerce(value: Any, default: Any, where: str) -> Any:
    """Ép kiểu giá trị theo kiểu của giá trị mặc định; sai kiểu thì báo ``ConfigError``."""
    try:
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise TypeError
            return value
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            if not isinstance(value, str):
                raise TypeError
            return value
        if isinstance(default, dict):
            return {str(k): str(v) for k, v in dict(value).items() if str(v).strip()}
        if isinstance(default, list):
            return [[str(c) for c in row] for row in value]
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{where}: giá trị {value!r} không hợp lệ") from e
    return value


def config_from_dict(raw: dict[str, Any]) -> Config:
    """Dựng ``Config`` từ dict (đọc từ TOML hoặc gửi lên từ trang web)."""
    kwargs = {name: _fill(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()}
    for key in raw.keys() - _SECTIONS.keys():
        log.warning("config: mục lạ [%s] — bỏ qua", key)
    cfg = Config(**kwargs)
    for section in (cfg.home_assistant, cfg.nas):
        for key in section.entities.keys() - set(HOST_SLOTS):
            log.warning("config: ô entity lạ %r — bỏ qua", key)
            del section.entities[key]
    if cfg.display.interval < 1:
        raise ConfigError("display.interval phải ≥ 1 giây")
    if cfg.display.sender not in ("auto", "python", "cli"):
        raise ConfigError("display.sender phải là auto, python hoặc cli")
    if cfg.nas.source not in ("synology", "home_assistant"):
        raise ConfigError("nas.source phải là synology hoặc home_assistant")
    if cfg.network.source not in ("local", "home_assistant"):
        raise ConfigError("network.source phải là local hoặc home_assistant")
    for row in cfg.temperature.rows:
        if len(row) != 2:
            raise ConfigError("mỗi dòng temperature.rows phải là [nhãn, nguồn]")
    return cfg


def config_to_dict(cfg: Config) -> dict[str, Any]:
    return {name: asdict(getattr(cfg, name)) for name in _SECTIONS}


def load_config(path: Path | None) -> Config:
    """Đọc ``path``; không có file thì dùng giá trị mặc định."""
    if path is None or not path.exists():
        log.info("config: không có file cấu hình (%s), dùng mặc định", path)
        return Config()
    log.info("config: đọc %s", path)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return config_from_dict(raw)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # chuỗi JSON cũng là chuỗi TOML hợp lệ
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"không ghi được kiểu {type(v).__name__} ra TOML")


def dumps_toml(data: dict[str, Any]) -> str:
    """Ghi dict 2 tầng (mục → khoá, có thể có bảng con) ra TOML."""
    out: list[str] = ["# Cấu hình dashboard Trofeo Vision 9.16 LCD (ghi bởi trang cấu hình)\n"]
    for section, values in data.items():
        out.append(f"[{section}]")
        tables = {k: v for k, v in values.items() if isinstance(v, dict)}
        for key, value in values.items():
            if key not in tables:
                out.append(f"{key} = {_toml_value(value)}")
        out.append("")
        for key, table in tables.items():
            out.append(f"[{section}.{key}]")
            out.extend(f"{k} = {_toml_value(v)}" for k, v in table.items())
            out.append("")
    return "\n".join(out)


def save_config(cfg: Config, path: Path) -> None:
    """Ghi cấu hình (nguyên tử, quyền 600 vì chứa token/mật khẩu)."""
    log.info("config: ghi %s", path)
    text = dumps_toml(config_to_dict(cfg))
    tomllib.loads(text)  # tự kiểm tra: file ghi ra phải đọc lại được
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)

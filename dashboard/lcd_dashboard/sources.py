"""Nguồn số liệu: Home Assistant, Synology DSM, máy cục bộ, Open-Meteo.

Chỉ dùng thư viện chuẩn (``urllib``) cho HTTP, và ``psutil`` cho máy cục bộ.
Mỗi nguồn tự bắt lỗi của mình: một nguồn hỏng chỉ làm thẻ của nó hiện "--",
không làm dừng cả dashboard.
"""

from __future__ import annotations

import json
import logging
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from .config import Config, HomeAssistantConfig, NasConfig
from .models import HostStats, NetStats, Snapshot, TempRow, Weather

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 5.0

_SIZE_UNITS = {
    "b": 1, "kb": 1e3, "kib": 2**10, "mb": 1e6, "mib": 2**20,
    "gb": 1e9, "gib": 2**30, "tb": 1e12, "tib": 2**40,
}
_RATE_UNITS_TO_MBPS = {
    "bit/s": 1e-6, "kbit/s": 1e-3, "mbit/s": 1.0, "gbit/s": 1e3,
    "b/s": 8e-6, "kb/s": 8e-3, "kib/s": 8 * 2**10 / 1e6,
    "mb/s": 8.0, "mib/s": 8 * 2**20 / 1e6, "gb/s": 8e3,
}


def _ssl_context(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_json(url: str, *, headers: dict[str, str] | None = None,
              verify_ssl: bool = True) -> Any:
    """GET ``url`` và trả về JSON đã parse. Lỗi mạng/HTTP ném ``OSError``."""
    log.debug("http GET %s", url.split("?")[0])
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT,
                                context=_ssl_context(verify_ssl)) as resp:
        return json.load(resp)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(used: float | None, total: float | None) -> float | None:
    if used is None or not total:
        return None
    return used / total * 100.0


class _StateLog:
    """Chỉ ghi log khi một nguồn đổi trạng thái OK ↔ lỗi, tránh spam mỗi 5 giây."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok: bool | None = None

    def success(self) -> None:
        if self.ok is not True:
            log.info("%s: đã kết nối", self.name)
        self.ok = True

    def failure(self, err: object) -> None:
        if self.ok is not False:
            log.warning("%s: lỗi — %s", self.name, err)
        self.ok = False


# --------------------------------------------------------------------------- HA


class HomeAssistant:
    """REST API của Home Assistant (token dài hạn)."""

    def __init__(self, cfg: HomeAssistantConfig) -> None:
        self.url = cfg.url.rstrip("/")
        self.token = cfg.token
        self.verify_ssl = cfg.verify_ssl

    def state(self, entity_id: str) -> tuple[float | None, str]:
        """(giá trị số, đơn vị) của một entity; giá trị None nếu unavailable."""
        data = http_json(
            f"{self.url}/api/states/{urllib.parse.quote(entity_id)}",
            headers={"Authorization": f"Bearer {self.token}"},
            verify_ssl=self.verify_ssl,
        )
        unit = str(data.get("attributes", {}).get("unit_of_measurement") or "")
        return _num(data.get("state")), unit

    def size(self, entity_id: str) -> float | None:
        value, unit = self.state(entity_id)
        if value is None:
            return None
        factor = _SIZE_UNITS.get(unit.lower())
        if factor is None:
            log.warning("ha: %s có đơn vị lạ %r — coi là GiB", entity_id, unit)
            factor = 2**30
        return value * factor

    def rate_mbps(self, entity_id: str) -> float | None:
        value, unit = self.state(entity_id)
        if value is None:
            return None
        factor = _RATE_UNITS_TO_MBPS.get(unit.lower())
        if factor is None:
            log.warning("ha: %s có đơn vị lạ %r — coi là Mbit/s", entity_id, unit)
            factor = 1.0
        return value * factor

    def host_stats(self, entities: dict[str, str]) -> HostStats:
        """Dựng ``HostStats`` từ bảng khoá → entity id (khoá nào thiếu thì bỏ qua)."""
        def val(key: str) -> float | None:
            return self.state(entities[key])[0] if entities.get(key) else None

        def size(key: str) -> float | None:
            return self.size(entities[key]) if entities.get(key) else None

        st = HostStats(online=True)
        st.cpu_pct = val("cpu")
        st.cpu_temp = val("cpu_temp")
        st.disk_temp = val("disk_temp")
        st.ram_used, st.ram_total = self._used_total(
            size("ram_used"), size("ram_free"), size("ram_total"))
        st.disk_used, st.disk_total = self._used_total(
            size("disk_used"), size("disk_free"), size("disk_total"))
        st.ram_pct = val("ram_pct")
        if st.ram_pct is None:
            st.ram_pct = _pct(st.ram_used, st.ram_total)
        st.disk_pct = val("disk_pct")
        if st.disk_pct is None:
            st.disk_pct = _pct(st.disk_used, st.disk_total)
        log.debug("ha: host_stats %s", st)
        return st

    @staticmethod
    def _used_total(used: float | None, free: float | None,
                    total: float | None) -> tuple[float | None, float | None]:
        if total is None and used is not None and free is not None:
            total = used + free
        if used is None and total is not None and free is not None:
            used = total - free
        return used, total


# ---------------------------------------------------------------------- Synology


class SynologyError(OSError):
    pass


class Synology:
    """Web API của DSM 7: tải CPU/RAM, dung lượng volume, nhiệt độ ổ và hệ thống."""

    _SESSION_ERRORS = {105, 106, 107, 119}

    def __init__(self, cfg: NasConfig) -> None:
        self.url = cfg.url.rstrip("/")
        self.username = cfg.username
        self.password = cfg.password
        self.verify_ssl = cfg.verify_ssl
        self.volume = cfg.volume
        self.sid: str | None = None

    def _get(self, params: dict[str, str]) -> Any:
        url = f"{self.url}/webapi/entry.cgi?{urllib.parse.urlencode(params)}"
        return http_json(url, verify_ssl=self.verify_ssl)

    def _login(self) -> None:
        log.info("synology: đăng nhập %s với tài khoản %s", self.url, self.username)
        data = self._get({
            "api": "SYNO.API.Auth", "version": "6", "method": "login",
            "account": self.username, "passwd": self.password,
            "session": "TrccDashboard", "format": "sid",
        })
        if not data.get("success"):
            raise SynologyError(f"đăng nhập thất bại, mã lỗi {data.get('error')}")
        self.sid = data["data"]["sid"]

    def call(self, api: str, method: str, version: str = "1") -> dict[str, Any]:
        for attempt in range(2):
            if self.sid is None:
                self._login()
            data = self._get({"api": api, "version": version, "method": method,
                              "_sid": self.sid or ""})
            if data.get("success"):
                return data.get("data", {})
            code = (data.get("error") or {}).get("code")
            if code in self._SESSION_ERRORS and attempt == 0:
                log.info("synology: phiên hết hạn (mã %s), đăng nhập lại", code)
                self.sid = None
                continue
            raise SynologyError(f"{api}.{method} lỗi mã {code}")
        raise SynologyError(f"{api}.{method}: không đăng nhập được")

    def host_stats(self) -> HostStats:
        st = HostStats(online=True)
        util = self.call("SYNO.Core.System.Utilization", "get")
        cpu = util.get("cpu", {})
        st.cpu_pct = sum(_num(cpu.get(k)) or 0.0
                         for k in ("user_load", "system_load", "other_load"))
        mem = util.get("memory", {})
        st.ram_pct = _num(mem.get("real_usage"))
        total_kb = _num(mem.get("memory_size")) or _num(mem.get("total_real"))
        if total_kb:
            st.ram_total = total_kb * 1024
            if st.ram_pct is not None:
                st.ram_used = st.ram_total * st.ram_pct / 100.0

        try:
            st.cpu_temp = _num(self.call("SYNO.Core.System", "info").get("sys_temp"))
        except (OSError, ValueError) as e:
            log.debug("synology: không đọc được sys_temp: %s", e)

        storage = self.call("SYNO.Storage.CGI.Storage", "load_info")
        used = total = 0.0
        for vol in storage.get("volumes", []):
            if self.volume and vol.get("id") != self.volume:
                continue
            size = vol.get("size", {})
            used += _num(size.get("used")) or 0.0
            total += _num(size.get("total")) or 0.0
        if total:
            st.disk_used, st.disk_total = used, total
            st.disk_pct = used / total * 100.0
        temps = [t for d in storage.get("disks", []) if (t := _num(d.get("temp")))]
        if temps:
            st.disk_temp = max(temps)
        log.debug("synology: host_stats %s", st)
        return st


# ------------------------------------------------------------------------- Local


class LocalMachine:
    """Máy đang cắm màn hình: nhiệt độ CPU/GPU và tốc độ mạng."""

    _CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
    _CPU_LABELS = ("Package id 0", "Tctl", "Tdie")

    def __init__(self, interface: str = "") -> None:
        self.interface = interface
        self._last_io: tuple[float, int, int] | None = None
        self._nvidia_smi = shutil.which("nvidia-smi")

    def cpu_temp(self) -> float | None:
        sensors = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for chip in self._CPU_CHIPS:
            entries = sensors.get(chip)
            if not entries:
                continue
            for entry in entries:
                if entry.label in self._CPU_LABELS:
                    return entry.current
            return max(e.current for e in entries)
        return None

    def gpu_temp(self) -> float | None:
        sensors = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for chip in ("amdgpu", "nouveau", "radeon"):
            if sensors.get(chip):
                return sensors[chip][0].current
        if self._nvidia_smi:
            try:
                out = subprocess.run(
                    [self._nvidia_smi, "--query-gpu=temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3, check=True,
                ).stdout
                return _num(out.splitlines()[0].strip()) if out.strip() else None
            except (OSError, subprocess.SubprocessError) as e:
                log.debug("local: nvidia-smi lỗi: %s", e)
        return None

    def net_rates(self) -> tuple[float | None, float | None]:
        """(download, upload) Mbit/s tính từ lần gọi trước."""
        if self.interface:
            counters = psutil.net_io_counters(pernic=True).get(self.interface)
            if counters is None:
                log.warning("local: không có card mạng %r", self.interface)
                return None, None
            rx, tx = counters.bytes_recv, counters.bytes_sent
        else:
            per = psutil.net_io_counters(pernic=True)
            rx = sum(c.bytes_recv for n, c in per.items() if n != "lo")
            tx = sum(c.bytes_sent for n, c in per.items() if n != "lo")
        now = time.monotonic()
        last, self._last_io = self._last_io, (now, rx, tx)
        if last is None or now <= last[0]:
            return None, None
        dt = now - last[0]
        return (max(rx - last[1], 0) * 8 / dt / 1e6,
                max(tx - last[2], 0) * 8 / dt / 1e6)


# ----------------------------------------------------------------------- Weather


class OpenMeteo:
    """Thời tiết hiện tại từ open-meteo.com (miễn phí, không cần API key)."""

    URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, city: str, lat: float, lon: float, refresh_minutes: float) -> None:
        self.city = city
        self.lat, self.lon = lat, lon
        self.refresh_s = refresh_minutes * 60
        self._cached = Weather(city=city)
        self._fetched_at = 0.0

    def get(self) -> Weather:
        if time.monotonic() - self._fetched_at < self.refresh_s and self._fetched_at:
            return self._cached
        params = urllib.parse.urlencode({
            "latitude": self.lat, "longitude": self.lon, "timezone": "auto",
            "current": "temperature_2m,relative_humidity_2m,weather_code,"
                       "wind_speed_10m,is_day",
        })
        cur = http_json(f"{self.URL}?{params}")["current"]
        self._cached = Weather(
            city=self.city,
            temp=_num(cur.get("temperature_2m")),
            humidity=_num(cur.get("relative_humidity_2m")),
            wind_kmh=_num(cur.get("wind_speed_10m")),
            code=int(cur["weather_code"]) if cur.get("weather_code") is not None else None,
            is_day=bool(cur.get("is_day", 1)),
        )
        self._fetched_at = time.monotonic()
        log.info("weather: %s %.1f°C mã %s", self.city, self._cached.temp or 0,
                 self._cached.code)
        return self._cached


# ----------------------------------------------------------------------- History


class History:
    """Lịch sử CPU 24 giờ (1 mẫu / 5 phút), lưu ra file JSON để giữ qua khởi động lại."""

    def __init__(self, path: Path, period_s: float = 300, maxlen: int = 288) -> None:
        self.path = path
        self.period_s = period_s
        self.maxlen = maxlen
        self.series: dict[str, list[tuple[float, float]]] = {}
        try:
            self.series = {k: [tuple(p) for p in v]  # type: ignore[misc]
                           for k, v in json.loads(path.read_text("utf-8")).items()}
            log.info("history: đọc %s", path)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("history: file %s hỏng (%s), bắt đầu lại", path, e)

    def add(self, key: str, value: float | None, ts: float) -> None:
        if value is None:
            return
        points = self.series.setdefault(key, [])
        cutoff = ts - self.period_s * self.maxlen
        if points and ts - points[-1][0] < self.period_s:
            return
        points.append((ts, value))
        self.series[key] = [p for p in points if p[0] >= cutoff][-self.maxlen:]
        self._save()

    def values(self, key: str, current: float | None) -> list[float]:
        vals = [v for _, v in self.series.get(key, [])]
        return [*vals, current] if current is not None else vals

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.series), "utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("history: không ghi được %s: %s", self.path, e)


# --------------------------------------------------------------------- Collector


class Collector:
    """Gom mọi nguồn thành một ``Snapshot`` cho mỗi khung hình."""

    NET_POINTS = 60

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.ha = HomeAssistant(cfg.home_assistant) if cfg.home_assistant.enabled else None
        nas = cfg.nas
        self.syno = (Synology(nas) if nas.enabled and nas.source == "synology" else None)
        self.local = LocalMachine(cfg.network.interface)
        w = cfg.weather
        self.weather = (OpenMeteo(w.city, w.latitude, w.longitude, w.refresh_minutes)
                        if w.enabled else None)
        self.history = History(cfg.state_dir / "history.json")
        self.down: deque[float] = deque(maxlen=self.NET_POINTS)
        self.up: deque[float] = deque(maxlen=self.NET_POINTS)
        self._log = {n: _StateLog(n) for n in ("home_assistant", "nas", "weather", "network")}
        self._weather = Weather(city=w.city)

    def _guard(self, name: str, fn: Any, default: Any) -> Any:
        try:
            result = fn()
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as e:
            self._log[name].failure(e)
            return default
        self._log[name].success()
        return result

    def _ha_stats(self) -> HostStats:
        if self.ha is None:
            return HostStats()
        return self._guard("home_assistant",
                           lambda: self.ha.host_stats(self.cfg.home_assistant.entities),  # type: ignore[union-attr]
                           HostStats())

    def _nas_stats(self) -> HostStats:
        nas = self.cfg.nas
        if not nas.enabled:
            return HostStats()
        if nas.source == "home_assistant":
            if self.ha is None:
                log.warning("nas: source=home_assistant nhưng [home_assistant] đang tắt")
                return HostStats()
            return self._guard("nas", lambda: self.ha.host_stats(nas.entities),  # type: ignore[union-attr]
                               HostStats())
        return self._guard("nas", self.syno.host_stats, HostStats())  # type: ignore[union-attr]

    def _net(self) -> tuple[float | None, float | None]:
        net = self.cfg.network
        if net.source == "home_assistant" and self.ha is not None:
            ha = self.ha
            return self._guard("network", lambda: (
                ha.rate_mbps(net.download_entity) if net.download_entity else None,
                ha.rate_mbps(net.upload_entity) if net.upload_entity else None,
            ), (None, None))
        return self.local.net_rates()

    def _temp(self, source: str, ha: HostStats, nas: HostStats) -> float | None:
        match source:
            case "local:cpu":
                return self.local.cpu_temp()
            case "local:gpu":
                return self.local.gpu_temp()
            case "nas:cpu":
                return nas.cpu_temp
            case "nas:disk":
                return nas.disk_temp
            case "ha:cpu":
                return ha.cpu_temp
            case "ha:disk":
                return ha.disk_temp
            case _ if self.ha is not None and "." in source:
                ha_client = self.ha
                return self._guard("home_assistant",
                                   lambda: ha_client.state(source)[0], None)
        log.warning("temperature: nguồn lạ %r", source)
        return None

    def collect(self) -> Snapshot:
        now = datetime.now()
        ts = time.time()
        ha = self._ha_stats()
        nas = self._nas_stats()
        self.history.add("ha_cpu", ha.cpu_pct, ts)
        self.history.add("nas_cpu", nas.cpu_pct, ts)

        down, up = self._net()
        if down is not None:
            self.down.append(down)
        if up is not None:
            self.up.append(up)

        if self.weather is not None:
            self._weather = self._guard("weather", self.weather.get, self._weather)

        temps = [TempRow(label, self._temp(src, ha, nas))
                 for label, src in self.cfg.temperature.rows]
        snap = Snapshot(
            now=now,
            ha_title=self.cfg.home_assistant.title,
            nas_title=self.cfg.nas.title,
            ha=ha, nas=nas,
            ha_hist=self.history.values("ha_cpu", ha.cpu_pct),
            nas_hist=self.history.values("nas_cpu", nas.cpu_pct),
            temps=temps,
            temp_max=self.cfg.temperature.max_temp,
            net=NetStats(down, up, list(self.down), list(self.up)),
            weather=self._weather,
        )
        log.debug("collect: %s", snap)
        return snap

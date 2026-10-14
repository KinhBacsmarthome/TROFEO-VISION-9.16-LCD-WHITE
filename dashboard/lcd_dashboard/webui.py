"""Trang web cấu hình: chọn entity Home Assistant / NAS, xem trước, lưu cấu hình.

Chỉ dùng thư viện chuẩn (``http.server``). Mặc định chỉ nghe trên 127.0.0.1.

Bảo vệ:
* Khi nghe trên loopback, header ``Host`` phải là localhost/127.0.0.1/[::1]
  (chặn DNS rebinding).
* Mọi POST phải là ``application/json`` và, nếu có ``Origin``, phải cùng gốc với
  ``Host`` — một trang web lạ mở trong trình duyệt không gửi được cấu hình giả
  (vd. trỏ URL Home Assistant sang máy khác để lấy token).
* Token HA và mật khẩu NAS không bao giờ được gửi ngược về trình duyệt.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import (
    HOST_SLOTS,
    Config,
    ConfigError,
    HomeAssistantConfig,
    NasConfig,
    config_from_dict,
    config_to_dict,
    load_config,
    save_config,
)
from .demo import demo_snapshot
from .render import render

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("webui.html")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_BODY = 1 << 20
LOCKED_DISPLAY_KEYS = ("trcc_command", "state_dir", "font_regular", "font_bold")


@dataclass
class Runtime:
    """Trạng thái dùng chung giữa vòng lặp gửi ảnh và trang web."""

    config_path: Path
    lcd_enabled: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_sent: float | None = None
    last_ok: bool | None = None
    last_message: str = "Chưa gửi khung hình nào"
    sender_mode: str = ""

    def report(self, ok: bool, message: str) -> None:
        with self.lock:
            self.last_sent = time.time()
            self.last_ok = ok
            self.last_message = message

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "lcd_enabled": self.lcd_enabled,
                "last_sent": self.last_sent,
                "last_ok": self.last_ok,
                "message": self.last_message,
                "sender": self.sender_mode,
                "config_path": str(self.config_path),
            }


class _Preview:
    """Bộ gom số liệu riêng cho ảnh xem trước, dựng lại khi cấu hình đổi."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.lock = threading.Lock()
        self._mtime: float | None = -1.0
        self._cfg = Config()
        self._collector: Any = None

    def _mtime_now(self) -> float | None:
        try:
            return self.runtime.config_path.stat().st_mtime
        except FileNotFoundError:
            return None

    def png(self, demo: bool) -> bytes:
        with self.lock:
            mtime = self._mtime_now()
            if mtime != self._mtime:
                log.info("webui: nạp lại cấu hình cho ảnh xem trước")
                self._cfg = load_config(self.runtime.config_path)
                self._collector = None
                self._mtime = mtime
            cfg = self._cfg
            if demo:
                snap = demo_snapshot(cfg)
            else:
                if self._collector is None:
                    from .sources import Collector
                    self._collector = Collector(cfg)
                snap = self._collector.collect()
            d = cfg.display
            img = render(snap, d.width, d.height, font_regular=d.font_regular,
                         font_bold=d.font_bold, background=d.background_image)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


def _public_config(cfg: Config) -> dict[str, Any]:
    """Cấu hình gửi cho trình duyệt — bỏ token/mật khẩu, chỉ báo đã đặt hay chưa."""
    data = config_to_dict(cfg)
    data["home_assistant"]["token"] = ""
    data["nas"]["password"] = ""
    return {
        "config": data,
        "secrets": {"ha_token": bool(cfg.home_assistant.token),
                    "nas_password": bool(cfg.nas.password)},
        "host_slots": list(HOST_SLOTS),
    }


class WebUI:
    def __init__(self, runtime: Runtime, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.runtime = runtime
        self.host = host
        self.preview = _Preview(runtime)
        self.save_lock = threading.Lock()
        handler = type("Handler", (_Handler,), {"ui": self})
        self.server = ThreadingHTTPServer((host, port), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                       name="webui")

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.server.server_address[1]}/"

    def start(self) -> None:
        log.info("webui: mở trang cấu hình tại %s", self.url)
        if self.host not in LOOPBACK_HOSTS:
            log.warning("webui: đang nghe trên %s — ai trong mạng LAN cũng sửa được cấu hình",
                        self.host)
        self.thread.start()

    def stop(self) -> None:
        log.info("webui: dừng")
        self.server.shutdown()

    # ---- các thao tác (gọi từ handler)

    def current(self) -> Config:
        return load_config(self.runtime.config_path)

    def save(self, raw: dict[str, Any]) -> Config:
        with self.save_lock:
            old = self.current()
            # Trộn vào cấu hình cũ: mục/khoá nào không gửi lên thì giữ nguyên.
            merged = config_to_dict(old)
            for section, values in raw.items():
                if isinstance(values, dict) and isinstance(merged.get(section), dict):
                    merged[section].update(values)
                else:
                    merged[section] = values
            raw = merged
            ha = raw.setdefault("home_assistant", {})
            nas = raw.setdefault("nas", {})
            if not ha.get("token"):
                ha["token"] = old.home_assistant.token
            if not nas.get("password"):
                nas["password"] = old.nas.password
            # Các trường trỏ tới lệnh/thư mục trên máy chỉ sửa được bằng tay trong file:
            # trang web (nhất là khi mở cho LAN) không được đổi lệnh mà chương trình chạy.
            display = raw.setdefault("display", {})
            for key in LOCKED_DISPLAY_KEYS:
                display[key] = getattr(old.display, key)
            cfg = config_from_dict(raw)
            save_config(cfg, self.runtime.config_path)
            return cfg

    def ha_client(self, body: dict[str, Any]) -> Any:
        from .sources import HomeAssistant
        saved = self.current().home_assistant
        cfg = HomeAssistantConfig(
            url=str(body.get("url") or saved.url),
            token=str(body.get("token") or saved.token),
            verify_ssl=bool(body.get("verify_ssl", saved.verify_ssl)),
        )
        if not cfg.token:
            raise ConfigError("chưa có token Home Assistant")
        return HomeAssistant(cfg)

    def nas_test(self, body: dict[str, Any]) -> dict[str, Any]:
        from .sources import Synology
        saved = self.current().nas
        cfg = NasConfig(
            url=str(body.get("url") or saved.url),
            username=str(body.get("username") or saved.username),
            password=str(body.get("password") or saved.password),
            verify_ssl=bool(body.get("verify_ssl", saved.verify_ssl)),
            volume=str(body.get("volume", saved.volume)),
        )
        st = Synology(cfg).host_stats()
        return {"cpu_pct": st.cpu_pct, "cpu_temp": st.cpu_temp, "ram_pct": st.ram_pct,
                "disk_pct": st.disk_pct, "disk_used": st.disk_used,
                "disk_total": st.disk_total, "disk_temp": st.disk_temp}


class _Handler(BaseHTTPRequestHandler):
    ui: WebUI
    server_version = "trcc-dashboard"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("webui: %s - %s", self.address_string(), format % args)

    # ---- tiện ích

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        log.warning("webui: %s %s -> %d %s", self.command, self.path, status, message)
        self._json({"ok": False, "error": message}, status)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        name = host[1:].split("]")[0] if host.startswith("[") else host.split(":")[0]
        if self.ui.host in LOOPBACK_HOSTS and name not in LOOPBACK_HOSTS:
            return False
        return bool(host)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin.rstrip("/") == f"http://{self.headers.get('Host', '')}"

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ConfigError("dữ liệu gửi lên quá lớn")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ConfigError("dữ liệu phải là một object JSON")
        return data

    # ---- GET

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error("Host không hợp lệ", HTTPStatus.FORBIDDEN)
            return
        url = urlparse(self.path)
        log.debug("webui: GET %s", url.path)
        try:
            match url.path:
                case "/" | "/index.html":
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                case "/api/config":
                    self._json(_public_config(self.ui.current()))
                case "/api/status":
                    self._json(self.ui.runtime.status())
                case "/api/preview.png":
                    demo = parse_qs(url.query).get("mode", ["live"])[0] == "demo"
                    self._send(200, self.ui.preview.png(demo), "image/png")
                case _:
                    self._error("không có trang này", HTTPStatus.NOT_FOUND)
        except (OSError, ValueError) as e:
            self._error(str(e), HTTPStatus.INTERNAL_SERVER_ERROR)

    # ---- POST

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            self._error("yêu cầu khác nguồn bị chặn", HTTPStatus.FORBIDDEN)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            self._error("Content-Type phải là application/json",
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        log.info("webui: POST %s", self.path)
        try:
            body = self._body()
            match self.path:
                case "/api/config":
                    cfg = self.ui.save(body.get("config", {}))
                    self._json({"ok": True, **_public_config(cfg)})
                case "/api/ha/test":
                    ha = self.ui.ha_client(body)
                    msg = ha.ping()
                    self._json({"ok": True, "message": msg})
                case "/api/ha/entities":
                    self._json({"ok": True, "entities": self.ui.ha_client(body).list_entities()})
                case "/api/nas/test":
                    self._json({"ok": True, "stats": self.ui.nas_test(body)})
                case _:
                    self._error("không có API này", HTTPStatus.NOT_FOUND)
        except ConfigError as e:
            self._error(str(e))
        except json.JSONDecodeError:
            self._error("JSON không hợp lệ")
        except urllib.error.HTTPError as e:
            hint = {401: "token sai hoặc đã hết hạn", 403: "không có quyền",
                    404: "không tìm thấy — kiểm tra lại địa chỉ"}.get(e.code, e.reason)
            self._error(f"máy chủ trả lỗi {e.code}: {hint}", HTTPStatus.BAD_GATEWAY)
        except OSError as e:
            self._error(f"không kết nối được: {e}", HTTPStatus.BAD_GATEWAY)
        except (KeyError, TypeError, ValueError) as e:
            self._error(f"dữ liệu trả về không như mong đợi: {e}", HTTPStatus.BAD_GATEWAY)

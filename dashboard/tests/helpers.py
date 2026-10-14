"""Máy chủ Home Assistant / Synology DSM giả để test, chạy trong luồng nền."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

TOKEN = "TESTTOKEN"

HA_STATES: dict[str, tuple[str, str]] = {
    "sensor.system_monitor_processor_use": ("28", "%"),
    "sensor.system_monitor_processor_temperature": ("52.0", "°C"),
    "sensor.system_monitor_memory_usage": ("46.2", "%"),
    "sensor.system_monitor_memory_use": ("4096", "MiB"),
    "sensor.system_monitor_memory_free": ("4096", "MiB"),
    "sensor.system_monitor_disk_use": ("58", "GiB"),
    "sensor.system_monitor_disk_free": ("35", "GiB"),
    "sensor.nas_memory_usage_real": ("50", "%"),
    "sensor.nas_memory_total_real": ("8", "GB"),
    "sensor.router_download": ("12500", "kB/s"),
    "sensor.broken": ("unavailable", ""),
}


class _HA(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _reply(self, code: int, data: Any = None) -> None:
        body = json.dumps(data).encode() if data is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._reply(401)
        elif self.path == "/api/":
            self._reply(200, {"message": "API running."})
        elif self.path == "/api/states":
            self._reply(200, [{"entity_id": k, "state": v,
                               "attributes": {"unit_of_measurement": u, "friendly_name": k}}
                              for k, (v, u) in HA_STATES.items()])
        else:
            eid = self.path.rsplit("/", 1)[-1]
            if eid in HA_STATES:
                v, u = HA_STATES[eid]
                self._reply(200, {"entity_id": eid, "state": v,
                                  "attributes": {"unit_of_measurement": u}})
            else:
                self._reply(404)


class _DSM(BaseHTTPRequestHandler):
    logins = 0

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        api = q.get("api")
        data: dict[str, Any]
        if api == "SYNO.API.Auth":
            type(self).logins += 1
            data = ({"success": True, "data": {"sid": "SID1"}} if q.get("passwd") == "pw"
                    else {"success": False, "error": {"code": 400}})
        elif q.get("_sid") != "SID1":
            data = {"success": False, "error": {"code": 119}}
        elif api == "SYNO.Core.System.Utilization":
            data = {"success": True, "data": {
                "cpu": {"user_load": 20, "system_load": 10, "other_load": 2},
                "memory": {"real_usage": 50, "memory_size": 8 * 1024 * 1024}}}
        elif api == "SYNO.Core.System":
            data = {"success": True, "data": {"sys_temp": 48}}
        elif api == "SYNO.Storage.CGI.Storage":
            data = {"success": True, "data": {
                "volumes": [{"id": "volume_1", "size": {"total": str(4 * 2**40), "used": str(2**40)}},
                            {"id": "volume_2", "size": {"total": str(4 * 2**40), "used": str(3 * 2**40)}}],
                "disks": [{"temp": 36}, {"temp": 41}]}}
        else:
            data = {"success": False, "error": {"code": 102}}
        body = json.dumps(data).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


class MockServer:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def mock_ha() -> MockServer:
    return MockServer(_HA)


def mock_dsm() -> MockServer:
    _DSM.logins = 0
    return MockServer(_DSM)


def dsm_logins() -> int:
    return _DSM.logins

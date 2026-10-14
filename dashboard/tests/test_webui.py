import http.client
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from helpers import TOKEN, mock_ha
from lcd_dashboard.config import Config, load_config, save_config
from lcd_dashboard.webui import Runtime, WebUI


class WebUITest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = Path(self.tmp.name) / "config.toml"
        cfg = Config()
        cfg.home_assistant.token = "SECRET-TOKEN"
        cfg.nas.password = "SECRET-PASS"
        cfg.display.state_dir = self.tmp.name
        cfg.weather.enabled = False
        save_config(cfg, self.cfg_path)
        self.ui = WebUI(Runtime(config_path=self.cfg_path), "127.0.0.1", 0)
        self.ui.start()
        self.port = self.ui.server.server_address[1]

    def tearDown(self) -> None:
        self.ui.stop()
        self.ui.server.server_close()
        self.tmp.cleanup()

    def req(self, method: str, path: str, body: Any = None,
            headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        out = (resp.status, resp.read())
        conn.close()
        return out

    def test_page_and_status(self) -> None:
        status, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Cấu hình màn hình".encode(), body)
        status, body = self.req("GET", "/api/status")
        self.assertEqual(json.loads(body)["config_path"], str(self.cfg_path))

    def test_secrets_are_never_returned(self) -> None:
        status, body = self.req("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn(b"SECRET", body)
        data = json.loads(body)
        self.assertEqual(data["secrets"], {"ha_token": True, "nas_password": True})

    def test_save_keeps_secrets_merges_and_locks(self) -> None:
        status, _ = self.req("POST", "/api/config", {"config": {
            "home_assistant": {"token": "", "title": "NHÀ"},
            "display": {"trcc_command": "rm -rf /", "interval": 9},
        }})
        self.assertEqual(status, 200)
        cfg = load_config(self.cfg_path)
        self.assertEqual(cfg.home_assistant.token, "SECRET-TOKEN")
        self.assertEqual(cfg.nas.password, "SECRET-PASS")
        self.assertEqual(cfg.home_assistant.title, "NHÀ")
        self.assertEqual(cfg.display.interval, 9)
        self.assertEqual(cfg.display.trcc_command, "trcc")
        self.assertFalse(cfg.weather.enabled)          # mục không gửi lên: giữ nguyên

    def test_invalid_config_is_rejected(self) -> None:
        status, body = self.req("POST", "/api/config", {"config": {"display": {"interval": "x"}}})
        self.assertEqual(status, 400)
        self.assertIn("display.interval", json.loads(body)["error"])

    def test_cross_site_requests_are_blocked(self) -> None:
        status, _ = self.req("POST", "/api/config", {"config": {}},
                             {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/config", body=b"{}",
                     headers={"Content-Type": "text/plain"})
        self.assertEqual(conn.getresponse().status, 415)
        conn.close()
        status, _ = self.req("GET", "/api/config", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_same_origin_is_allowed(self) -> None:
        status, _ = self.req("POST", "/api/config", {"config": {}},
                             {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_preview_png(self) -> None:
        status, body = self.req("GET", "/api/preview.png?mode=demo")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_ha_entities_and_errors(self) -> None:
        srv = mock_ha()
        try:
            status, body = self.req("POST", "/api/ha/entities",
                                    {"url": srv.url, "token": TOKEN})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["entities"])
            status, body = self.req("POST", "/api/ha/test", {"url": srv.url, "token": "BAD"})
            self.assertEqual(status, 502)
            self.assertIn("401", json.loads(body)["error"])
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()

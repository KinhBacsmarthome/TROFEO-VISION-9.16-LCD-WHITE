import tempfile
import unittest
from pathlib import Path

from helpers import TOKEN, dsm_logins, mock_dsm, mock_ha
from lcd_dashboard.config import Config, HomeAssistantConfig, NasConfig
from lcd_dashboard.sources import Collector, History, HomeAssistant, Synology

GIB = 2**30


class HomeAssistantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = mock_ha()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.close()

    def client(self, token: str = TOKEN) -> HomeAssistant:
        return HomeAssistant(HomeAssistantConfig(url=self.srv.url, token=token))

    def test_host_stats_converts_units_and_derives_totals(self) -> None:
        st = self.client().host_stats({
            "cpu": "sensor.system_monitor_processor_use",
            "cpu_temp": "sensor.system_monitor_processor_temperature",
            "ram_pct": "sensor.system_monitor_memory_usage",
            "ram_used": "sensor.system_monitor_memory_use",
            "ram_free": "sensor.system_monitor_memory_free",
            "disk_used": "sensor.system_monitor_disk_use",
            "disk_free": "sensor.system_monitor_disk_free",
        })
        self.assertTrue(st.online)
        self.assertEqual((st.cpu_pct, st.cpu_temp, st.ram_pct), (28, 52, 46.2))
        self.assertEqual((st.ram_used, st.ram_total), (4 * GIB, 8 * GIB))
        self.assertEqual((st.disk_used, st.disk_total), (58 * GIB, 93 * GIB))
        self.assertAlmostEqual(st.disk_pct or 0, 58 / 93 * 100)

    def test_used_is_derived_from_pct_and_total(self) -> None:
        st = self.client().host_stats({"ram_pct": "sensor.nas_memory_usage_real",
                                       "ram_total": "sensor.nas_memory_total_real"})
        self.assertEqual((st.ram_total, st.ram_used), (8e9, 4e9))

    def test_unavailable_state_is_none(self) -> None:
        self.assertEqual(self.client().state("sensor.broken"), (None, ""))

    def test_rate(self) -> None:
        self.assertAlmostEqual(self.client().rate_mbps("sensor.router_download") or 0, 100.0)

    def test_list_and_ping(self) -> None:
        ha = self.client()
        self.assertEqual(ha.ping(), "API running.")
        ids = [e["entity_id"] for e in ha.list_entities()]
        self.assertEqual(ids, sorted(ids))
        self.assertIn("sensor.system_monitor_processor_use", ids)

    def test_bad_token_raises(self) -> None:
        with self.assertRaises(OSError):
            self.client("WRONG").ping()

    def test_no_entities_still_checks_connection(self) -> None:
        with self.assertRaises(OSError):
            self.client("WRONG").host_stats({})
        self.assertTrue(self.client().host_stats({}).online)


class SynologyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = mock_dsm()

    def tearDown(self) -> None:
        self.srv.close()

    def nas(self, password: str = "pw", volume: str = "") -> Synology:
        return Synology(NasConfig(url=self.srv.url, username="u", password=password,
                                  volume=volume))

    def test_host_stats(self) -> None:
        st = self.nas().host_stats()
        self.assertEqual((st.cpu_pct, st.ram_pct, st.cpu_temp, st.disk_temp), (32, 50, 48, 41))
        self.assertEqual((st.disk_used, st.disk_total), (4 * 2**40, 8 * 2**40))
        self.assertEqual(st.ram_total, 8 * GIB)

    def test_single_volume(self) -> None:
        st = self.nas(volume="volume_2").host_stats()
        self.assertEqual(st.disk_pct, 75)

    def test_relogin_when_session_expires(self) -> None:
        nas = self.nas()
        nas.sid = "EXPIRED"
        self.assertEqual(nas.host_stats().cpu_pct, 32)
        self.assertEqual(dsm_logins(), 1)

    def test_bad_password(self) -> None:
        with self.assertRaises(OSError):
            self.nas("nope").host_stats()


class HistoryTest(unittest.TestCase):
    def test_sampling_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.json"
            h = History(path, period_s=300, maxlen=3)
            for ts, v in ((0, 1), (100, 2), (300, 3), (600, 4), (900, 5)):
                h.add("cpu", v, ts)
            h.add("cpu", None, 1200)
            self.assertEqual(h.values("cpu", None), [3, 4, 5])
            self.assertEqual(History(path).values("cpu", 9), [3, 4, 5, 9])

    def test_corrupt_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.json"
            path.write_text("{not json", "utf-8")
            self.assertEqual(History(path).values("cpu", None), [])


class CollectorTest(unittest.TestCase):
    def test_unconfigured_sources_are_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.display.state_dir = tmp
            cfg.weather.enabled = False
            cfg.temperature.rows = [["X", "khong:hop:le"], ["N", "nas:disk"]]
            snap = Collector(cfg).collect()
            self.assertFalse(snap.ha.online)
            self.assertFalse(snap.nas.online)
            self.assertEqual([t.value for t in snap.temps], [None, None])

    def test_nas_through_home_assistant(self) -> None:
        srv = mock_ha()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Config()
                cfg.display.state_dir = tmp
                cfg.weather.enabled = False
                cfg.home_assistant.url = srv.url
                cfg.home_assistant.token = TOKEN
                cfg.nas.source = "home_assistant"
                cfg.nas.entities = {"ram_pct": "sensor.nas_memory_usage_real"}
                cfg.temperature.rows = [["T", "sensor.system_monitor_processor_temperature"]]
                snap = Collector(cfg).collect()
                self.assertTrue(snap.nas.online)
                self.assertEqual(snap.nas.ram_pct, 50)
                self.assertEqual(snap.temps[0].value, 52)
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()

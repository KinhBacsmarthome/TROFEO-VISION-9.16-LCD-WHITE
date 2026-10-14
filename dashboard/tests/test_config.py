import stat
import tempfile
import unittest
from pathlib import Path

from lcd_dashboard.config import (
    Config,
    ConfigError,
    config_from_dict,
    config_to_dict,
    load_config,
    save_config,
)


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "config.toml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self) -> None:
        self.assertEqual(load_config(self.path), Config())

    def test_round_trip_keeps_every_value(self) -> None:
        cfg = Config()
        cfg.home_assistant.token = 'abc"def\\ghi\nline2 — tiếng Việt'
        cfg.home_assistant.entities = {"cpu": "sensor.a", "disk_pct": "sensor.b"}
        cfg.weather.city = "Bắc Ninh"
        cfg.weather.latitude = 21.1861
        cfg.temperature.rows = [["Phòng \"khách\"", "sensor.x"], ["CPU", "local:cpu"]]
        cfg.display.interval = 3
        save_config(cfg, self.path)
        self.assertEqual(load_config(self.path), cfg)

    def test_saved_file_is_private(self) -> None:
        save_config(Config(), self.path)
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_wrong_types_are_rejected(self) -> None:
        for raw in ({"display": {"interval": "abc"}},
                    {"display": {"interval": 0}},
                    {"home_assistant": {"enabled": "yes"}},
                    {"nas": {"source": "ftp"}},
                    {"temperature": {"rows": [["only-one"]]}},
                    {"display": "not a table"}):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                config_from_dict(raw)

    def test_unknown_entity_slots_are_dropped(self) -> None:
        cfg = config_from_dict({"home_assistant": {"entities": {
            "cpu": "sensor.a", 'evil"]\n[x': "y", "ram_pct": ""}}})
        self.assertEqual(cfg.home_assistant.entities, {"cpu": "sensor.a"})

    def test_numbers_are_coerced(self) -> None:
        cfg = config_from_dict({"display": {"interval": 7, "width": 1920.0}})
        self.assertEqual(cfg.display.interval, 7.0)
        self.assertIsInstance(cfg.display.width, int)

    def test_example_config_parses(self) -> None:
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        cfg = load_config(example)
        self.assertEqual(cfg.display.device_key, "0416:5408")
        self.assertIn("cpu", cfg.home_assistant.entities)

    def test_to_dict_has_all_sections(self) -> None:
        self.assertEqual(set(config_to_dict(Config())),
                         {"display", "home_assistant", "nas", "temperature", "network",
                          "weather"})


if __name__ == "__main__":
    unittest.main()

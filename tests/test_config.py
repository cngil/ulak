import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily import main
from ulak_app.config import ConfigError, default_path, example, load_theme, validate


class ConfigTests(unittest.TestCase):
    def test_both_starter_profiles_validate(self):
        for lang in ("tr", "en"):
            self.assertEqual(validate(example(lang))["language"], lang)

    def test_invalid_values_are_rejected(self):
        for key, bad in [("language", "xx"), ("hours", 0), ("max_articles", 9),
                         ("timezone", "Nowhere/Unknown"), ("hours", True)]:
            with self.subTest(key=key, bad=bad), self.assertRaises(ConfigError):
                config = example()
                config[key] = bad
                validate(config)

    def test_duplicate_source_ids_and_all_disabled_rejected(self):
        config = example()
        config["sources"].append(copy.deepcopy(config["sources"][0]))
        with self.assertRaises(ConfigError):
            validate(config)
        config = example()
        for source in config["sources"]:
            source["enabled"] = False
        with self.assertRaises(ConfigError):
            validate(config)

    def test_theme_validation(self):
        self.assertEqual(load_theme("classic")["name"], "classic")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"theme.json"
            path.write_text('{"name":"bad"}')
            with self.assertRaises(ConfigError):
                load_theme(str(path))

    def test_init_never_overwrites_local_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.json"
            self.assertEqual(main(["--init", "--config", str(path)]), 0)
            self.assertEqual(json.loads(path.read_text())['language'],'en')
            original = path.read_bytes()
            self.assertEqual(main(["--init", "--config", str(path)]), 2)
            self.assertEqual(path.read_bytes(), original)

    def test_unconfigured_install_defaults_to_english(self):
        self.assertEqual(example()['language'],'en')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('ulak_app.config.ROOT',root),patch.dict('os.environ',{'XDG_CONFIG_HOME':str(root/'user')}):
                self.assertEqual(default_path(),root/'examples/en.json')

    def test_cli_zero_hours_rejected_before_network(self):
        with patch("daily.collect") as fetch:
            self.assertEqual(main(["--config", "examples/tr.json", "--hours", "0"]), 2)
            fetch.assert_not_called()

"""Tests chargement config.toml."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from annie.config import AnnieConfig, reload_config


class ConfigLoadTests(unittest.TestCase):
    def tearDown(self) -> None:
        reload_config()

    def test_flat_keys_backward_compat(self) -> None:
        toml = """
player = "mpv"
category = "1_2"
filter = "2"
skip_recap_movies = true
preferred_groups = ["Erai-raws"]
subtitles_enabled = false
default_sub_lang = "fr"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.player, "mpv")
        self.assertEqual(cfg.category, "1_2")
        self.assertEqual(cfg.filter_code, "2")
        self.assertTrue(cfg.skip_recap_movies)
        self.assertEqual(cfg.preferred_groups, ["Erai-raws"])
        self.assertFalse(cfg.subtitles_enabled)
        self.assertEqual(cfg.default_sub_lang, "fr")

    def test_nested_sections(self) -> None:
        toml = """
[player]
command = "mpv"

[nyaa]
search_pages = 3
parallel = 6

[mal]
enabled = false

[catalog]
min_seeders_strict = 5
preferred_group_bonus = 20
preferred_groups = ["SubsPlease"]

[subtitles]
fetch_timeout = 30.0

[ui]
seeders_highlight = 100
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.player, "mpv")
        self.assertEqual(cfg.nyaa.search_pages, 3)
        self.assertEqual(cfg.nyaa.parallel, 6)
        self.assertFalse(cfg.mal.enabled)
        self.assertEqual(cfg.catalog.min_seeders_strict, 5)
        self.assertEqual(cfg.catalog.preferred_group_bonus, 20)
        self.assertEqual(cfg.subtitles.fetch_timeout, 30.0)
        self.assertEqual(cfg.ui.seeders_highlight, 100)

    def test_metadata_mode_off(self) -> None:
        toml = """
[metadata]
mode = "off"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.mode, "off")
        self.assertFalse(cfg.metadata.enabled)

    def test_metadata_mode_mal(self) -> None:
        toml = """
[metadata]
mode = "mal"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.mode, "mal")
        self.assertTrue(cfg.metadata.enabled)
        self.assertEqual(cfg.metadata.provider, "mal")
        self.assertEqual(cfg.metadata.structure, "franchise")

    def test_metadata_mode_anilist_override_structure(self) -> None:
        toml = """
[metadata]
mode = "anilist"
structure = "allanime"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.provider, "anilist")
        self.assertEqual(cfg.metadata.structure, "allanime")

    def test_metadata_top_level_string_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('metadata = "off"\n', encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.mode, "off")
        self.assertFalse(cfg.metadata.enabled)

    def test_metadata_mode_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[metadata]\nmode = \"auto\"\n", encoding="utf-8")
            with (
                mock.patch("annie.config.CONFIG_FILE", path),
                mock.patch.dict("os.environ", {"ANNIE_METADATA_MODE": "off"}),
            ):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.metadata.mode, "off")
        self.assertFalse(cfg.metadata.enabled)

    def test_preferred_resolution(self) -> None:
        toml = """
[catalog]
preferred_resolution = "1080p"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertEqual(cfg.catalog.preferred_resolution, "1080p")


class ConfigWriteTests(unittest.TestCase):
    def tearDown(self) -> None:
        reload_config()

    def test_set_config_value_keeps_other_keys(self) -> None:
        from annie.user_config import set_config_value

        toml = """
[subtitles]
enabled = true
api_key = ""

[catalog]
preferred_resolution = "auto"
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.user_config.CONFIG_FILE", path):
                set_config_value("subtitles", "api_key", "secret-key")
                set_config_value("catalog", "preferred_resolution", "1080p")
                text = path.read_text(encoding="utf-8")
        self.assertIn('api_key = "secret-key"', text)
        self.assertIn('preferred_resolution = "1080p"', text)
        self.assertIn("enabled = true", text)


class StreamConfigLoadTests(unittest.TestCase):
    def tearDown(self) -> None:
        reload_config()

    def test_streaming_buffer_and_mpv(self) -> None:
        toml = """
[streaming]
seed_while_watching = false
upload_limit_kib = 0

[buffer]
max_wait_sec = 10.0
mkv_start_mib = 8

[player.mpv]
cache_secs = 60
hwdec = "no"
extra_args = ["--fs"]
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml, encoding="utf-8")
            with mock.patch("annie.config.CONFIG_FILE", path):
                reload_config()
                cfg = AnnieConfig.load()
        self.assertFalse(cfg.seed_while_watching)
        self.assertEqual(cfg.streaming.upload_limit_kib, 0)
        self.assertEqual(cfg.buffer.max_wait_sec, 10.0)
        self.assertEqual(cfg.buffer.mkv_start_mib, 8)
        self.assertEqual(cfg.mpv.cache_secs, 60)
        self.assertEqual(cfg.mpv.hwdec, "no")
        self.assertEqual(cfg.mpv.extra_args, ["--fs"])


if __name__ == "__main__":
    unittest.main()

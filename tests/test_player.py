"""Profils mpv : d3d11 réservé à Windows."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from annie.player import _mpv_profile_video


class MpvProfileVideoTests(unittest.TestCase):
    def test_safe_uses_opengl_on_linux(self) -> None:
        with patch("annie.player.sys.platform", "linux"):
            gpu, hwdec, vo = _mpv_profile_video("safe", "opengl", "auto", "gpu")
        self.assertEqual(gpu, "opengl")
        self.assertEqual(hwdec, "auto-safe")
        self.assertEqual(vo, "gpu")

    def test_safe_does_not_force_d3d11_on_linux(self) -> None:
        with patch("annie.player.sys.platform", "linux"):
            gpu, _, _ = _mpv_profile_video("safe", "vulkan", "auto", "gpu")
        self.assertEqual(gpu, "vulkan")
        with patch("annie.player.sys.platform", "linux"):
            gpu, _, _ = _mpv_profile_video("safe", "d3d11", "auto", "gpu")
        self.assertEqual(gpu, "opengl")

    def test_software_disables_hwdec_without_d3d11_on_linux(self) -> None:
        with patch("annie.player.sys.platform", "linux"):
            gpu, hwdec, _ = _mpv_profile_video("software", "opengl", "auto", "gpu")
        self.assertEqual(gpu, "opengl")
        self.assertEqual(hwdec, "no")

    def test_safe_uses_d3d11_on_windows(self) -> None:
        with patch("annie.player.sys.platform", "win32"):
            gpu, hwdec, _ = _mpv_profile_video("safe", "opengl", "auto", "gpu")
        self.assertEqual(gpu, "d3d11")
        self.assertEqual(hwdec, "auto-safe")


if __name__ == "__main__":
    unittest.main()

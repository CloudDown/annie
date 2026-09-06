"""mpv playback profiles."""

from __future__ import annotations

import unittest

from annie.player import _mpv_profile_video


class MpvProfileVideoTests(unittest.TestCase):
    def test_safe_uses_opengl(self) -> None:
        gpu, hwdec, vo = _mpv_profile_video("safe", "opengl", "auto", "gpu")
        self.assertEqual(gpu, "opengl")
        self.assertEqual(hwdec, "auto-safe")
        self.assertEqual(vo, "gpu")

    def test_safe_maps_d3d11_to_opengl(self) -> None:
        gpu, _, _ = _mpv_profile_video("safe", "vulkan", "auto", "gpu")
        self.assertEqual(gpu, "vulkan")
        gpu, _, _ = _mpv_profile_video("safe", "d3d11", "auto", "gpu")
        self.assertEqual(gpu, "opengl")

    def test_software_disables_hwdec(self) -> None:
        gpu, hwdec, _ = _mpv_profile_video("software", "opengl", "auto", "gpu")
        self.assertEqual(gpu, "opengl")
        self.assertEqual(hwdec, "no")


if __name__ == "__main__":
    unittest.main()

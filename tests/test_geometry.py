"""Geometry cases measured by docs/plan-1.0/spikes/spike4_matrix.py; every one
was a verified hit on a real toolkit. Do not change the numbers."""

from __future__ import annotations

import unittest

from computer_use_sway import geometry

# (origin, frame_ext, window_size, children, element_ext, device_px, scale, expected_center)
CASES = {
    "gtk3 tiled s1": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (120, 248, 1036, 34), False, 1.0, (640, 291)),
    "gtk3-csd floating s1": ((150, 50), (0, 0, 952, 752), (900, 700), [(26, 23, 900, 47), (146, 180, 660, 400)],
                             (146, 318, 660, 34), False, 1.0, (600, 362)),
    "gtk4 tiled": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (120, 248, 1036, 34), False, 2.0, (640, 291)),
    "qt6 tiled": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (120, 244, 80, 32), False, 2.0, (162, 286)),
    "firefox s1": ((2, 26), (20, 20, 1276, 772), (1276, 772), [], (60, 345, 200, 80), False, 1.0, (142, 391)),
    "firefox s2": ((2, 26), (20, 20, 1276, 772), (1276, 772), [], (60, 344, 200, 80), False, 2.0, (142, 390)),
    "chromium s1": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (40, 240, 200, 80), True, 1.0, (142, 306)),
    "chromium s2": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (80, 480, 400, 160), True, 2.0, (142, 306)),
    "electron s1": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (40, 268, 200, 80), True, 1.0, (142, 334)),
    "electron s2": ((2, 26), (0, 0, 1276, 772), (1276, 772), [], (80, 536, 400, 160), True, 2.0, (142, 334)),
    "gtk3-csd tiled": ((2, 26), (0, 0, 1316, 812), (1276, 772), [(20, 20, 1276, 47), (140, 187, 1036, 485)],
                       (140, 251, 1036, 34), False, 1.0, (640, 274)),
}


class GeometryTests(unittest.TestCase):
    def test_measured_matrix(self) -> None:
        for name, (origin, frame, size, children, ext, dev, scale, want) in CASES.items():
            with self.subTest(name):
                csd = geometry.csd_origin(frame, children, size)
                geo = geometry.WindowGeo(origin, size, scale, frame, csd)
                self.assertEqual(geometry.center(geometry.a11y_to_layout(geo, ext, dev)), want)

    def test_csd_origin_from_children(self) -> None:
        boxes = [(20, 20, 1276, 47), (140, 187, 1036, 485)]
        self.assertEqual(geometry.csd_origin((0, 0, 1316, 812), boxes, (1276, 772)), (20, 20))

    def test_csd_origin_falls_back_to_centred_margin(self) -> None:
        invalid = [(-2147483648, -2147483648, 1, 1)]
        self.assertEqual(geometry.csd_origin((0, 0, 1316, 812), invalid, (1276, 772)), (20, 20))

    def test_no_csd_when_sizes_match(self) -> None:
        self.assertEqual(geometry.csd_origin((0, 0, 10, 10), [(5, 5, 3, 3)], (10, 10)), (0, 0))

    def test_ocr_boxes_halve_the_2x_capture(self) -> None:
        self.assertEqual(geometry.ocr_to_layout((100, 50), (40, 20, 60, 30)), (120, 60, 30, 15))

    def test_output_scale_lookup(self) -> None:
        outs = [{"rect": {"x": 0, "y": 0, "width": 10, "height": 10}, "scale": 2}]
        self.assertEqual(geometry.output_scale_at(outs, 5, 5), 2.0)
        self.assertEqual(geometry.output_scale_at(outs, 50, 5), 1.0)


if __name__ == "__main__":
    unittest.main()

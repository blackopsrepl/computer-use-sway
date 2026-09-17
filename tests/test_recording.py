from __future__ import annotations

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from computer_use_sway import server


def single_output(name: str = "DP-1", width: int = 1920, height: int = 1080) -> list[dict]:
    return [
        {
            "name": name,
            "active": True,
            "rect": {"x": 0, "y": 0, "width": width, "height": height},
        }
    ]


class StrictIntTests(unittest.TestCase):
    def test_accepts_integer(self) -> None:
        self.assertEqual(server.strict_int(7, "value"), 7)
        self.assertEqual(server.strict_int(-1, "value"), -1)

    def test_rejects_non_integers(self) -> None:
        for value in (True, False, 1.0, "7", None, [1]):
            with self.assertRaises(server.ToolError, msg=f"{value!r}"):
                server.strict_int(value, "value")


class RecordingFormatTests(unittest.TestCase):
    def test_default_format_is_webm(self) -> None:
        self.assertEqual(server.parse_recording_format(None), "webm")

    def test_known_formats_are_accepted(self) -> None:
        self.assertEqual(server.parse_recording_format("gif"), "gif")

    def test_unknown_format_is_rejected(self) -> None:
        for value in ("mkv", "mp4", 7, None if False else "WEBM"):
            with self.assertRaises(server.ToolError, msg=f"{value!r}"):
                server.parse_recording_format(value)

    def test_default_durations_per_format(self) -> None:
        self.assertEqual(server.parse_max_duration(None, "webm"), 60.0)
        self.assertEqual(server.parse_max_duration(None, "gif"), 15.0)

    def test_duration_bounds(self) -> None:
        with self.assertRaises(server.ToolError):
            server.parse_max_duration(0, "webm")
        with self.assertRaises(server.ToolError):
            server.parse_max_duration(-1, "gif")
        with self.assertRaises(server.ToolError):
            server.parse_max_duration(301, "webm")
        with self.assertRaises(server.ToolError):
            server.parse_max_duration(16, "gif")
        self.assertEqual(server.parse_max_duration(300, "webm"), 300.0)

    def test_duration_type_is_enforced(self) -> None:
        for value in (True, "60", None if False else object()):
            with self.assertRaises(server.ToolError, msg=f"{value!r}"):
                server.parse_max_duration(value, "webm")


class SelectRecordingOutputTests(unittest.TestCase):
    def test_single_output_is_inferred(self) -> None:
        with patch.object(server, "get_outputs", return_value=single_output()):
            output = server.select_recording_output(None)
        self.assertEqual(output["name"], "DP-1")

    def test_exact_name_is_required_when_known(self) -> None:
        with patch.object(server, "get_outputs", return_value=single_output()):
            output = server.select_recording_output("DP-1")
        self.assertEqual(output["name"], "DP-1")

    def test_unknown_name_is_rejected(self) -> None:
        with patch.object(server, "get_outputs", return_value=single_output()):
            with self.assertRaises(server.ToolError):
                server.select_recording_output("HDMI-A-1")
            with self.assertRaises(server.ToolError):
                server.select_recording_output(7)

    def test_ambiguous_outputs_require_a_name(self) -> None:
        outputs = single_output() + [
            {
                "name": "HDMI-A-1",
                "active": True,
                "rect": {"x": 1920, "y": 0, "width": 1280, "height": 720},
            }
        ]
        with patch.object(server, "get_outputs", return_value=outputs):
            with self.assertRaises(server.ToolError):
                server.select_recording_output(None)
            output = server.select_recording_output("HDMI-A-1")
        self.assertEqual(output["rect"]["width"], 1280)


class ValidateContainedRegionTests(unittest.TestCase):
    RECT = {"x": 1920, "y": 0, "width": 1280, "height": 720}

    def test_none_passes_through(self) -> None:
        self.assertIsNone(server.validate_contained_region(None, self.RECT))

    def test_contained_region_is_normalized(self) -> None:
        region = server.validate_contained_region(
            {"x": 1920, "y": 10, "width": 640, "height": 480}, self.RECT
        )
        self.assertEqual(region, {"x": 1920, "y": 10, "width": 640, "height": 480})

    def test_partial_overlap_is_rejected(self) -> None:
        with self.assertRaises(server.ToolError):
            server.validate_contained_region(
                {"x": 1000, "y": 0, "width": 1280, "height": 720}, self.RECT
            )

    def test_corner_touch_across_gap_is_rejected(self) -> None:
        with self.assertRaises(server.ToolError):
            server.validate_contained_region(
                {"x": 1919, "y": -1, "width": 1281, "height": 721}, self.RECT
            )

    def test_outside_region_is_rejected(self) -> None:
        with self.assertRaises(server.ToolError):
            server.validate_contained_region(
                {"x": 0, "y": 0, "width": 100, "height": 100}, self.RECT
            )

    def test_invalid_shapes_are_rejected(self) -> None:
        for region in ("0,0 10x10", [], {"x": 0, "y": 0}):
            with self.assertRaises(server.ToolError, msg=f"{region!r}"):
                server.validate_contained_region(region, self.RECT)

    def test_non_positive_sizes_are_rejected(self) -> None:
        for size in (0, -10):
            with self.assertRaises(server.ToolError):
                server.validate_contained_region(
                    {"x": 1920, "y": 0, "width": size, "height": 480}, self.RECT
                )
            with self.assertRaises(server.ToolError):
                server.validate_contained_region(
                    {"x": 1920, "y": 0, "width": 480, "height": size}, self.RECT
                )


class RecordingPathTests(unittest.TestCase):
    def test_paths_are_private_unique_and_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                directory = server.recording_directory()
                first = server.allocate_recording_paths("webm")
                second = server.allocate_recording_paths("gif")

                recording_id, intermediate, artifact, log_path, log_fd = first
                os.close(log_fd)
                _, intermediate2, artifact2, log_path2, log_fd2 = second
                os.close(log_fd2)

            self.assertEqual(directory, server.Path(tmpdir) / "computer-use-sway" / "recordings")
            self.assertEqual(
                stat.S_IMODE(os.stat(directory).st_mode),
                0o700,
            )
            for path in (intermediate, artifact, log_path, intermediate2, artifact2, log_path2):
                self.assertTrue(path.exists(), path)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path)
            self.assertNotEqual(intermediate, intermediate2)
            self.assertEqual(intermediate.suffix, ".mkv")
            self.assertEqual(artifact.suffix, ".webm")
            self.assertEqual(artifact2.suffix, ".gif")
            self.assertTrue(intermediate.name.startswith(recording_id))
            self.assertEqual(intermediate.stem, artifact.stem)
            self.assertEqual(intermediate.stem, log_path.stem)

    def test_missing_runtime_dir_is_a_tool_error(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "XDG_RUNTIME_DIR"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(server.ToolError):
                server.recording_directory()


class NewRecordingJobTests(unittest.TestCase):
    def test_job_is_built_from_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(
                        {
                            "region": {"x": 10, "y": 20, "width": 800, "height": 600},
                            "format": "gif",
                            "max_duration_seconds": 10,
                        }
                    )
                os.close(job.log_fd)

            self.assertEqual(job.fmt, "gif")
            self.assertEqual(job.output, "DP-1")
            self.assertEqual(job.region, {"x": 10, "y": 20, "width": 800, "height": 600})
            self.assertEqual(job.width, 800)
            self.assertEqual(job.height, 600)
            self.assertEqual(job.max_duration, 10.0)
            self.assertEqual(job.phase, "recording")
            self.assertIsNone(job.process)
            self.assertEqual(job.intermediate.parent, job.directory)
            self.assertEqual(job.artifact.suffix, ".gif")
            self.assertTrue(job.intermediate.exists())

    def test_full_output_job_uses_output_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job({})
                os.close(job.log_fd)
            self.assertEqual((job.width, job.height), (1920, 1080))
            self.assertIsNone(job.region)


if __name__ == "__main__":
    unittest.main()

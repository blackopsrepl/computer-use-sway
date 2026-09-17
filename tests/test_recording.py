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


class CaptureArgvTests(unittest.TestCase):
    def make_job(self, **overrides):
        arguments = {
            "region": {"x": 10, "y": 20, "width": 800, "height": 600},
            "format": "webm",
            "max_duration_seconds": 30,
        }
        arguments.update(overrides)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(arguments)
        os.close(job.log_fd)
        return job

    def test_capture_argv_is_exact_and_silent(self) -> None:
        job = self.make_job()
        argv = server.recording_capture_argv(job)
        self.assertEqual(
            argv,
            [
                "wf-recorder",
                "-o",
                "DP-1",
                "-g",
                "10,20 800x600",
                "-f",
                str(job.intermediate),
                "-c",
                "libx264rgb",
                "-r",
                "30",
                "-p",
                "preset=ultrafast",
                "-p",
                "crf=0",
                "-y",
            ],
        )
        self.assertNotIn("-a", argv)
        self.assertFalse(any("audio" in str(part).lower() for part in argv))

    def test_full_output_capture_omits_geometry(self) -> None:
        job = self.make_job(region=None)
        argv = server.recording_capture_argv(job)
        self.assertNotIn("-g", argv)


class EncoderChoiceTests(unittest.TestCase):
    def test_prefers_svt_av1(self) -> None:
        text = (
            " V....D libaom-av1           libaom AV1 (codec av1)\n"
            " V..... libsvtav1            SVT-AV1 encoder (codec av1)\n"
        )
        with patch.object(server, "run_command") as run:
            run.return_value.text = text
            self.assertEqual(server.choose_video_encoder(), "libsvtav1")

    def test_falls_back_to_aom(self) -> None:
        text = " V....D libaom-av1           libaom AV1 (codec av1)\n"
        with patch.object(server, "run_command") as run:
            run.return_value.text = text
            self.assertEqual(server.choose_video_encoder(), "libaom-av1")

    def test_requires_an_av1_encoder(self) -> None:
        with patch.object(server, "run_command") as run:
            run.return_value.text = " V....D libx264            libx264 H.264\n"
            with self.assertRaises(server.ToolError):
                server.choose_video_encoder()


class FinalizeArgvTests(unittest.TestCase):
    def make_job(self, fmt: str = "webm", **overrides):
        arguments = {
            "region": {"x": 0, "y": 0, "width": 1280, "height": 720},
            "format": fmt,
            "max_duration_seconds": 30,
        }
        arguments.update(overrides)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(arguments)
        os.close(job.log_fd)
        return job

    def test_webm_argv_uses_av1_and_excludes_audio(self) -> None:
        job = self.make_job()
        job.encoder = "libsvtav1"
        argv = server.recording_webm_argv(job)
        joined = " ".join(argv)
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("-nostdin", argv)
        self.assertIn("-an", argv)
        self.assertIn("-map_metadata", argv)
        self.assertIn("fps=30", joined)
        self.assertIn("pad=ceil(iw/2)*2:ceil(ih/2)*2", joined)
        self.assertIn("format=yuv420p", joined)
        self.assertIn("libsvtav1", argv)
        self.assertIn("-force_key_frames", argv)
        self.assertIn("webm", argv)
        self.assertEqual(argv[-1], str(job.artifact))
        self.assertNotIn("-a", argv)

    def test_webm_aom_fallback_params(self) -> None:
        job = self.make_job()
        job.encoder = "libaom-av1"
        argv = server.recording_webm_argv(job)
        self.assertIn("-cpu-used", argv)
        self.assertIn("-row-mt", argv)

    def test_gif_argv_downscales_only_wide_captures(self) -> None:
        job = self.make_job(fmt="gif", max_duration_seconds=10)
        wide = server.recording_gif_argv(job, capture_width=1600)
        self.assertIn("fps=12", " ".join(wide))
        self.assertIn("scale=960:-1:flags=lanczos", " ".join(wide))
        self.assertIn("palettegen=stats_mode=diff", " ".join(wide))
        self.assertIn("paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", " ".join(wide))
        self.assertIn("0", wide[wide.index("-loop") + 1])
        self.assertEqual(wide[-1], str(job.artifact))

        narrow = server.recording_gif_argv(job, capture_width=800)
        narrow_joined = " ".join(narrow)
        self.assertNotIn("scale=960", narrow_joined)
        self.assertNotIn("lanczos", narrow_joined)


class ProbeAndValidateTests(unittest.TestCase):
    def webm_probe(self, codec: str = "av1", audio: bool = False, duration: str = "12.5") -> dict:
        streams = [
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
            }
        ]
        if audio:
            streams.append({"codec_type": "audio", "codec_name": "opus"})
        return {"format": {"format_name": "matroska,webm", "duration": duration}, "streams": streams}

    def gif_probe(self, audio: bool = False) -> dict:
        streams = [
            {
                "codec_type": "video",
                "codec_name": "gif",
                "width": 960,
                "height": 540,
                "avg_frame_rate": "12/1",
            }
        ]
        if audio:
            streams.append({"codec_type": "audio", "codec_name": "opus"})
        return {"format": {"format_name": "gif", "duration": "4.2"}, "streams": streams}

    def make_job(self, fmt: str = "webm") -> server.RecordingJob:
        duration = 10 if fmt == "gif" else 30
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(
                        {"format": fmt, "max_duration_seconds": duration}
                    )
        os.close(job.log_fd)
        return job

    def test_probe_media_parses_json(self) -> None:
        with patch.object(server, "run_command") as run:
            run.return_value.text = '{"format": {}, "streams": []}'
            probe = server.probe_media(server.Path("/tmp/x.webm"))
        self.assertEqual(probe, {"format": {}, "streams": []})

    def test_probe_media_rejects_invalid_json(self) -> None:
        with patch.object(server, "run_command") as run:
            run.return_value.text = "not json"
            with self.assertRaises(server.ToolError):
                server.probe_media(server.Path("/tmp/x.webm"))

    def test_valid_webm_artifact_passes(self) -> None:
        job = self.make_job("webm")
        with patch.object(server, "probe_media", return_value=self.webm_probe()):
            summary = server.validate_recording_artifact(job)
        self.assertEqual(summary["codec"], "av1")
        self.assertEqual(summary["container"], "webm")
        self.assertEqual(summary["mime_type"], "video/webm")
        self.assertEqual(summary["width"], 1280)
        self.assertEqual(summary["duration_seconds"], 12.5)
        self.assertEqual(summary["frame_rate"], 30.0)

    def test_webm_artifact_rejections(self) -> None:
        job = self.make_job("webm")
        cases = [
            self.webm_probe(codec="h264"),
            self.webm_probe(audio=True),
            {"format": {"format_name": "matroska", "duration": "1"}, "streams": []},
            self.gif_probe(),
        ]
        for probe in cases:
            with patch.object(server, "probe_media", return_value=probe):
                with self.assertRaises(server.ToolError, msg=str(probe)[:60]):
                    server.validate_recording_artifact(job)

    def test_zero_duration_is_rejected(self) -> None:
        job = self.make_job("webm")
        probe = self.webm_probe(duration="0")
        with patch.object(server, "probe_media", return_value=probe):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(job)

    def test_valid_gif_artifact_passes(self) -> None:
        job = self.make_job("gif")
        with patch.object(server, "probe_media", return_value=self.gif_probe()):
            summary = server.validate_recording_artifact(job)
        self.assertEqual(summary["codec"], "gif")
        self.assertEqual(summary["container"], "gif")
        self.assertEqual(summary["mime_type"], "image/gif")

    def test_gif_with_audio_is_rejected(self) -> None:
        job = self.make_job("gif")
        with patch.object(server, "probe_media", return_value=self.gif_probe(audio=True)):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(job)


class FinalizeRecordingTests(unittest.TestCase):
    def test_finalize_converts_then_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(
                        {"format": "webm", "max_duration_seconds": 30}
                    )
            os.close(job.log_fd)
            job.encoder = "libsvtav1"
            capture_probe = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1280,
                        "height": 720,
                    }
                ]
            }
            artifact_probe = {
                "format": {"format_name": "matroska,webm", "duration": "9.9"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "av1",
                        "width": 1280,
                        "height": 720,
                        "avg_frame_rate": "30/1",
                    }
                ],
            }
            with patch.object(
                server, "probe_media", side_effect=[capture_probe, artifact_probe]
            ) as probe:
                with patch.object(server, "run_command") as run:
                    summary = server.finalize_recording(job)

            self.assertEqual(probe.call_count, 2)
            self.assertEqual(run.call_count, 1)
            ffmpeg_argv = run.call_args[0][0]
            self.assertEqual(ffmpeg_argv[0], "ffmpeg")
            self.assertEqual(ffmpeg_argv[-1], str(job.artifact))
            self.assertIn("libsvtav1", ffmpeg_argv)
            self.assertEqual(summary["duration_seconds"], 9.9)

    def test_finalize_gif_uses_capture_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(server, "get_outputs", return_value=single_output()):
                    job = server.new_recording_job(
                        {"format": "gif", "max_duration_seconds": 10}
                    )
            os.close(job.log_fd)
            capture_probe = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1920,
                        "height": 1080,
                    }
                ]
            }
            artifact_probe = {
                "format": {"format_name": "gif", "duration": "5"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "gif",
                        "width": 960,
                        "height": 540,
                        "avg_frame_rate": "12/1",
                    }
                ],
            }
            with patch.object(
                server, "probe_media", side_effect=[capture_probe, artifact_probe]
            ):
                with patch.object(server, "run_command") as run:
                    server.finalize_recording(job)
            ffmpeg_argv = run.call_args[0][0]
            self.assertIn("scale=960:-1:flags=lanczos", " ".join(ffmpeg_argv))


if __name__ == "__main__":
    unittest.main()

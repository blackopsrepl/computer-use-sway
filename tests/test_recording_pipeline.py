from __future__ import annotations

import io
import os
import signal
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import core, desktop, media, narration, recording, server, tts

from support import *
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

    def mp4_probe(
        self, codec: str = "h264", audio: str | None = None, duration: str = "12.5"
    ) -> dict:
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
            streams.append({"codec_type": "audio", "codec_name": audio})
        return {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration},
            "streams": streams,
        }

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

    def make_job(self, fmt: str = "webm") -> recording.RecordingJob:
        duration = 10 if fmt == "gif" else 30
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(desktop, "get_outputs", return_value=single_output()):
                    job = recording.new_recording_job(
                        {"format": fmt, "max_duration_seconds": duration}
                    )
        os.close(job.log_fd)
        return job

    def test_probe_media_parses_json(self) -> None:
        with patch.object(core, "run_command") as run:
            run.return_value.text = '{"format": {}, "streams": []}'
            probe = server.probe_media(server.Path("/tmp/x.webm"))
        self.assertEqual(probe, {"format": {}, "streams": []})

    def test_probe_media_rejects_invalid_json(self) -> None:
        with patch.object(core, "run_command") as run:
            run.return_value.text = "not json"
            with self.assertRaises(server.ToolError):
                server.probe_media(server.Path("/tmp/x.webm"))

    def test_valid_webm_artifact_passes(self) -> None:
        job = self.make_job("webm")
        with patch.object(media, "probe_media", return_value=self.webm_probe()):
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
            with patch.object(media, "probe_media", return_value=probe):
                with self.assertRaises(server.ToolError, msg=str(probe)[:60]):
                    server.validate_recording_artifact(job)

    def test_zero_duration_is_rejected(self) -> None:
        job = self.make_job("webm")
        probe = self.webm_probe(duration="0")
        with patch.object(media, "probe_media", return_value=probe):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(job)

    def test_valid_gif_artifact_passes(self) -> None:
        job = self.make_job("gif")
        with patch.object(media, "probe_media", return_value=self.gif_probe()):
            summary = server.validate_recording_artifact(job)
        self.assertEqual(summary["codec"], "gif")
        self.assertEqual(summary["container"], "gif")
        self.assertEqual(summary["mime_type"], "image/gif")

    def test_gif_with_audio_is_rejected(self) -> None:
        job = self.make_job("gif")
        with patch.object(media, "probe_media", return_value=self.gif_probe(audio=True)):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(job)

    def test_valid_mp4_artifact_passes(self) -> None:
        job = self.make_job("mp4")
        with patch.object(media, "probe_media", return_value=self.mp4_probe()):
            summary = server.validate_recording_artifact(job)
        self.assertEqual(summary["codec"], "h264")
        self.assertEqual(summary["container"], "mp4")
        self.assertEqual(summary["mime_type"], "video/mp4")

    def test_mp4_artifact_rejections(self) -> None:
        job = self.make_job("mp4")
        cases = [
            self.mp4_probe(codec="av1"),
            self.mp4_probe(audio="aac"),
            self.webm_probe(),
        ]
        for probe in cases:
            with patch.object(media, "probe_media", return_value=probe):
                with self.assertRaises(server.ToolError, msg=str(probe)[:60]):
                    server.validate_recording_artifact(job)

    def test_mp4_expect_audio_accepts_aac(self) -> None:
        job = self.make_job("mp4")
        with patch.object(media, "probe_media", return_value=self.mp4_probe(audio="aac")):
            summary = server.validate_recording_artifact(job, expect_audio=True)
        self.assertEqual(summary["codec"], "h264")
        with patch.object(media, "probe_media", return_value=self.mp4_probe(audio="opus")):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(job, expect_audio=True)


class FinalizeRecordingTests(unittest.TestCase):
    def test_finalize_converts_then_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
                with patch.object(desktop, "get_outputs", return_value=single_output()):
                    job = recording.new_recording_job(
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
            with patch.object(media, "probe_media", side_effect=[capture_probe, artifact_probe]
            ) as probe:
                with patch.object(core, "run_command") as run:
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
                with patch.object(desktop, "get_outputs", return_value=single_output()):
                    job = recording.new_recording_job(
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
            with patch.object(media, "probe_media", side_effect=[capture_probe, artifact_probe]
            ):
                with patch.object(core, "run_command") as run:
                    server.finalize_recording(job)
            ffmpeg_argv = run.call_args[0][0]
            self.assertIn("scale=960:-1:flags=lanczos", " ".join(ffmpeg_argv))


if __name__ == "__main__":
    unittest.main()

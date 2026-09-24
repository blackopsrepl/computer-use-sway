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


class ManagerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        outputs = patch.object(desktop, "get_outputs", return_value=single_output())
        outputs.start()
        self.addCleanup(outputs.stop)
        encoder = patch.object(recording, "choose_video_encoder", return_value="libsvtav1")
        encoder.start()
        self.addCleanup(encoder.stop)
        real_sleep = time.sleep
        sleep = patch.object(server.time, "sleep", lambda _s: real_sleep(0.005))
        sleep.start()
        self.addCleanup(sleep.stop)
        self.manager = server.RecordingManager()

    def popen(self, process: FakeProcess):
        def factory(argv, **kwargs):
            process.argv = argv
            process.popen_kwargs = kwargs
            return process

        return patch.object(server.subprocess, "Popen", side_effect=factory)

    def start_ok(self, arguments: dict | None = None, process: FakeProcess | None = None) -> FakeProcess:
        process = process or FakeProcess()
        with self.popen(process):
            summary = self.manager.start(arguments or {"max_duration_seconds": 30})
        self.assertEqual(summary["phase"], "recording")
        return process

    def recordings_dir(self):
        return server.Path(os.environ["XDG_RUNTIME_DIR"]) / "computer-use-sway" / "recordings"

    def wait_terminal(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            summary = self.manager.status()
            if summary["phase"] in {"completed", "failed"}:
                return summary
            thread = self.manager._job.thread if self.manager._job else None
            if thread is not None:
                thread.join(timeout=0.05)
        return self.manager.status()

    def test_start_launches_silent_capture_in_own_session(self) -> None:
        process = self.start_ok()
        self.assertEqual(process.argv[0], "wf-recorder")
        self.assertNotIn("-a", process.argv)
        self.assertEqual(process.popen_kwargs["stdin"], server.subprocess.DEVNULL)
        self.assertEqual(process.popen_kwargs["stdout"], server.subprocess.DEVNULL)
        self.assertTrue(process.popen_kwargs["start_new_session"])
        self.assertIn("stderr", process.popen_kwargs)

    def test_start_reports_recording_metadata(self) -> None:
        with self.popen(FakeProcess()):
            summary = self.manager.start(
                {
                    "region": {"x": 0, "y": 0, "width": 640, "height": 480},
                    "max_duration_seconds": 30,
                }
            )
        self.assertEqual(summary["output"], "DP-1")
        self.assertEqual(summary["region"], {"x": 0, "y": 0, "width": 640, "height": 480})
        self.assertEqual(summary["width"], 640)
        self.assertEqual(summary["height"], 480)
        self.assertFalse(summary["audio_included"])
        self.assertTrue(summary["cursor_included"])
        self.assertEqual(summary["max_duration_seconds"], 30.0)

    def test_start_requires_recording_binaries(self) -> None:
        with patch.object(core, "command_available", lambda name: name != "wf-recorder"
        ):
            with self.assertRaises(server.ToolError) as ctx:
                self.manager.start({})
        self.assertIn("wf-recorder", str(ctx.exception))

    def test_failed_startup_cleans_all_files(self) -> None:
        dead = FakeProcess(returncode=1, already_exited=True)
        with self.popen(dead):
            with self.assertRaises(server.ToolError) as ctx:
                self.manager.start({})
        self.assertIn("exited during startup", str(ctx.exception))
        self.assertEqual(list(self.recordings_dir().iterdir()), [])

    def test_second_start_is_rejected_while_active(self) -> None:
        self.start_ok()
        with self.popen(FakeProcess()):
            with self.assertRaises(server.ToolError) as ctx:
                self.manager.start({})
        self.assertIn("already active", str(ctx.exception))

    def test_stop_signals_and_completes(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")

        def fake_finalize(j: recording.RecordingJob) -> dict:
            j.artifact.write_bytes(b"artifact-bytes")
            return {
                "codec": "av1",
                "container": "webm",
                "mime_type": "video/webm",
                "width": 640,
                "height": 480,
                "duration_seconds": 2.0,
                "frame_rate": 30.0,
            }

        with exit_process_on_signal(process):
            with patch.object(recording, "finalize_recording", side_effect=fake_finalize):
                summary = self.manager.stop()
        self.assertEqual(summary["phase"], "processing")
        self.assertIsNotNone(job.thread)
        job.thread.join(timeout=5)
        final = self.manager.status()
        self.assertEqual(final["phase"], "completed")
        self.assertEqual(final["path"], str(job.artifact))
        self.assertEqual(final["bytes"], len(b"artifact-bytes"))
        self.assertTrue(final["graceful"])
        self.assertEqual(process.signals[0], server.signal.SIGINT)
        self.assertFalse(job.intermediate.exists())
        self.assertFalse(job.log_path.exists())
        self.assertTrue(job.artifact.exists())

    def test_stop_escalation_reports_not_graceful(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        stops = [
            patch.object(recording, "RECORDING_STOP_TIMEOUT_SECONDS", 0.02),
            patch.object(recording, "RECORDING_TERM_TIMEOUT_SECONDS", 0.02),
            patch.object(recording, "RECORDING_KILL_TIMEOUT_SECONDS", 0.02),
            patch.object(recording, "finalize_recording", side_effect=lambda j: {"codec": "av1"}),
        ]
        for stopper in stops:
            stopper.start()
            self.addCleanup(stopper.stop)
        sent: list[int] = []

        def record(_process, sig: int) -> None:
            sent.append(sig)

        with patch.object(
            server.RecordingManager, "_signal_group", autospec=True, side_effect=record
        ):
            summary = self.manager.stop()
        self.assertEqual(summary["phase"], "processing")
        job.thread.join(timeout=5)
        final = self.manager.status()
        self.assertEqual(final["phase"], "completed")
        self.assertFalse(final["graceful"])
        self.assertEqual(sent, [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])

    def test_status_reaps_independently_exited_recorder(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        process.receive_signal(0)
        with patch.object(recording, "finalize_recording", side_effect=lambda j: {"codec": "av1"}):
            summary = self.manager.status()
        self.assertEqual(summary["phase"], "processing")
        job.thread.join(timeout=5)
        self.assertEqual(self.manager.status()["phase"], "completed")

    def test_unexpected_recorder_exit_fails_with_log_tail(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.log_path.write_bytes(b"encoder exploded")
        process.returncode = 3
        summary = self.manager.status()
        self.assertEqual(summary["phase"], "failed")
        self.assertIn("encoder exploded", summary["detail"])
        self.assertIn("code 3", summary["detail"])
        self.assertTrue(job.intermediate.exists())

    def test_failed_finalization_keeps_intermediate(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        with exit_process_on_signal(process):
            with patch.object(recording, "finalize_recording", side_effect=server.ToolError("artifact is not WebM")
            ):
                self.manager.stop()
        job.thread.join(timeout=5)
        summary = self.manager.status()
        self.assertEqual(summary["phase"], "failed")
        self.assertIn("artifact is not WebM", summary["detail"])
        self.assertTrue(job.intermediate.exists())
        self.assertFalse(job.artifact.exists())

    def test_watchdog_stops_at_duration_deadline(self) -> None:
        process = self.start_ok({"max_duration_seconds": 0.05})
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        with exit_process_on_signal(process):
            with patch.object(recording, "finalize_recording", side_effect=lambda j: {"codec": "av1"}):
                final = self.wait_terminal()
        self.assertEqual(final["phase"], "completed")
        self.assertTrue(final["auto_stopped"])

    def test_watchdog_stops_at_size_limit(self) -> None:
        limit = patch.object(recording, "RECORDING_MAX_INTERMEDIATE_BYTES", 16)
        limit.start()
        self.addCleanup(limit.stop)
        process = self.start_ok({"max_duration_seconds": 300})
        job = self.manager._job
        job.intermediate.write_bytes(b"x" * 32)
        with exit_process_on_signal(process):
            with patch.object(recording, "finalize_recording", side_effect=lambda j: {"codec": "av1"}):
                final = self.wait_terminal()
        self.assertEqual(final["phase"], "completed")
        self.assertTrue(final["auto_stopped"])

    def test_shutdown_stops_active_capture(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        with exit_process_on_signal(process):
            with patch.object(recording, "finalize_recording", side_effect=lambda j: {"codec": "av1"}):
                self.manager.shutdown()
        self.assertIn(server.signal.SIGINT, process.signals)
        self.assertEqual(job.phase, "processing")

    def test_status_idle_without_job(self) -> None:
        self.assertEqual(self.manager.status(), {"phase": "idle"})

    def test_stop_without_recording_is_an_error(self) -> None:
        with self.assertRaises(server.ToolError):
            self.manager.stop()

    def test_run_mcp_server_shuts_down_recording_on_eof(self) -> None:
        process = self.start_ok()
        job = self.manager._job
        job.intermediate.write_bytes(b"capture-bytes")
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def restore_handlers() -> None:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

        self.addCleanup(restore_handlers)
        with patch.object(server.sys, "stdin", io.StringIO("")):
            with patch.object(server, "RECORDINGS", self.manager):
                with exit_process_on_signal(process):
                    server.run_mcp_server()
        self.assertEqual(job.phase, "processing")
        self.assertEqual(process.signals[0], signal.SIGINT)


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

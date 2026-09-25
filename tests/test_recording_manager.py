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
        h264 = patch.object(recording, "choose_h264_encoder", return_value="libx264")
        h264.start()
        self.addCleanup(h264.stop)
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


class RecordingIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = server.RecordingManager()

    def register(self, job: recording.RecordingJob) -> recording.RecordingJob:
        job.result = {
            "id": job.id,
            "phase": "completed",
            "path": str(job.artifact),
            "audio_included": False,
        }
        self.manager._jobs[job.id] = job
        return job

    def test_status_and_timeline_address_a_non_latest_job(self) -> None:
        first = self.register(completed_job(self.tmp.name, events=[event(1, 500.0)]))
        second = self.register(completed_job(self.tmp.name))
        self.manager._job = second
        self.assertEqual(self.manager.status({"id": first.id})["id"], first.id)
        self.assertEqual(self.manager.status()["id"], second.id)
        self.assertEqual(self.manager.timeline({"id": first.id})["id"], first.id)
        self.assertEqual(self.manager.timeline()["id"], second.id)

    def test_unknown_id_is_a_clear_error(self) -> None:
        with self.assertRaises(server.ToolError) as ctx:
            self.manager.status({"id": "deadbeef"})
        self.assertIn("unknown recording id", str(ctx.exception))
        for call in (
            lambda: self.manager.timeline({"id": "deadbeef"}),
            lambda: self.manager.voiceover(
                {"id": "deadbeef", "segments": [{"anchor": {"at_ms": 0}, "text": "hi"}]}
            ),
            lambda: self.manager.scenes({"id": "deadbeef"}),
        ):
            with self.assertRaises(server.ToolError):
                call()

    def test_voiceover_targets_the_requested_job_not_the_latest(self) -> None:
        first = self.register(completed_job(self.tmp.name, events=[event(1, 500.0)]))
        second = self.register(completed_job(self.tmp.name))
        self.manager._job = second
        fresh = {
            "codec": "av1",
            "container": "webm",
            "mime_type": "video/webm",
            "width": 640,
            "height": 360,
            "duration_seconds": 9.75,
            "frame_rate": 30.0,
        }
        with patch.object(tts, "resolve_tts_engine", return_value=server.EdgeTtsEngine()):
            with patch.object(
                narration, "perform_narration", return_value={"engine": "edge", "segment_count": 1}
            ):
                with patch.object(narration, "recording_duration_ms", return_value=9000.0):
                    with patch.object(
                        recording, "validate_recording_artifact", return_value=fresh
                    ):
                        summary = self.manager.voiceover(
                            {
                                "id": first.id,
                                "segments": [{"anchor": {"event_id": 1}, "text": "hi"}],
                            }
                        )
        self.assertEqual(summary["id"], first.id)
        first.thread.join(timeout=5)
        self.assertEqual(first.phase, "completed")
        self.assertEqual(second.phase, "completed")
        self.assertTrue(first.result["audio_included"])


if __name__ == "__main__":
    unittest.main()

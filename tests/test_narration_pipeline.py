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
class NarrationArgvTests(unittest.TestCase):
    def build(self):
        anchored = [
            (server.NarrationSegment(1, 1, None, "a"), 1000.0),
            (server.NarrationSegment(2, 2, None, "b"), 6000.0),
        ]
        clips = {1: clip("/tmp/1.mp3", 1200.0, lead_ms=100.0), 2: clip("/tmp/2.mp3", 800.0, words=3)}
        request = server.NarrationRequest(
            segments=[item[0] for item in anchored],
            engine="edge",
            voice=None,
            offset_ms=0.0,
            fit="compress",
            tail_ms=300.0,
        )
        schedule = server.schedule_narration(anchored, clips, 10000.0, request)
        return anchored, clips, schedule

    def test_track_argv_builds_mix_and_delays(self) -> None:
        anchored, clips, schedule = self.build()
        argv = server.narration_track_argv(
            anchored, clips, schedule, server.Path("/tmp/narration.wav")
        )
        joined = " ".join(argv)
        self.assertEqual(argv[0], "ffmpeg")
        self.assertIn("/tmp/1.mp3", argv)
        self.assertIn("/tmp/2.mp3", argv)
        self.assertIn("anullsrc=channel_layout=stereo", joined)
        self.assertIn("adelay=1000|1000", joined)
        self.assertIn("amix=inputs=3:normalize=0", joined)
        self.assertIn("aformat=channel_layouts=stereo", joined)
        self.assertIn("pcm_s16le", argv)
        self.assertEqual(argv[-1], "/tmp/narration.wav")

    def test_track_argv_uses_atempo_when_compressed(self) -> None:
        anchored = [
            (server.NarrationSegment(1, 1, None, "a"), 1000.0),
            (server.NarrationSegment(2, 2, None, "b"), 1700.0),
        ]
        clips = {1: clip("/tmp/1.mp3", 3000.0), 2: clip("/tmp/2.mp3", 200.0)}
        request = server.NarrationRequest(
            segments=[item[0] for item in anchored],
            engine="edge",
            voice=None,
            offset_ms=0.0,
            fit="compress",
            tail_ms=300.0,
        )
        schedule = server.schedule_narration(anchored, clips, 10000.0, request)
        self.assertTrue(schedule.segments[0].compressed)
        argv = server.narration_track_argv(
            anchored, clips, schedule, server.Path("/tmp/narration.wav")
        )
        self.assertIn("atempo", " ".join(argv))

    def test_mux_argv_copies_video_and_adds_opus(self) -> None:
        from types import SimpleNamespace

        job = SimpleNamespace(artifact=server.Path("/tmp/a.webm"), fmt="webm", encoder="libsvtav1")
        argv = recording.narration_mux_argv(
            job, server.Path("/tmp/n.wav"), server.Path("/tmp/out.webm")
        )
        self.assertEqual(argv[:4], ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"])
        self.assertIn("0:v:0", argv)
        self.assertIn("1:a:0", argv)
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertEqual(argv[argv.index("-c:a") + 1], "libopus")
        self.assertIn("-map_metadata", argv)
        self.assertEqual(argv[-2], "webm")
        self.assertEqual(argv[-1], "/tmp/out.webm")

    def test_mux_argv_mp4_uses_h264_and_aac(self) -> None:
        from types import SimpleNamespace

        job = SimpleNamespace(artifact=server.Path("/tmp/a.mp4"), fmt="mp4", encoder="libx264")
        argv = recording.narration_mux_argv(
            job, server.Path("/tmp/n.wav"), server.Path("/tmp/out.mp4")
        )
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertEqual(argv[argv.index("-c:a") + 1], "aac")
        self.assertIn("+faststart", argv)
        self.assertEqual(argv[-1], "/tmp/out.mp4")


class TtsHelperTests(unittest.TestCase):
    def test_parse_vtt_word_boundaries(self) -> None:
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.050 --> 00:00:00.350\nHello\n\n"
            "00:00:00.350 --> 00:00:00.700\nworld\n\n"
        )
        words = server.parse_vtt_word_boundaries(vtt)
        self.assertEqual([word["text"] for word in words], ["Hello", "world"])
        self.assertEqual(words[0]["start_ms"], 50)
        self.assertEqual(words[1]["end_ms"], 700)

    def test_parse_vtt_ignores_empty_text(self) -> None:
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:00.100\n\n"
        self.assertEqual(server.parse_vtt_word_boundaries(vtt), [])

    def test_parse_vtt_accepts_comma_milliseconds(self) -> None:
        vtt = "1\n00:00:00,050 --> 00:00:02,125\nHello there.\n"
        words = server.parse_vtt_word_boundaries(vtt)
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0]["start_ms"], 50)
        self.assertEqual(words[0]["end_ms"], 2125)

    def test_parse_lead_silence(self) -> None:
        leading = "[silencedetect @ 0x1] silence_start: 0\n[silencedetect @ 0x1] silence_end: 0.42 | silence_duration: 0.42\n"
        self.assertEqual(server.parse_lead_silence_ms(leading), 420.0)
        self.assertEqual(server.parse_lead_silence_ms("no silence here"), 0.0)
        later = "[silencedetect] silence_start: 1.0\n[silencedetect] silence_end: 1.5\n"
        self.assertEqual(server.parse_lead_silence_ms(later), 0.0)

    def test_atempo_filters_chain_within_bounds(self) -> None:
        self.assertEqual(server.atempo_filters(1.0), ["atempo=1.000000"])
        fast = server.atempo_filters(4.0)
        self.assertEqual(fast, ["atempo=2", "atempo=2.000000"])
        slow = server.atempo_filters(0.25)
        self.assertEqual(slow, ["atempo=0.5", "atempo=0.500000"])

    def test_probe_audio_duration_uses_format_duration(self) -> None:
        with patch.object(media, "probe_media", return_value={"format": {"duration": "1.25"}, "streams": []}
        ):
            self.assertEqual(server.probe_audio_duration_ms(server.Path("/tmp/a.mp3")), 1250.0)


class EngineSynthesizeTests(unittest.TestCase):
    VTT = "WEBVTT\n\n00:00:00.080 --> 00:00:00.400\nHi\n\n"

    def test_edge_synthesize_parses_words_and_lead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = server.Path(tmp) / "segment.mp3"

            def fake_run(argv, **kwargs):
                if argv[0] == server.NARRATION_EDGE_COMMAND:
                    out.write_bytes(b"audio")
                    out.with_suffix(".vtt").write_text(self.VTT, encoding="utf-8")
                return server.CommandResult(stdout=b"", stderr=b"", returncode=0)

            with patch.object(core, "command_available", return_value=True):
                with patch.object(core, "run_command", side_effect=fake_run) as run:
                    with patch.object(tts, "probe_audio_duration_ms", return_value=1200.0):
                        result = server.EdgeTtsEngine().synthesize("Hi", "en-US-AriaNeural", out)
            self.assertEqual(result.duration_ms, 1200.0)
            self.assertEqual(result.lead_silence_ms, 80.0)
            self.assertEqual(result.words[0]["text"], "Hi")
            argv = run.call_args[0][0]
            self.assertIn("--voice", argv)
            self.assertEqual(argv[argv.index("--voice") + 1], "en-US-AriaNeural")

    def test_piper_synthesize_requires_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = server.Path(tmp) / "segment.wav"
            with patch.object(core, "command_available", return_value=True):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(server.NARRATION_PIPER_MODEL_ENV, None)
                    with self.assertRaises(server.ToolError):
                        server.PiperTtsEngine().synthesize("Hi", None, out)

    def test_piper_synthesize_passes_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = server.Path(tmp) / "voice.onnx"
            model.write_bytes(b"model")
            out = server.Path(tmp) / "segment.wav"
            with patch.object(core, "command_available", return_value=True):
                with patch.object(core, "run_command") as run:
                    with patch.object(tts, "probe_audio_duration_ms", return_value=900.0):
                        with patch.object(tts, "detect_lead_silence_ms", return_value=0.0):
                            result = server.PiperTtsEngine().synthesize("Hi", str(model), out)
            self.assertEqual(result.lead_silence_ms, 0.0)
            argv = run.call_args[0][0]
            self.assertEqual(argv[argv.index("--model") + 1], str(model))


class EngineResolutionTests(unittest.TestCase):
    def test_prefers_edge_when_both_available(self) -> None:
        with patch.object(core, "command_available", return_value=True):
            self.assertEqual(server.resolve_tts_engine("auto").name, "edge")

    def test_falls_back_to_piper_when_edge_missing(self) -> None:
        with patch.object(core, "command_available", lambda name: name == server.NARRATION_PIPER_COMMAND
        ):
            self.assertEqual(server.resolve_tts_engine("auto").name, "piper")

    def test_no_engine_is_a_clear_error(self) -> None:
        with patch.object(core, "command_available", return_value=False):
            with self.assertRaises(server.ToolError) as ctx:
                server.resolve_tts_engine("auto")
        self.assertIn("no TTS engine", str(ctx.exception))

    def test_explicit_engine_must_be_installed(self) -> None:
        with patch.object(core, "command_available", return_value=False):
            with self.assertRaises(server.ToolError):
                server.resolve_tts_engine("edge")
            with self.assertRaises(server.ToolError):
                server.resolve_tts_engine("piper")


class SceneParsingTests(unittest.TestCase):
    def test_parse_scene_cuts(self) -> None:
        stderr = (
            "[Parsed_showinfo_1 @ 0x1] n:0 pts:123 pts_time:1.230 fmt:yuv420p\n"
            "[Parsed_showinfo_1 @ 0x1] n:1 pts:456 pts_time:4.560 fmt:yuv420p\n"
        )
        cuts = server.parse_scene_cuts(stderr, limit=10)
        self.assertEqual([cut["t_ms"] for cut in cuts], [1230.0, 4560.0])

    def test_parse_scene_cuts_respects_limit(self) -> None:
        stderr = "pts_time:1.0\npts_time:2.0\npts_time:3.0"
        self.assertEqual(len(server.parse_scene_cuts(stderr, limit=2)), 2)


class ValidateNarratedArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = completed_job(self.tmp.name)

    def webm_probe(self, audio_codec: str | None = None, audio_count: int = 0) -> dict:
        streams = [
            {
                "codec_type": "video",
                "codec_name": "av1",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
            }
        ]
        if audio_codec:
            for _ in range(audio_count):
                streams.append({"codec_type": "audio", "codec_name": audio_codec})
        return {"format": {"format_name": "matroska,webm", "duration": "8.0"}, "streams": streams}

    def test_expect_audio_accepts_single_opus(self) -> None:
        with patch.object(media, "probe_media", return_value=self.webm_probe("opus", 1)
        ):
            summary = server.validate_recording_artifact(self.job, expect_audio=True)
        self.assertEqual(summary["codec"], "av1")

    def test_expect_audio_rejects_silent_and_wrong_codec(self) -> None:
        for probe in (self.webm_probe(), self.webm_probe("vorbis", 1), self.webm_probe("opus", 2)):
            with patch.object(media, "probe_media", return_value=probe):
                with self.assertRaises(server.ToolError, msg=str(probe)[:40]):
                    server.validate_recording_artifact(self.job, expect_audio=True)

    def test_silent_default_still_rejects_audio(self) -> None:
        with patch.object(media, "probe_media", return_value=self.webm_probe("opus", 1)):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(self.job)


class NarrationArtifactSourceTests(unittest.TestCase):
    """The published artifact is the single source of truth for scheduling."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def trimmed_job(self) -> recording.RecordingJob:
        return completed_job(
            self.tmp.name, capture_seconds=102.941, events=[event(1, 500.0)]
        )

    def probe_artifact(self, duration_seconds: str):
        return patch.object(
            media,
            "probe_media",
            return_value={"format": {"duration": duration_seconds}, "streams": []},
        )

    def test_anchor_validation_uses_artifact_duration_not_capture_window(self) -> None:
        job = self.trimmed_job()
        with self.probe_artifact("57.0"):
            self.assertEqual(server.recording_duration_ms(job), 57000.0)
            request = server.parse_narration_arguments(
                {"segments": [{"anchor": {"at_ms": 50000}, "text": "hi"}]}, job
            )
            self.assertEqual(request.duration_ms, 57000.0)
            with self.assertRaises(server.ToolError) as ctx:
                server.parse_narration_arguments(
                    {"segments": [{"anchor": {"at_ms": 60000}, "text": "hi"}]}, job
                )
        self.assertIn("beyond the recording duration", str(ctx.exception))

    def test_pipeline_schedules_and_muxes_from_the_artifact(self) -> None:
        job = self.trimmed_job()
        with self.probe_artifact("57.0"):
            request = server.parse_narration_arguments(
                {"segments": [{"anchor": {"at_ms": 50000}, "text": "hi"}], "subtitles": False},
                job,
            )

        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            server.Path(argv[-1]).write_bytes(b"x")
            return core.CommandResult(stdout=b"", stderr=b"", returncode=0)

        class FakeEngine:
            name = "edge"

            def synthesize(self, text, voice, out_path):
                out_path.write_bytes(b"audio")
                return clip(str(out_path), 1200.0)

        fresh = {
            "codec": "av1",
            "container": "webm",
            "mime_type": "video/webm",
            "width": 1920,
            "height": 1080,
            "duration_seconds": 57.0,
            "frame_rate": 30.0,
        }
        with patch.object(core, "run_command", side_effect=fake_run):
            with patch.object(recording, "validate_recording_artifact", return_value=fresh):
                meta = narration.perform_narration(job, request, FakeEngine())

        track_argv = next(argv for argv in calls if argv[-1].endswith("narration.wav"))
        mux_argv = next(argv for argv in calls if "-c:v" in argv)
        self.assertEqual(track_argv[track_argv.index("-t") + 1], "57.000")
        self.assertIn(str(job.artifact), mux_argv)
        self.assertNotIn(str(job.intermediate), mux_argv)
        self.assertEqual(meta["total_duration_ms"], 57000.0)


class VoiceoverLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = server.RecordingManager()

    def attach(self, job: recording.RecordingJob) -> None:
        job.result = {
            "id": job.id,
            "phase": "completed",
            "path": str(job.artifact),
            "audio_included": False,
        }
        self.manager._job = job

    def test_gif_narration_is_refused(self) -> None:
        job = completed_job(self.tmp.name, fmt="gif")
        self.attach(job)
        with self.assertRaises(server.ToolError) as ctx:
            self.manager.voiceover(
                {"segments": [{"anchor": {"at_ms": 0}, "text": "hi"}]}
            )
        self.assertIn("GIF cannot carry", str(ctx.exception))

    def test_narration_requires_completed_recording(self) -> None:
        job = completed_job(self.tmp.name)
        self.attach(job)
        job.phase = "recording"
        with self.assertRaises(server.ToolError):
            self.manager.voiceover({"segments": [{"anchor": {"at_ms": 0}, "text": "hi"}]})

    def test_voiceover_runs_async_and_updates_result(self) -> None:
        job = completed_job(self.tmp.name, events=[event(1, 500.0)])
        self.attach(job)

        def fake_perform(j, request, engine):
            j.artifact.write_bytes(b"narrated")
            return {"engine": engine.name, "segment_count": len(request.segments)}

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
            with patch.object(narration, "perform_narration", side_effect=fake_perform):
                with patch.object(recording, "validate_recording_artifact", return_value=fresh):
                    with patch.object(narration, "recording_duration_ms", return_value=9000.0):
                        summary = self.manager.voiceover(
                            {"segments": [{"anchor": {"event_id": 1}, "text": "hi"}]}
                        )
                        self.assertEqual(summary["phase"], "narrating")
                        job.thread.join(timeout=5)
        final = self.manager.status()
        self.assertEqual(final["phase"], "completed")
        self.assertTrue(final["audio_included"])
        self.assertEqual(final["narration"]["engine"], "edge")
        self.assertEqual(final["bytes"], len(b"narrated"))
        self.assertEqual(final["duration_seconds"], 9.75)

    def test_narration_failure_reverts_to_completed_with_error(self) -> None:
        job = completed_job(self.tmp.name, events=[event(1, 500.0)])
        self.attach(job)
        with patch.object(tts, "resolve_tts_engine", return_value=server.EdgeTtsEngine()):
            with patch.object(narration, "perform_narration", side_effect=server.ToolError("tts exploded")
            ):
                with patch.object(narration, "recording_duration_ms", return_value=9000.0):
                    self.manager.voiceover(
                        {"segments": [{"anchor": {"event_id": 1}, "text": "hi"}]}
                    )
                    job.thread.join(timeout=5)
        final = self.manager.status()
        self.assertEqual(final["phase"], "completed")
        self.assertIn("tts exploded", final["narration"]["error"])


class TimelineEventCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = server.RecordingManager()

    def test_events_are_recorded_only_while_recording(self) -> None:
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name}):
            with patch.object(desktop, "get_outputs", return_value=single_output()):
                job = recording.new_recording_job({"max_duration_seconds": 30})
        os.close(job.log_fd)
        job.started_monotonic = 100.0
        job.phase = "recording"
        self.manager._job = job
        self.manager.record_event("click", {"x": 1, "y": 2}, 101.5, True)
        self.assertEqual(len(job.events), 1)
        self.assertEqual(job.events[0]["t_ms"], 1500.0)
        self.assertEqual(job.events[0]["id"], 1)

        job.phase = "completed"
        self.manager.record_event("click", {"x": 3, "y": 4}, 102.0, True)
        self.assertEqual(len(job.events), 1)

    def test_recording_tools_are_never_logged(self) -> None:
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name}):
            with patch.object(desktop, "get_outputs", return_value=single_output()):
                job = recording.new_recording_job({"max_duration_seconds": 30})
        os.close(job.log_fd)
        job.phase = "recording"
        self.manager._job = job
        self.manager.record_event("recording_status", {}, 101.0, True)
        self.assertEqual(job.events, [])


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
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


def event(event_id: int, t_ms: float, tool: str = "click") -> dict:
    return {"id": event_id, "t_ms": t_ms, "tool": tool, "ok": True, "payload": {}}


def completed_job(
    tmpdir: str,
    fmt: str = "webm",
    capture_seconds: float = 10.0,
    events: list[dict] | None = None,
) -> server.RecordingJob:
    duration = 15 if fmt == "gif" else 30
    with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmpdir}):
        with patch.object(server, "get_outputs", return_value=single_output()):
            job = server.new_recording_job(
                {"format": fmt, "max_duration_seconds": duration}
            )
    os.close(job.log_fd)
    job.started_monotonic = 100.0
    job.ended_monotonic = 100.0 + capture_seconds
    job.events = list(events or [])
    job.artifact.write_bytes(b"artifact")
    job.phase = "completed"
    return job


def clip(index_path: str, duration_ms: float, lead_ms: float = 0.0, words: int = 0) -> server.TtsClip:
    return server.TtsClip(
        path=server.Path(index_path),
        duration_ms=duration_ms,
        lead_silence_ms=lead_ms,
        words=[{"text": "w", "start_ms": 0, "end_ms": 1}] * words,
    )


class RecordingRelativeMsTests(unittest.TestCase):
    def test_converts_monotonic_delta_to_milliseconds(self) -> None:
        self.assertEqual(server.recording_relative_ms(100.0, 101.5), 1500.0)
        self.assertEqual(server.recording_relative_ms(100.0, 100.0005), 0.5)

    def test_clamps_backwards_events_to_zero(self) -> None:
        self.assertEqual(server.recording_relative_ms(100.0, 99.0), 0.0)


class TimelinePayloadTests(unittest.TestCase):
    def test_click_payload_is_curated(self) -> None:
        payload = server.timeline_payload("click", {"x": 5, "y": 6, "button": "left", "count": 2})
        self.assertEqual(payload, {"x": 5, "y": 6, "button": "left", "count": 2})

    def test_type_text_stores_counts_not_content(self) -> None:
        payload = server.timeline_payload("type_text", {"text": "secret", "delay_ms": 10})
        self.assertEqual(payload, {"delay_ms": 10, "characters": 6})
        self.assertNotIn("secret", str(payload))

    def test_clipboard_stores_byte_count(self) -> None:
        payload = server.timeline_payload("clipboard_set", {"text": "héllo"})
        self.assertEqual(payload, {"bytes": len("héllo".encode("utf-8"))})

    def test_screenshot_keeps_geometry_not_image(self) -> None:
        region = {"x": 0, "y": 0, "width": 10, "height": 10}
        payload = server.timeline_payload("screenshot", {"region": region, "output": "both"})
        self.assertEqual(payload, {"output": "both", "region": region})


class TimelineDocumentTests(unittest.TestCase):
    def test_document_reports_capture_window_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job = completed_job(tmpdir, capture_seconds=7.5, events=[event(1, 1200.0)])
            document = server.timeline_document(job)
        self.assertEqual(document["event_count"], 1)
        self.assertEqual(document["events"][0]["t_ms"], 1200.0)
        self.assertEqual(document["capture_seconds"], 7.5)

    def test_sidecar_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            job = completed_job(tmpdir, events=[event(1, 10.0)])
            path = server.write_timeline_sidecar(job)
            self.assertEqual(path, job.timeline_path)
            data = server.json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["events"][0]["id"], 1)


class NarrationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = completed_job(
            self.tmp.name, capture_seconds=10.0, events=[event(1, 1000.0), event(2, 5000.0)]
        )

    def parse(self, arguments: dict) -> server.NarrationRequest:
        return server.parse_narration_arguments(arguments, self.job)

    def test_accepts_event_and_absolute_anchors(self) -> None:
        request = self.parse(
            {
                "segments": [
                    {"anchor": {"event_id": 1}, "text": "one"},
                    {"anchor": {"at_ms": 2500}, "text": "two"},
                ]
            }
        )
        self.assertEqual(len(request.segments), 2)
        self.assertEqual(request.segments[0].anchor_event_id, 1)
        self.assertEqual(request.segments[1].anchor_at_ms, 2500.0)
        self.assertEqual(request.engine, "auto")
        self.assertEqual(request.fit, "natural")

    def test_rejects_empty_or_missing_segments(self) -> None:
        for arguments in ({}, {"segments": []}, {"segments": "x"}):
            with self.assertRaises(server.ToolError, msg=str(arguments)):
                self.parse(arguments)

    def test_rejects_anchor_with_both_or_neither_keys(self) -> None:
        for anchor in ({}, {"event_id": 1, "at_ms": 10}, {"event_id": None}):
            with self.assertRaises(server.ToolError, msg=str(anchor)):
                self.parse({"segments": [{"anchor": anchor, "text": "t"}]})

    def test_rejects_unknown_event_id(self) -> None:
        with self.assertRaises(server.ToolError):
            self.parse({"segments": [{"anchor": {"event_id": 9}, "text": "t"}]})

    def test_rejects_anchor_beyond_recording(self) -> None:
        with self.assertRaises(server.ToolError):
            self.parse({"segments": [{"anchor": {"at_ms": 60000}, "text": "t"}]})

    def test_rejects_bad_text(self) -> None:
        for text in ("", "   ", 5):
            with self.assertRaises(server.ToolError, msg=repr(text)):
                self.parse({"segments": [{"anchor": {"at_ms": 0}, "text": text}]})
        with self.assertRaises(server.ToolError):
            self.parse({"segments": [{"anchor": {"at_ms": 0}, "text": "a" * 2001}]})
        with self.assertRaises(server.ToolError):
            self.parse({"segments": [{"anchor": {"at_ms": 0}, "text": "a\x00b"}]})

    def test_rejects_unknown_keys_and_bad_enums(self) -> None:
        base = {"segments": [{"anchor": {"at_ms": 0}, "text": "t"}]}
        for extra in ({"bogus": 1}, {"engine": "festival"}, {"fit": "stretch"}):
            with self.assertRaises(server.ToolError, msg=str(extra)):
                self.parse({**base, **extra})

    def test_rejects_bad_numbers(self) -> None:
        base = {"segments": [{"anchor": {"at_ms": 0}, "text": "t"}]}
        for extra in ({"offset_ms": 99999}, {"tail_ms": -1}, {"offset_ms": True}):
            with self.assertRaises(server.ToolError, msg=str(extra)):
                self.parse({**base, **extra})


class AnchorResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = completed_job(
            self.tmp.name, capture_seconds=10.0, events=[event(1, 1000.0), event(2, 4000.0)]
        )

    def test_event_anchor_and_offset_are_applied(self) -> None:
        request = server.parse_narration_arguments(
            {"segments": [{"anchor": {"event_id": 2}, "text": "t"}], "offset_ms": 250},
            self.job,
        )
        anchored = server.resolve_segment_anchors(request, self.job)
        self.assertEqual(anchored[0][1], 4250.0)

    def test_segments_are_sorted_by_resolved_time(self) -> None:
        request = server.parse_narration_arguments(
            {
                "segments": [
                    {"anchor": {"event_id": 2}, "text": "later"},
                    {"anchor": {"event_id": 1}, "text": "earlier"},
                ]
            },
            self.job,
        )
        anchored = server.resolve_segment_anchors(request, self.job)
        self.assertEqual([item[0].text for item in anchored], ["earlier", "later"])


class AlignmentScheduleTests(unittest.TestCase):
    def make_request(self, fit: str = "natural", tail_ms: float = 300.0) -> server.NarrationRequest:
        return server.NarrationRequest(
            segments=[
                server.NarrationSegment(1, 1, None, "a"),
                server.NarrationSegment(2, 2, None, "b"),
            ],
            engine="edge",
            voice=None,
            offset_ms=0.0,
            fit=fit,
            tail_ms=tail_ms,
        )

    def test_natural_schedule_pushes_later_segments_forward(self) -> None:
        anchored = [
            (server.NarrationSegment(1, 1, None, "a"), 1000.0),
            (server.NarrationSegment(2, 2, None, "b"), 1500.0),
        ]
        clips = {1: clip("/tmp/1.mp3", 2000.0), 2: clip("/tmp/2.mp3", 500.0)}
        schedule = server.schedule_narration(anchored, clips, 10000.0, self.make_request())
        first, second = schedule.segments
        self.assertEqual(first.start_ms, 1000.0)
        self.assertEqual(first.shift_ms, 0.0)
        self.assertEqual(second.start_ms, 1000.0 + 2000.0 + server.NARRATION_MIN_GAP_MS)
        self.assertGreater(second.shift_ms, 0.0)
        self.assertEqual(schedule.total_ms, 10000.0)

    def test_lead_silence_is_removed_from_clip_length(self) -> None:
        anchored = [(server.NarrationSegment(1, 1, None, "a"), 500.0)]
        clips = {1: clip("/tmp/1.mp3", 2000.0, lead_ms=400.0)}
        schedule = server.schedule_narration(anchored, clips, 10000.0, self.make_request())
        self.assertEqual(schedule.segments[0].clip_ms, 1600.0)

    def test_compress_fit_uses_tempo_to_avoid_overrun(self) -> None:
        anchored = [
            (server.NarrationSegment(1, 1, None, "a"), 0.0),
            (server.NarrationSegment(2, 2, None, "b"), 1000.0),
        ]
        clips = {1: clip("/tmp/1.mp3", 3000.0), 2: clip("/tmp/2.mp3", 200.0)}
        schedule = server.schedule_narration(
            anchored, clips, 10000.0, self.make_request(fit="compress")
        )
        first = schedule.segments[0]
        self.assertTrue(first.compressed)
        self.assertGreater(first.tempo, 1.0)
        self.assertLessEqual(
            first.start_ms + first.clip_ms, anchored[1][1] - server.NARRATION_MIN_GAP_MS + 1e-6
        )

    def test_tail_extends_total_when_speech_outlasts_capture(self) -> None:
        anchored = [(server.NarrationSegment(1, 1, None, "a"), 900.0)]
        clips = {1: clip("/tmp/1.mp3", 5000.0)}
        schedule = server.schedule_narration(
            anchored, clips, 1000.0, self.make_request(tail_ms=250.0)
        )
        self.assertEqual(schedule.total_ms, 900.0 + 5000.0 + 250.0)


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
        argv = server.narration_mux_argv(
            server.Path("/tmp/a.webm"), server.Path("/tmp/n.wav"), server.Path("/tmp/out.webm")
        )
        self.assertEqual(argv[:4], ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"])
        self.assertIn("0:v:0", argv)
        self.assertIn("1:a:0", argv)
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertEqual(argv[argv.index("-c:a") + 1], "libopus")
        self.assertIn("-map_metadata", argv)
        self.assertEqual(argv[-3], "-f")
        self.assertEqual(argv[-2], "webm")
        self.assertEqual(argv[-1], "/tmp/out.webm")


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
        with patch.object(
            server, "probe_media", return_value={"format": {"duration": "1.25"}, "streams": []}
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

            with patch.object(server, "command_available", return_value=True):
                with patch.object(server, "run_command", side_effect=fake_run) as run:
                    with patch.object(server, "probe_audio_duration_ms", return_value=1200.0):
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
            with patch.object(server, "command_available", return_value=True):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(server.NARRATION_PIPER_MODEL_ENV, None)
                    with self.assertRaises(server.ToolError):
                        server.PiperTtsEngine().synthesize("Hi", None, out)

    def test_piper_synthesize_passes_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = server.Path(tmp) / "voice.onnx"
            model.write_bytes(b"model")
            out = server.Path(tmp) / "segment.wav"
            with patch.object(server, "command_available", return_value=True):
                with patch.object(server, "run_command") as run:
                    with patch.object(server, "probe_audio_duration_ms", return_value=900.0):
                        with patch.object(server, "detect_lead_silence_ms", return_value=0.0):
                            result = server.PiperTtsEngine().synthesize("Hi", str(model), out)
            self.assertEqual(result.lead_silence_ms, 0.0)
            argv = run.call_args[0][0]
            self.assertEqual(argv[argv.index("--model") + 1], str(model))


class EngineResolutionTests(unittest.TestCase):
    def test_prefers_edge_when_both_available(self) -> None:
        with patch.object(server, "command_available", return_value=True):
            self.assertEqual(server.resolve_tts_engine("auto").name, "edge")

    def test_falls_back_to_piper_when_edge_missing(self) -> None:
        with patch.object(
            server, "command_available", lambda name: name == server.NARRATION_PIPER_COMMAND
        ):
            self.assertEqual(server.resolve_tts_engine("auto").name, "piper")

    def test_no_engine_is_a_clear_error(self) -> None:
        with patch.object(server, "command_available", return_value=False):
            with self.assertRaises(server.ToolError) as ctx:
                server.resolve_tts_engine("auto")
        self.assertIn("no TTS engine", str(ctx.exception))

    def test_explicit_engine_must_be_installed(self) -> None:
        with patch.object(server, "command_available", return_value=False):
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
        with patch.object(
            server, "probe_media", return_value=self.webm_probe("opus", 1)
        ):
            summary = server.validate_recording_artifact(self.job, expect_audio=True)
        self.assertEqual(summary["codec"], "av1")

    def test_expect_audio_rejects_silent_and_wrong_codec(self) -> None:
        for probe in (self.webm_probe(), self.webm_probe("vorbis", 1), self.webm_probe("opus", 2)):
            with patch.object(server, "probe_media", return_value=probe):
                with self.assertRaises(server.ToolError, msg=str(probe)[:40]):
                    server.validate_recording_artifact(self.job, expect_audio=True)

    def test_silent_default_still_rejects_audio(self) -> None:
        with patch.object(server, "probe_media", return_value=self.webm_probe("opus", 1)):
            with self.assertRaises(server.ToolError):
                server.validate_recording_artifact(self.job)


class VoiceoverLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = server.RecordingManager()

    def attach(self, job: server.RecordingJob) -> None:
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
        with patch.object(server, "resolve_tts_engine", return_value=server.EdgeTtsEngine()):
            with patch.object(server, "perform_narration", side_effect=fake_perform):
                with patch.object(server, "validate_recording_artifact", return_value=fresh):
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
        with patch.object(server, "resolve_tts_engine", return_value=server.EdgeTtsEngine()):
            with patch.object(
                server, "perform_narration", side_effect=server.ToolError("tts exploded")
            ):
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
            with patch.object(server, "get_outputs", return_value=single_output()):
                job = server.new_recording_job({"max_duration_seconds": 30})
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
            with patch.object(server, "get_outputs", return_value=single_output()):
                job = server.new_recording_job({"max_duration_seconds": 30})
        os.close(job.log_fd)
        job.phase = "recording"
        self.manager._job = job
        self.manager.record_event("recording_status", {}, 101.0, True)
        self.assertEqual(job.events, [])


if __name__ == "__main__":
    unittest.main()

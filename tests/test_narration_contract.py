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
        return server.parse_narration_arguments(
            arguments, self.job, server.recording_capture_ms(self.job)
        )

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

    def test_subtitles_default_on_and_overridable(self) -> None:
        base = {"segments": [{"anchor": {"at_ms": 0}, "text": "t"}]}
        self.assertTrue(self.parse(base).subtitles)
        self.assertFalse(self.parse({**base, "subtitles": False}).subtitles)
        with self.assertRaises(server.ToolError):
            self.parse({**base, "subtitles": "no"})


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
            server.recording_capture_ms(self.job),
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
            server.recording_capture_ms(self.job),
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

if __name__ == "__main__":
    unittest.main()

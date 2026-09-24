from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import core, narration, recording, server, subtitles, tts

from support import completed_job, event


class TimestampTests(unittest.TestCase):
    def test_formats_ass_timestamps(self) -> None:
        self.assertEqual(subtitles.format_timestamp(0), "0:00:00.00")
        self.assertEqual(subtitles.format_timestamp(1500), "0:00:01.50")
        self.assertEqual(subtitles.format_timestamp(3_661_230), "1:01:01.23")

    def test_rounds_carries_into_seconds(self) -> None:
        self.assertEqual(subtitles.format_timestamp(999), "0:00:01.00")


class EscapeTests(unittest.TestCase):
    def test_escapes_override_syntax_and_newlines(self) -> None:
        self.assertEqual(
            subtitles.escape_text("a{b}\\c\nd"),
            "a\\{b\\}\\\\c\\Nd",
        )


class AssDocumentTests(unittest.TestCase):
    def test_builds_styled_dialogue(self) -> None:
        entries = [
            {"start_ms": 1000.0, "end_ms": 3500.0, "text": "First line"},
            {"start_ms": 3500.0, "end_ms": 6000.0, "text": "Second line"},
        ]
        document = subtitles.build_ass_document(entries, width=1920, height=1080)
        self.assertIn("PlayResX: 1920", document)
        self.assertIn("PlayResY: 1080", document)
        self.assertIn("Style: Caption,DejaVu Sans", document)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.50,Caption,,0,0,0,,First line", document)
        self.assertIn("Dialogue: 0,0:00:03.50,0:00:06.00,Caption,,0,0,0,,Second line", document)

    def test_write_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = subtitles.write_subtitle_file(Path(tmp), "doc")
            self.assertEqual(path.name, subtitles.SUBTITLE_FILE_NAME)
            self.assertEqual(path.read_text(encoding="utf-8"), "doc")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), recording.RECORDING_FILE_MODE)


class BurnMuxArgvTests(unittest.TestCase):
    def test_burn_argv_reencodes_with_subtitles(self) -> None:
        argv = subtitles.burn_mux_argv(
            Path("/tmp/a.webm"),
            Path("/tmp/n.wav"),
            Path("/tmp/captions.ass"),
            Path("/tmp/out.webm"),
            "libsvtav1",
        )
        joined = " ".join(argv)
        self.assertIn("subtitles=filename=", joined)
        self.assertIn("captions.ass", joined)
        self.assertEqual(argv[argv.index("-c:v") + 1], "libsvtav1")
        self.assertNotIn("copy", argv)
        self.assertEqual(argv[argv.index("-c:a") + 1], "libopus")
        self.assertEqual(argv[-3], "-f")
        self.assertEqual(argv[-1], "/tmp/out.webm")

    def test_copy_argv_stays_a_stream_copy(self) -> None:
        argv = narration.narration_mux_argv(
            Path("/tmp/a.webm"), Path("/tmp/n.wav"), Path("/tmp/out.webm")
        )
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")


class _FakeEngine:
    name = "edge"

    def synthesize(self, text: str, voice: str | None, out_path: Path) -> tts.TtsClip:
        out_path.write_bytes(b"audio")
        return tts.TtsClip(out_path, 1000.0, 100.0, [{"text": "x", "start_ms": 0, "end_ms": 1}])


class PerformNarrationSubtitlesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = completed_job(self.tmp.name, events=[event(1, 500.0)])

    def run_narration(self, arguments: dict) -> tuple[dict, list[list[str]]]:
        request = narration.parse_narration_arguments(arguments, self.job)
        captured: list[list[str]] = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            if argv[-1].endswith(".part"):
                Path(argv[-1]).write_bytes(b"muxed")
            return core.CommandResult(stdout=b"", stderr=b"", returncode=0)

        with patch.object(core, "run_command", side_effect=fake_run):
            with patch.object(recording, "validate_recording_artifact", return_value={}):
                metadata = narration.perform_narration(self.job, request, _FakeEngine())
        return metadata, captured

    def test_default_burns_subtitles(self) -> None:
        metadata, captured = self.run_narration(
            {"segments": [{"anchor": {"event_id": 1}, "text": "hello"}]}
        )
        self.assertTrue(metadata["subtitles"])
        self.assertTrue(any("subtitles=filename=" in " ".join(argv) for argv in captured))

    def test_disabled_copies_video(self) -> None:
        metadata, captured = self.run_narration(
            {"segments": [{"anchor": {"event_id": 1}, "text": "hello"}], "subtitles": False}
        )
        self.assertFalse(metadata["subtitles"])
        mux = captured[-1]
        self.assertEqual(mux[mux.index("-c:v") + 1], "copy")


if __name__ == "__main__":
    unittest.main()

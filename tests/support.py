from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import desktop, recording, server, tts



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


class FakeProcess:
    def __init__(self, returncode: int = 0, already_exited: bool = False) -> None:
        self.pid = 424242
        self._final_returncode = returncode
        self.returncode = returncode if already_exited else None
        self.popen_kwargs: dict = {}
        self.argv: list[str] = []
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def receive_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = 0


def exit_process_on_signal(process: FakeProcess):
    def fake_killpg(pid: int, sig: int) -> None:
        process.receive_signal(sig)

    return patch.object(server.os, "killpg", side_effect=fake_killpg)

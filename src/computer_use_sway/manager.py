from __future__ import annotations

import os
import subprocess
import signal
import threading
import time
from typing import Any

from . import core, narration, recording, scenes, timeline, tts


class RecordingManager:
    """Owns the single capture/finalization lifecycle for this server process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: recording.RecordingJob | None = None

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _intermediate_size(job: recording.RecordingJob) -> int:
        try:
            return job.intermediate.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _close_log_fd(job: recording.RecordingJob) -> None:
        if job.log_fd >= 0:
            try:
                os.close(job.log_fd)
            except OSError:
                pass
            job.log_fd = -1

    @staticmethod
    def _log_tail(job: recording.RecordingJob) -> str:
        try:
            data = job.log_path.read_bytes()
        except OSError:
            return ""
        return data[-recording.RECORDING_LOG_TAIL_BYTES:].decode("utf-8", errors="replace").strip()

    @staticmethod
    def _signal_group(process: subprocess.Popen, sig: int) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _wait_exited(process: subprocess.Popen, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(0.05)
        return process.poll() is not None

    def _terminate_process_group(self, process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        escalations = (
            (signal.SIGINT, recording.RECORDING_STOP_TIMEOUT_SECONDS, True),
            (signal.SIGTERM, recording.RECORDING_TERM_TIMEOUT_SECONDS, False),
            (signal.SIGKILL, recording.RECORDING_KILL_TIMEOUT_SECONDS, False),
        )
        for sig, timeout, was_graceful in escalations:
            if process.poll() is not None:
                return True
            self._signal_group(process, sig)
            if self._wait_exited(process, timeout):
                return was_graceful
        if process.poll() is None:
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pass
        return False

    @staticmethod
    def _discard_intermediate(job: recording.RecordingJob) -> None:
        job.intermediate.unlink(missing_ok=True)

    def _fail(self, job: recording.RecordingJob, message: str) -> None:
        job.phase = "failed"
        job.detail = f"{job.detail}; {message}" if job.detail else message
        job.artifact.unlink(missing_ok=True)

    def _cleanup_failed_start(self, job: recording.RecordingJob) -> None:
        self._close_log_fd(job)
        for path in (job.intermediate, job.artifact, job.log_path):
            path.unlink(missing_ok=True)

    # -- lifecycle -------------------------------------------------------

    def start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        core.require_binaries(["wf-recorder", "ffmpeg", "ffprobe"])
        with self._lock:
            job = self._job
            if job is not None and job.phase in {"recording", "stopping", "processing", "narrating"}:
                raise core.ToolError(
                    f"a recording is already active (phase: {job.phase}); call recording_status"
                )
            new_job = recording.new_recording_job(arguments)
            if new_job.fmt == "webm":
                new_job.encoder = recording.choose_video_encoder()
            try:
                new_job.process = subprocess.Popen(
                    recording.recording_capture_argv(new_job),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=new_job.log_fd,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                self._cleanup_failed_start(new_job)
                raise core.ToolError(f"failed to launch wf-recorder: {exc}") from exc
            time.sleep(recording.RECORDING_STARTUP_PROBE_SECONDS)
            if new_job.process.poll() is not None:
                detail = self._log_tail(new_job)
                self._cleanup_failed_start(new_job)
                raise core.ToolError(
                    f"wf-recorder exited during startup (code {new_job.process.returncode})"
                    + (f": {detail}" if detail else "")
                )
            self._job = new_job
            watchdog = threading.Thread(target=self._watchdog, args=(new_job,), daemon=True)
            new_job.thread = watchdog
            watchdog.start()
            return self._summary(new_job)

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None:
                return {"phase": "idle"}
            if job.phase == "recording":
                process = job.process
                if process is not None and process.poll() is not None:
                    job.ended_monotonic = time.monotonic()
                    self._close_log_fd(job)
                    if process.returncode != 0:
                        tail = self._log_tail(job)
                        self._fail(
                            job,
                            f"wf-recorder exited unexpectedly (code {process.returncode})"
                            + (f": {tail}" if tail else ""),
                        )
                    elif self._intermediate_size(job) == 0:
                        self._fail(job, "capture produced no data")
                    else:
                        job.phase = "processing"
                        job.thread = threading.Thread(
                            target=self._finalize, args=(job,), daemon=True
                        )
                        job.thread.start()
            return self._summary(job)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or job.phase != "recording":
                phase = job.phase if job is not None else "idle"
                raise core.ToolError(f"no active recording to stop (phase: {phase})")
            self._end_capture(job)
            return self._summary(job)

    def record_event(
        self,
        name: str,
        arguments: dict[str, Any],
        started_monotonic: float,
        ok: bool,
    ) -> None:
        if name not in timeline.TIMELINE_ACTION_TOOLS:
            return
        with self._lock:
            job = self._job
            if job is None or job.phase != "recording":
                return
            job.events.append(
                {
                    "id": len(job.events) + 1,
                    "t_ms": timeline.recording_relative_ms(job.started_monotonic, started_monotonic),
                    "tool": name,
                    "ok": bool(ok),
                    "payload": timeline.timeline_payload(name, arguments),
                }
            )

    def timeline(self) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None:
                return {"phase": "idle", "event_count": 0, "events": []}
            document = timeline.timeline_document(job)
            document["phase"] = job.phase
            if job.timeline_path is not None and job.timeline_path.exists():
                document["timeline_path"] = str(job.timeline_path)
            return document

    def voiceover(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None:
                raise core.ToolError("no recording to narrate; record something first")
            if job.fmt == "gif":
                raise core.ToolError(
                    "GIF cannot carry an audio track; record format=webm to add narration"
                )
            if job.phase != "completed":
                raise core.ToolError(
                    f"recording must be completed before narration (phase: {job.phase})"
                )
            request = narration.parse_narration_arguments(arguments, job)
            engine = tts.resolve_tts_engine(request.engine)
            job.phase = "narrating"
            job.detail = None
            job.thread = threading.Thread(
                target=self._narrate, args=(job, request, engine), daemon=True
            )
            job.thread.start()
            return self._summary(job)

    def scenes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        threshold = core.strict_number(
            arguments.get("threshold", scenes.SCENE_DEFAULT_THRESHOLD),
            "threshold",
            minimum=scenes.SCENE_MIN_THRESHOLD,
            maximum=scenes.SCENE_MAX_THRESHOLD,
        )
        max_scenes = core.strict_int(arguments.get("max_scenes", scenes.SCENE_DEFAULT_MAX), "max_scenes")
        if max_scenes < 1 or max_scenes > scenes.SCENE_MAX_CUTS:
            raise core.ToolError(f"max_scenes must be between 1 and {scenes.SCENE_MAX_CUTS}")
        ocr = bool(arguments.get("ocr", False))
        with self._lock:
            job = self._job
            if job is None or job.phase != "completed":
                raise core.ToolError("no completed recording to analyze")
            artifact = job.artifact
            workdir = job.directory
            recording_id = job.id
        core.require_binaries(["ffmpeg", "ffprobe"])
        cuts = scenes.detect_scene_cuts(artifact, threshold, max_scenes)
        if ocr:
            workdir.mkdir(mode=recording.RECORDING_DIR_MODE, exist_ok=True)
            for cut in cuts:
                cut["text"] = scenes.ocr_frame(artifact, cut["t_ms"], workdir)
        return {
            "id": recording_id,
            "path": str(artifact),
            "threshold": threshold,
            "approximate": True,
            "note": "scene cuts are approximate fallback anchors, not the sync source",
            "scene_count": len(cuts),
            "scenes": cuts,
        }

    def shutdown(self) -> None:
        with self._lock:
            job = self._job
            if job is None or job.phase != "recording":
                return
            if job.detail is None:
                job.detail = "capture ended at server shutdown"
            self._end_capture(job)

    # -- internals -------------------------------------------------------

    def _end_capture(self, job: recording.RecordingJob) -> None:
        """Stop the recorder and begin finalization. Caller must hold the lock."""
        job.phase = "stopping"
        process = job.process
        if process is not None and process.poll() is None:
            job.forced = not self._terminate_process_group(process)
        job.ended_monotonic = time.monotonic()
        self._close_log_fd(job)
        if self._intermediate_size(job) == 0:
            self._fail(job, "capture produced no data")
            return
        job.phase = "processing"
        job.thread = threading.Thread(target=self._finalize, args=(job,), daemon=True)
        job.thread.start()

    def _watchdog(self, job: recording.RecordingJob) -> None:
        deadline = job.started_monotonic + job.max_duration
        while True:
            with self._lock:
                if self._job is not job or job.phase != "recording":
                    return
                exceeded_time = time.monotonic() >= deadline
                exceeded_size = self._intermediate_size(job) > recording.RECORDING_MAX_INTERMEDIATE_BYTES
                if exceeded_time or exceeded_size:
                    job.auto_stopped = True
                    if exceeded_size and not exceeded_time:
                        job.detail = "recording stopped: intermediate size limit reached"
                    self._end_capture(job)
                    return
            time.sleep(0.5)

    def _finalize(self, job: recording.RecordingJob) -> None:
        try:
            summary = recording.finalize_recording(job)
            job.artifact.chmod(recording.RECORDING_FILE_MODE)
            timeline_path = timeline.write_timeline_sidecar(job)
        except core.ToolError as exc:
            with self._lock:
                self._fail(job, f"finalization failed: {exc}")
            return
        except Exception as exc:
            core.eprint(f"unexpected finalization error for {job.id}: {exc}")
            with self._lock:
                self._fail(job, "unexpected finalization failure; see server log")
            return
        capture_seconds = max(
            (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 0.0
        )
        try:
            artifact_bytes = job.artifact.stat().st_size
        except OSError:
            artifact_bytes = 0
        result = {
            "id": job.id,
            "phase": "completed",
            "format": job.fmt,
            "output": job.output,
            "region": job.region,
            "path": str(job.artifact),
            "bytes": artifact_bytes,
            "capture_seconds": round(capture_seconds, 3),
            "graceful": not job.forced,
            "audio_included": False,
            "cursor_included": True,
            "auto_stopped": job.auto_stopped,
            "timeline_path": str(timeline_path) if timeline_path is not None else None,
            "timeline_event_count": len(job.events),
            **summary,
        }
        with self._lock:
            job.result = result
            job.phase = "completed"
            self._discard_intermediate(job)
            job.log_path.unlink(missing_ok=True)

    def _narrate(
        self, job: recording.RecordingJob, request: narration.NarrationRequest, engine: tts.TtsEngine
    ) -> None:
        try:
            narration_meta = narration.perform_narration(job, request, engine)
            summary = recording.validate_recording_artifact(job, expect_audio=True)
        except core.ToolError as exc:
            with self._lock:
                job.phase = "completed"
                job.detail = f"narration failed: {exc}"
                if job.result is not None:
                    job.result["narration"] = {"error": str(exc)}
            return
        except Exception as exc:
            core.eprint(f"unexpected narration error for {job.id}: {exc}")
            with self._lock:
                job.phase = "completed"
                job.detail = "unexpected narration failure; see server log"
                if job.result is not None:
                    job.result["narration"] = {"error": "unexpected narration failure"}
            return
        with self._lock:
            job.narration = narration_meta
            job.phase = "completed"
            job.detail = None
            if job.result is not None:
                job.result.update(summary)
                try:
                    job.result["bytes"] = job.artifact.stat().st_size
                except OSError:
                    pass
                job.result["audio_included"] = True
                job.result["narration"] = narration_meta

    def _summary(self, job: recording.RecordingJob) -> dict[str, Any]:
        base = {
            "id": job.id,
            "format": job.fmt,
            "output": job.output,
            "region": job.region,
            "audio_included": bool(job.result and job.result.get("audio_included")),
            "cursor_included": True,
        }
        if job.phase == "recording":
            return {
                **base,
                "phase": "recording",
                "width": job.width,
                "height": job.height,
                "elapsed_seconds": round(time.monotonic() - job.started_monotonic, 3),
                "max_duration_seconds": job.max_duration,
                "bytes": self._intermediate_size(job),
            }
        if job.phase == "stopping":
            return {
                **base,
                "phase": "stopping",
                "capture_seconds": round(
                    (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 3
                ),
            }
        if job.phase == "processing":
            return {
                **base,
                "phase": "processing",
                "capture_seconds": round(
                    (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 3
                ),
                "note": "finalizing artifact; poll recording_status until completed or failed",
            }
        if job.phase == "narrating":
            return {
                **base,
                "phase": "narrating",
                "capture_seconds": round(
                    (job.ended_monotonic or time.monotonic()) - job.started_monotonic, 3
                ),
                "note": "synthesizing and muxing narration; poll recording_status",
            }
        if job.phase == "completed":
            return dict(job.result or {"id": job.id, "phase": "completed"})
        return {
            **base,
            "phase": "failed",
            "detail": job.detail or "recording failed",
            "intermediate_path": str(job.intermediate) if job.intermediate.exists() else None,
            "log_path": str(job.log_path) if job.log_path.exists() else None,
        }


RECORDINGS = RecordingManager()

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import core, recording, subtitles, tts


@dataclass
class NarrationSegment:
    index: int
    anchor_event_id: int | None
    anchor_at_ms: float | None
    text: str


@dataclass
class NarrationRequest:
    segments: list[NarrationSegment]
    engine: str
    voice: str | None
    offset_ms: float
    fit: str
    tail_ms: float
    subtitles: bool = True


@dataclass
class ScheduledSegment:
    index: int
    anchor_ms: float
    start_ms: float
    clip_ms: float
    duration_ms: float
    lead_silence_ms: float
    shift_ms: float
    tempo: float
    compressed: bool
    word_count: int


@dataclass
class NarrationSchedule:
    segments: list[ScheduledSegment]
    total_ms: float


def recording_capture_ms(job: RecordingJob) -> float:
    end = job.ended_monotonic if job.ended_monotonic is not None else time.monotonic()
    return round(max(end - job.started_monotonic, 0.0) * 1000.0, 3)


def parse_narration_segment(
    raw: Any, index: int, event_ids: set[int], capture_ms: float
) -> NarrationSegment:
    if not isinstance(raw, dict):
        raise core.ToolError(f"segments[{index}] must be an object")
    unknown = set(raw) - {"anchor", "text"}
    if unknown:
        raise core.ToolError(f"segments[{index}] has unknown key(s): {', '.join(sorted(unknown))}")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise core.ToolError(f"segments[{index}].text must be a non-empty string")
    if "\x00" in text:
        raise core.ToolError(f"segments[{index}].text must not contain NUL bytes")
    if len(text) > tts.NARRATION_MAX_SEGMENT_CHARS:
        raise core.ToolError(
            f"segments[{index}].text exceeds {tts.NARRATION_MAX_SEGMENT_CHARS} characters"
        )
    anchor = raw.get("anchor")
    if not isinstance(anchor, dict):
        raise core.ToolError(f"segments[{index}].anchor must be an object")
    unknown = set(anchor) - {"event_id", "at_ms"}
    if unknown:
        raise core.ToolError(
            f"segments[{index}].anchor has unknown key(s): {', '.join(sorted(unknown))}"
        )
    has_event = "event_id" in anchor
    has_at = "at_ms" in anchor
    if has_event == has_at:
        raise core.ToolError(
            f"segments[{index}].anchor must contain exactly one of event_id or at_ms"
        )
    if has_event:
        event_id = core.strict_int(anchor["event_id"], f"segments[{index}].anchor.event_id")
        if event_id not in event_ids:
            raise core.ToolError(
                f"segments[{index}].anchor.event_id {event_id} is not in the recording timeline"
            )
        return NarrationSegment(index, event_id, None, text)
    at_ms = core.strict_number(
        anchor["at_ms"], f"segments[{index}].anchor.at_ms", minimum=0.0
    )
    if at_ms > capture_ms + tts.NARRATION_ANCHOR_SLACK_MS:
        raise core.ToolError(
            f"segments[{index}].anchor.at_ms {at_ms:g} is beyond the recording duration"
        )
    return NarrationSegment(index, None, at_ms, text)


def parse_narration_arguments(arguments: dict[str, Any], job: RecordingJob) -> NarrationRequest:
    if not isinstance(arguments, dict):
        raise core.ToolError("narration arguments must be an object")
    unknown = set(arguments) - {"segments", "engine", "voice", "offset_ms", "fit", "tail_ms", "subtitles"}
    if unknown:
        raise core.ToolError(f"unknown narration key(s): {', '.join(sorted(unknown))}")
    raw_segments = arguments.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise core.ToolError("segments must be a non-empty list")
    engine = arguments.get("engine", "auto")
    if engine not in tts.NARRATION_ENGINES:
        raise core.ToolError("engine must be one of: " + ", ".join(tts.NARRATION_ENGINES))
    fit = arguments.get("fit", "natural")
    if fit not in tts.NARRATION_FITS:
        raise core.ToolError("fit must be one of: " + ", ".join(tts.NARRATION_FITS))
    voice = arguments.get("voice")
    if voice is not None and (not isinstance(voice, str) or not voice.strip()):
        raise core.ToolError("voice must be a non-empty string")
    offset_ms = core.strict_number(
        arguments.get("offset_ms", 0.0),
        "offset_ms",
        minimum=-tts.NARRATION_MAX_OFFSET_MS,
        maximum=tts.NARRATION_MAX_OFFSET_MS,
    )
    tail_ms = core.strict_number(
        arguments.get("tail_ms", tts.NARRATION_DEFAULT_TAIL_MS),
        "tail_ms",
        minimum=0.0,
        maximum=tts.NARRATION_MAX_TAIL_MS,
    )
    subtitles_enabled = arguments.get("subtitles", True)
    if not isinstance(subtitles_enabled, bool):
        raise core.ToolError("subtitles must be a boolean")
    capture_ms = recording_capture_ms(job)
    event_ids = {int(event["id"]) for event in job.events}
    segments = [
        parse_narration_segment(raw, index + 1, event_ids, capture_ms)
        for index, raw in enumerate(raw_segments)
    ]
    if sum(len(segment.text) for segment in segments) > core.TEXT_LIMIT:
        raise core.ToolError(f"narration text exceeds the total limit of {core.TEXT_LIMIT} characters")
    return NarrationRequest(
        segments=segments,
        engine=engine,
        voice=voice,
        offset_ms=offset_ms,
        fit=fit,
        tail_ms=tail_ms,
        subtitles=subtitles_enabled,
    )


def resolve_segment_anchors(
    request: NarrationRequest, job: RecordingJob
) -> list[tuple[NarrationSegment, float]]:
    event_times = {int(event["id"]): float(event["t_ms"]) for event in job.events}
    anchored: list[tuple[NarrationSegment, float]] = []
    for segment in request.segments:
        if segment.anchor_event_id is not None:
            anchor_ms = event_times[segment.anchor_event_id]
        else:
            anchor_ms = float(segment.anchor_at_ms)
        anchored.append((segment, anchor_ms + request.offset_ms))
    anchored.sort(key=lambda item: (item[1], item[0].index))
    return anchored


def atempo_filters(ratio: float) -> list[str]:
    filters: list[str] = []
    remaining = ratio
    guard = 0
    while remaining > tts.NARRATION_TEMPO_MAX and guard < tts.NARRATION_TEMPO_MAX_FILTERS:
        filters.append(f"atempo={tts.NARRATION_TEMPO_MAX:g}")
        remaining /= tts.NARRATION_TEMPO_MAX
        guard += 1
    while remaining < tts.NARRATION_TEMPO_MIN and guard < tts.NARRATION_TEMPO_MAX_FILTERS:
        filters.append(f"atempo={tts.NARRATION_TEMPO_MIN:g}")
        remaining /= tts.NARRATION_TEMPO_MIN
        guard += 1
    filters.append(f"atempo={remaining:.6f}")
    return filters


def schedule_narration(
    anchored: list[tuple[NarrationSegment, float]],
    clips: dict[int, tts.TtsClip],
    capture_ms: float,
    request: NarrationRequest,
) -> NarrationSchedule:
    scheduled: list[ScheduledSegment] = []
    previous_end = 0.0
    for position, (segment, anchor_ms) in enumerate(anchored):
        clip = clips[segment.index]
        lead_silence_ms = min(clip.lead_silence_ms, tts.NARRATION_MAX_LEAD_SILENCE_MS)
        clip_ms = max(clip.duration_ms - lead_silence_ms, 0.0)
        start_ms = max(anchor_ms, previous_end + tts.NARRATION_MIN_GAP_MS)
        tempo = 1.0
        compressed = False
        if request.fit == "compress":
            if position + 1 < len(anchored):
                limit_ms = anchored[position + 1][1] - tts.NARRATION_MIN_GAP_MS
            else:
                limit_ms = capture_ms
            available = limit_ms - start_ms
            if available > 0 and clip_ms > available:
                tempo = clip_ms / available
                clip_ms = clip_ms / tempo
                compressed = True
        scheduled.append(
            ScheduledSegment(
                index=segment.index,
                anchor_ms=anchor_ms,
                start_ms=start_ms,
                clip_ms=clip_ms,
                duration_ms=clip.duration_ms,
                lead_silence_ms=lead_silence_ms,
                shift_ms=start_ms - anchor_ms,
                tempo=tempo,
                compressed=compressed,
                word_count=len(clip.words),
            )
        )
        previous_end = start_ms + clip_ms
    total_ms = max(capture_ms, previous_end + request.tail_ms)
    return NarrationSchedule(segments=scheduled, total_ms=total_ms)


def narration_track_argv(
    anchored: list[tuple[NarrationSegment, float]],
    clips: dict[int, tts.TtsClip],
    schedule: NarrationSchedule,
    out_path: Path,
) -> list[str]:
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for segment, _ in anchored:
        argv.extend(["-i", str(clips[segment.index].path)])
    parts = [
        "anullsrc=channel_layout=stereo:sample_rate="
        f"{tts.NARRATION_SAMPLE_RATE}:duration={schedule.total_ms / 1000.0:.3f}[bed]"
    ]
    labels = ["[bed]"]
    for position, item in enumerate(schedule.segments):
        filters: list[str] = []
        if item.lead_silence_ms > 0:
            filters.append(f"atrim=start={item.lead_silence_ms / 1000.0:.6f}")
        filters.append("asetpts=PTS-STARTPTS")
        if abs(item.tempo - 1.0) > 1e-9:
            filters.extend(atempo_filters(item.tempo))
        filters.append(f"aresample={tts.NARRATION_SAMPLE_RATE}")
        filters.append("aformat=channel_layouts=stereo")
        delay = int(round(item.start_ms))
        filters.append(f"adelay={delay}|{delay}")
        label = f"[n{position}]"
        parts.append(f"[{position}:a]" + ",".join(filters) + label)
        labels.append(label)
    parts.append(
        "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]"
    )
    argv.extend(
        [
            "-filter_complex",
            ";".join(parts),
            "-map",
            "[aout]",
            "-t",
            f"{schedule.total_ms / 1000.0:.3f}",
            "-ac",
            "2",
            "-ar",
            str(tts.NARRATION_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )
    return argv


def narration_mux_argv(artifact: Path, track: Path, out_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(artifact),
        "-i",
        str(track),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "libopus",
        "-b:a",
        tts.NARRATION_AUDIO_BITRATE,
        "-ac",
        "2",
        "-ar",
        str(tts.NARRATION_SAMPLE_RATE),
        "-map_metadata",
        "-1",
        "-f",
        "webm",
        str(out_path),
    ]


def perform_narration(
    job: RecordingJob, request: NarrationRequest, engine: TtsEngine
) -> dict[str, Any]:
    capture_ms = recording_capture_ms(job)
    anchored = resolve_segment_anchors(request, job)
    workdir = job.directory / f"{job.id}.narration"
    workdir.mkdir(mode=recording.RECORDING_DIR_MODE, exist_ok=True)
    os.chmod(workdir, recording.RECORDING_DIR_MODE)
    try:
        clips: dict[int, tts.TtsClip] = {}
        for segment, _ in anchored:
            suffix = ".mp3" if engine.name == "edge" else ".wav"
            out_path = workdir / f"segment-{segment.index:03d}{suffix}"
            clips[segment.index] = engine.synthesize(segment.text, request.voice, out_path)
        schedule = schedule_narration(anchored, clips, capture_ms, request)
        segment_text = {segment.index: segment.text for segment, _ in anchored}
        track_path = workdir / "narration.wav"
        core.run_command(
            narration_track_argv(anchored, clips, schedule, track_path),
            timeout=tts.NARRATION_BUILD_TIMEOUT_SECONDS,
        )
        temp_path = job.directory / f"{job.id}.narrated.webm.part"
        if request.subtitles:
            entries = [
                {
                    "start_ms": item.start_ms,
                    "end_ms": item.start_ms + item.clip_ms,
                    "text": segment_text[item.index],
                }
                for item in schedule.segments
            ]
            document = subtitles.build_ass_document(entries, job.width, job.height)
            subtitle_path = subtitles.write_subtitle_file(workdir, document)
            mux_argv = subtitles.burn_mux_argv(
                job.artifact, track_path, subtitle_path, temp_path, job.encoder
            )
        else:
            mux_argv = narration_mux_argv(job.artifact, track_path, temp_path)
        core.run_command(mux_argv, timeout=tts.NARRATION_BUILD_TIMEOUT_SECONDS)
        try:
            recording.validate_recording_artifact(job, expect_audio=True, path=temp_path)
            os.chmod(temp_path, recording.RECORDING_FILE_MODE)
            os.replace(temp_path, job.artifact)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return {
            "engine": engine.name,
            "voice": request.voice,
            "offset_ms": request.offset_ms,
            "fit": request.fit,
            "subtitles": request.subtitles,
            "segment_count": len(schedule.segments),
            "total_duration_ms": round(schedule.total_ms, 3),
            "segments": [
                {
                    "index": item.index,
                    "anchor_ms": round(item.anchor_ms, 3),
                    "start_ms": round(item.start_ms, 3),
                    "duration_ms": round(item.duration_ms, 3),
                    "lead_silence_ms": round(item.lead_silence_ms, 3),
                    "shift_ms": round(item.shift_ms, 3),
                    "tempo": round(item.tempo, 6),
                    "compressed": item.compressed,
                    "word_count": item.word_count,
                }
                for item in schedule.segments
            ],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

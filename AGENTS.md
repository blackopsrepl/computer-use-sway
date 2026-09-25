# Repository Guidelines

Guidance for coding agents working in this repository.

## Product Contract

- `computer-use-sway` is a zero-runtime-dependency Python MCP stdio server that
  controls a Sway/Wayland desktop. `dependencies = []` in `pyproject.toml` is a
  hard contract; any TTS, OCR, or scene tooling is optional and runtime-detected,
  and a missing binary is a clear `ToolError`, never a hard import failure.
- The server is deterministic and never embeds an LLM. It owns timestamping,
  speech synthesis, alignment, and muxing; the calling agent writes the narration
  prose. Narration is strictly opt-in and must be explicitly requested.
- The silent recording contract is frozen: silent recordings carry no audio, the
  artifact has exactly one video stream (H.264 MP4 by default, AV1 WebM, or GIF),
  and `validate_recording_artifact` keeps rejecting unexpected audio.
- Only one recording may be active per process; `RecordingManager` is the single
  owner. `recording_start` must reject a second concurrent job.
- GIF cannot carry audio; `recording_voiceover` refuses `format=gif`.
- The `tools/call` dispatch layer timestamps every action/observation tool call
  into the active recording timeline; keep that hook intact.

## Project Structure

- `src/computer_use_sway/core.py`: tool errors, subprocess execution, session
  environment recovery, and strict parsing helpers.
- `desktop.py`: Sway output, seat, tree, window, coordinate, and cursor helpers.
- `media.py`: `ffprobe`-backed media probing.
- `recording.py`: recording job model, path/argument construction, artifact
  validation, and finalization.
- `timeline.py`: recording-relative event timeline and sidecar.
- `tts.py`: pluggable `edge-tts`/`piper` engines and audio parsing.
- `narration.py`: narration contract, anchor resolution, scheduling, and muxing.
- `subtitles.py`: styled ASS captions and the burn-in mux argv.
- `scenes.py`: approximate scene-cut and OCR fallback anchors.
- `manager.py`: the single recording lifecycle owner and `RECORDINGS`.
- `tools.py` / `specs.py`: MCP tool wrappers and JSON schemas.
- `server.py`: MCP protocol loop and CLI facade.
- `tests/`: deterministic unit tests plus `support.py` helpers.

## Engineering Rules

- Any file that reaches **500 lines** must be split into multiple files. This is
  enforced by `scripts/check_file_length.py` through `make check`. Move code into
  a new focused module instead of growing an existing one; `CHANGELOG.md` and
  binary assets are exempt.
- Do not hand-edit `CHANGELOG.md` or version numbers. `.versionrc.js` owns
  `pyproject.toml` and `SERVER_VERSION`, and releases run through
  `commit-and-tag-version`.
- When a task is simple, do the simple thing. Do not expand scope into unrelated
  critical paths.

## Validation

Run:

```bash
make check
```

That runs the unit tests, bytecode compilation, and the file-length check.

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, recording and narration workflows.
- `WIREFRAME.md`: shipped MCP tool surface and runtime contract.
- `docs/architecture.md`: layers, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.
- `AGENTS.md`: repository rules and validation.

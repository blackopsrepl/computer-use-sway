# Architecture

`computer-use-sway` is a single-process MCP stdio server. It keeps no state between sessions, but while a recording is active it owns a capture child process, a watchdog thread, a finalization thread, and transient files under the user's runtime directory.

## Boundaries

- MCP transport: JSON-RPC messages over stdin/stdout.
- Desktop backend: Sway IPC through `swaymsg`.
- Screenshots: `grim` returns PNG bytes directly to MCP image content.
- Input: pointer actions use Sway seat cursor commands; text and key events use `wtype`.
- Clipboard: text-only set/get through `wl-copy` and `wl-paste`.
- Recording: `wf-recorder` captures a lossless Matroska intermediate; `ffmpeg` converts it to the requested artifact and `ffprobe` validates it.

## Environment Recovery

MCP hosts often launch stdio servers with a stripped environment. Before talking to Sway, the server reconstructs:

- `XDG_RUNTIME_DIR` as `/run/user/<uid>` when available.
- `SWAYSOCK` from the newest Sway IPC socket in that runtime dir.
- `WAYLAND_DISPLAY` from the newest Wayland socket in that runtime dir.

This lets a fresh MCP host reach the current desktop session without shell-specific launch wrappers.

## Recording Lifecycle

State machine: `recording` → `stopping` → `processing` → `completed` | `failed`; `idle` when no job exists. Only one job exists at a time; a completed or failed job remains visible through `recording_status` until the next start.

- `recording_start` validates the format, duration cap, exact output name, and region containment (the region must lie fully inside the selected output; corner-touching across monitor gaps is rejected), allocates private paths, launches `wf-recorder` in its own process group with stdin/stdout on devnull and stderr in a bounded log, probes for immediate startup failure, and starts a watchdog thread.
- Capture writes a constant-frame-rate, lossless `libx264rgb` Matroska intermediate. Nothing touches MCP stdio.
- The watchdog stops the recording at the duration deadline or when the intermediate exceeds 1 GiB.
- `recording_stop` and the watchdog escalate `SIGINT` → `SIGTERM` → `SIGKILL` with bounded waits and record whether the stop was graceful. Recorder processes that exit on their own are reaped by `recording_status`.
- Finalization runs in a daemon thread: `ffmpeg` converts the intermediate to AV1/WebM (30 fps, `yuv420p`, even-dimension padding, stripped metadata, keyframes every 2 seconds, `libsvtav1` with a `libaom-av1` fallback) or to a constrained GIF (12 fps, ≤ 960 px, palettegen/paletteuse, infinite loop). `ffprobe` then gates completion on container, codec, exactly one video stream, zero audio streams, and a readable duration.
- On success the intermediate and log are deleted; on failure they are preserved and reported with the artifact path removed.
- On MCP stdin EOF, SIGINT, or SIGTERM, the server stops any active capture before exiting; the recorder can never outlive the server. If finalization cannot finish, the intermediate survives for recovery.
- Recordings live in `$XDG_RUNTIME_DIR/computer-use-sway/recordings` with `0700` directories and `0600` files. Callers receive server-generated paths and can never pass paths, PIDs, codec names, or extra recorder arguments through the MCP API.

## Tool Error Model

Expected desktop and validation failures are reported as MCP tool results with `isError: true`. Unexpected Python exceptions are logged to stderr and returned as a generic internal tool error.

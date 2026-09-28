# Architecture

`computer-use-sway` is a single-process MCP stdio server. It keeps no state between sessions and owns no long-lived child process: it runs one command, reads the result, publishes the call to the timeline stream, and forgets it.

## Boundaries

- MCP transport: JSON-RPC messages over stdin/stdout.
- Desktop backend: Sway IPC through `swaymsg`.
- Screenshots: `grim` returns PNG bytes; by default they become MCP image
  content, or `save_path` writes them to a `0600` file and returns text-only
  metadata so long runs do not exhaust a host's per-request image budget.
- Input: pointer actions use Sway seat cursor commands; text and key events use `wtype`.
- Clipboard: text-only set/get through `wl-copy` and `wl-paste`.
- Timeline: every dispatched call is appended to
  `$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl` with an `at_monotonic`
  timestamp, so a recorder in another process can anchor narration to real
  actions. This server owns no recording state and never writes a take.

## Environment Recovery

MCP hosts often launch stdio servers with a stripped environment. Before talking to Sway, the server reconstructs:

- `XDG_RUNTIME_DIR` as `/run/user/<uid>` when available.
- `SWAYSOCK` from the newest Sway IPC socket in that runtime dir.
- `WAYLAND_DISPLAY` from the newest Wayland socket in that runtime dir.

This lets a fresh MCP host reach the current desktop session without shell-specific launch wrappers.

## Recording, Narration, And The Stream

Screen recording, the take timeline, speech synthesis, captions and muxing now
live in a separate MCP server, **`seshat`** (<https://github.com/blackopsrepl/seshat>).
This server publishes every action it dispatches to that server's timeline stream
(`$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl`), which is what makes a
take of this desktop narratable. The contract, the tool surface and the workflows
are documented in that repository's README and WIREFRAME.

## Module Layout

- `core.py`: tool errors, subprocess execution, session environment recovery, and
  strict parsing helpers. Imports nothing from the package.
- `desktop.py`: Sway output, seat, tree, window, coordinate and cursor helpers.
  Depends on `core`.
- `timeline.py`: the published timeline stream — the curated payload rules, the
  stream path, startup truncation, and `append_event`. Depends on the standard
  library only, so publishing can never fail into a tool call.
- `tools.py`: the twelve MCP tool wrappers. Depends on `core`, `desktop`.
- `specs.py`: JSON schemas for that surface. Depends on `core`.
- `server.py`: MCP protocol loop, the `tools/call` dispatch that publishes every
  call, and the CLI facade (`--self-test`, `--doctor`, Codex registration).
- `version.py`: server name and version, imported by both the CLI and the tools.
- `tests/`: deterministic unit tests for the tool layer, the protocol surface and
  the published stream.

Dependency direction is one-way: `core` → `desktop`/`timeline` → `tools` → `specs`
/ `server`. Nothing here imports the recording stack, because this server no
longer has one.

## Tool Error Model

Expected desktop and validation failures are reported as MCP tool results with `isError: true`. Unexpected Python exceptions are logged to stderr and returned as a generic internal tool error.

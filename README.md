# computer-use-sway

<p align="center">
  <img src="assets/mascot.png" alt="computer-use-sway mascot" width="260">
</p>

`computer-use-sway` is a local MCP stdio server that lets an MCP client inspect and operate the current Sway desktop session.

It exposes screen, window, pointer, keyboard, clipboard, and recording tools through Sway-native commands. It is designed for Linux desktops running Sway on Wayland.

## Capabilities

- Inspect active outputs, seats, focused windows, and required binaries.
- Capture screenshots as MCP image content or data URLs.
- Return a simplified Sway window tree.
- Focus windows by container ID, app ID, class, or title.
- Move the pointer, click, drag, and scroll.
- Type text and send key chords through `wtype`.
- Read and write text clipboard contents through `wl-clipboard`.
- Record a screen region as a silent web-ready video or constrained GIF.

## Requirements

Runtime commands:

- `swaymsg`
- `grim`
- `wtype`
- `wl-copy`
- `wl-paste`
- `wf-recorder` (recording)
- `ffmpeg` and `ffprobe` (recording)

On openSUSE:

```bash
sudo zypper in python3 sway grim wtype wl-clipboard wf-recorder ffmpeg
```

On Debian or Ubuntu:

```bash
sudo apt install python3 sway grim wtype wl-clipboard wf-recorder ffmpeg
```

Non-recording tools work without the recording binaries; recording tools fail with a clear error until they are installed.

## Install

From a checkout:

```bash
python3 -m pip install .
```

For editable development:

```bash
python3 -m pip install -e .
```

## Register With Codex

After installation:

```bash
computer-use-sway --install-codex-mcp
```

That registers a stdio MCP server named `computer-use-sway`.

If the command is not on `PATH`, set the exact command Codex should launch:

```bash
COMPUTER_USE_SWAY_COMMAND="/path/to/venv/bin/computer-use-sway" computer-use-sway --install-codex-mcp
```

Verify:

```bash
computer-use-sway --self-test
codex mcp list
```

Remove the Codex registration:

```bash
computer-use-sway --uninstall-codex-mcp
```

## Run As MCP Server

Most clients should launch the command directly over stdio:

```bash
computer-use-sway
```

During source checkout development, this also works:

```bash
PYTHONPATH=src python3 -m computer_use_sway
```

## Recording

Recording is a three-step lifecycle designed for autonomous agents:

1. `recording_start` — begin capture of one output (or a region inside it).
2. `recording_stop` — stop capture and begin finalization.
3. `recording_status` — poll until the phase is `completed` or `failed`.

`recording_start` accepts `output` (inferred when exactly one output is active), an
optional `region` fully contained in that output, `format`, and
`max_duration_seconds`. Only one recording may be active at a time.

Formats:

- `webm` (default): silent AV1 video in WebM at 30 fps, `yuv420p`, periodic
  keyframes for seeking. Play it with
  `<video autoplay loop muted playsinline>`. AV1 is not decodable on some older
  Apple hardware; provide an H.264/MP4 fallback if you must support those
  devices.
- `gif`: deliberately constrained fallback for contexts that cannot run
  `<video>` (some email and chat clients). Maximum 15 seconds, 12 fps, 960 px
  maximum width, infinite loop. For ordinary web pages, prefer `webm`; a GIF of
  the same content is far larger.

Both formats are silent by design; there is no audio parameter.

Behavior and limits:

- The pointer cursor is always included in recordings.
- Capture goes to a lossless Matroska intermediate first; the requested artifact
  is produced after stop, so `recording_stop` returns quickly and finalization
  is observed through `recording_status` (`recording` → `stopping` →
  `processing` → `completed`/`failed`).
- Recordings are written to
  `$XDG_RUNTIME_DIR/computer-use-sway/recordings` with `0700`/`0600`
  permissions. That directory is runtime storage: files do not survive logout
  or reboot, so move or upload finished artifacts promptly.
- Recordings stop automatically at `max_duration_seconds` (default 60 for
  `webm`, 15 for `gif`) or when the intermediate exceeds 1 GiB.
- If finalization fails, the intermediate `.mkv` and a `.log` with the tool's
  stderr are kept and reported so the material is recoverable.

## Diagnostics

```bash
computer-use-sway --doctor
```

The server reconstructs `XDG_RUNTIME_DIR`, `SWAYSOCK`, and `WAYLAND_DISPLAY` from `/run/user/<uid>` when an MCP host launches it with a sanitized environment.

## Security

This server gives an MCP client practical control over your active desktop session. Only register it with local clients you trust. It intentionally has no network listener; the transport is stdio.

Recording captures whatever is visible on the recorded output, including the pointer cursor, until it is stopped. Recordings are written only to the server's private runtime directory, and their paths are returned to the MCP client; treat any active recording as visible desktop observation.

## Development

```bash
make check
```

`make check` runs import/protocol tests and Python bytecode compilation. It does not require an active Sway session.

Build local distribution artifacts:

```bash
python3 -m pip install ".[publish]"
make build
```

## License

`computer-use-sway` is released under the MIT License. See [LICENSE](LICENSE).

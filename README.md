# computer-use-sway

<p align="center">
  <img src="assets/mascot.png" alt="computer-use-sway mascot" width="260">
</p>

`computer-use-sway` is a local MCP stdio server that lets an MCP client inspect and operate the current Sway desktop session.

It exposes screen, window, pointer, keyboard, and clipboard tools through Sway-native commands. It is designed for Linux desktops running Sway on Wayland.

## Capabilities

- Inspect active outputs, seats, focused windows, and required binaries.
- Capture screenshots as MCP image content or data URLs.
- Return a simplified Sway window tree.
- Focus windows by container ID, app ID, class, or title.
- Move the pointer, click, drag, and scroll.
- Type text and send key chords through `wtype`.
- Read and write text clipboard contents through `wl-clipboard`.

## Requirements

Runtime commands:

- `swaymsg`
- `grim`
- `wtype`
- `wl-copy`
- `wl-paste`

On openSUSE:

```bash
sudo zypper in python3 sway grim wtype wl-clipboard
```

On Debian or Ubuntu:

```bash
sudo apt install python3 sway grim wtype wl-clipboard
```

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

## Diagnostics

```bash
computer-use-sway --doctor
```

The server reconstructs `XDG_RUNTIME_DIR`, `SWAYSOCK`, and `WAYLAND_DISPLAY` from `/run/user/<uid>` when an MCP host launches it with a sanitized environment.

## Security

This server gives an MCP client practical control over your active desktop session. Only register it with local clients you trust. It intentionally has no network listener; the transport is stdio.

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

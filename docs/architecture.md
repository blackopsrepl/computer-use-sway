# Architecture

`computer-use-sway` is a single-process MCP stdio server with no background daemon and no persistent state.

## Boundaries

- MCP transport: JSON-RPC messages over stdin/stdout.
- Desktop backend: Sway IPC through `swaymsg`.
- Screenshots: `grim` returns PNG bytes directly to MCP image content.
- Input: pointer actions use Sway seat cursor commands; text and key events use `wtype`.
- Clipboard: text-only set/get through `wl-copy` and `wl-paste`.

## Environment Recovery

MCP hosts often launch stdio servers with a stripped environment. Before talking to Sway, the server reconstructs:

- `XDG_RUNTIME_DIR` as `/run/user/<uid>` when available.
- `SWAYSOCK` from the newest Sway IPC socket in that runtime dir.
- `WAYLAND_DISPLAY` from the newest Wayland socket in that runtime dir.

This lets a fresh MCP host reach the current desktop session without shell-specific launch wrappers.

## Tool Error Model

Expected desktop and validation failures are reported as MCP tool results with `isError: true`. Unexpected Python exceptions are logged to stderr and returned as a generic internal tool error.

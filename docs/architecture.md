# Architecture

`computer-use-sway` is a single-process MCP stdio server, standard library only.
It talks to Sway over the IPC socket, to Wayland over its own wire client, and
to AT-SPI over its own D-Bus client; the only external programs it runs are
`grim`, `tesseract` and `wl-clipboard`. The measured facts behind every design
choice are in `docs/plan-1.0/FINDINGS.md`; the full design is
`docs/plan-1.0/ARCHITECTURE.md`.

## Layers

| layer | modules | role |
|---|---|---|
| transports | `swayipc`, `wire`, `keymap`, `vinput`, `dbus` | Sway IPC with an event subscription; a Wayland client for virtual pointer/keyboard and the idle notifier; a D-Bus client for AT-SPI |
| desktop | `core`, `desktop`, `geometry`, `capture` | errors, environment recovery, the Sway tree, coordinates, grim captures and change detection |
| agent | `install`, `agent`, `placement`, `sentinel`, `session`, `sandbox`, `reaper` | the config-declared agent seat, its output and workspace, window placement, the user's focus/idle sentinel, modes, the test sandbox |
| perception | `atspi`, `toolkits`, `ocr`, `query`, `refs`, `scene` | the accessibility tree, measured toolkit profiles, the OCR ensemble, queries, refs and their validation, the rendered scene |
| action | `actions`, `actions_delivery`, `verify`, `launch`, `waits`, `plans` | the delivery ladder, verdicts, launching, waits and multi-step runs |
| surface | `tools*`, `specs*`, `server`, `cli`, `timeline`, `version` | MCP tools and schemas, the protocol loop, the CLI, the timeline stream |

Dependencies point down the table only. Every file stays under 500 lines
(`scripts/check_file_length.py`).

## Lifecycle

1. The first call that needs the desktop starts the session in `seat` mode
   (or the mode `session start` asked for). The agent pins the user's outputs,
   creates the headless output at x=100000, renames its workspace to
   `cus-agent`, parks the agent seat's cursor on that output, and only then
   creates virtual devices on the config-declared seat (FINDINGS I8).
2. `observe` walks the AT-SPI tree of each window in scope, maps extents to
   layout points, adds OCR words where the tree is thin, and numbers everything
   as refs of one snapshot.
3. `act` resolves the target (ref, query or point), validates the ref, climbs
   the delivery ladder, and returns the verdict computed from readback,
   AT-SPI events, Sway events and pixels.
4. `launch` writes the launcher script, fires the agent's launcher binding,
   registers the reported child pid for placement, and waits for the window.
5. A sway `reload` marks the agent stale, and so does finding its devices off
   the agent seat, which every input message checks first (a runtime `seat`
   command moves them to seat0 without an event, FINDINGS S12). The next call
   re-pins the layout, parks the cursor, recreates the devices and re-adds
   placement criteria.
6. On exit the devices are destroyed first, then the agent output is
   unplugged, so the agent's cursor never lands on a user output, and
   nothing of the agent stays on the desktop. A sandbox removes only its own
   root.

## Threads

The main thread runs the MCP loop and owns the Wayland connection and the
AT-SPI client. One reader thread follows the Sway event subscription and feeds
the event log; its listeners (placement) issue IPC commands only. Nothing else
runs concurrently.

## Environment Recovery

MCP hosts often launch stdio servers with a stripped environment. Before talking
to Sway the server reconstructs `XDG_RUNTIME_DIR` (`/run/user/<uid>`),
`SWAYSOCK` and `WAYLAND_DISPLAY` (the newest sockets there) and the session bus
address.

## Recording, Narration, And The Stream

Screen recording, the take timeline, speech synthesis, captions and muxing live
in a separate MCP server, **`seshat`** (<https://github.com/blackopsrepl/seshat>).
This server publishes every call it dispatches to that server's timeline stream
(`$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl`) with curated
payloads; the verdict of an `act` travels inside `payload`.

## Tool Error Model

Expected desktop and validation failures are MCP tool results with
`isError: true`, a message and a code. Unexpected Python exceptions are logged
to stderr and returned as a generic internal tool error.

# WIREFRAME.md

This document is the current-state contract for the shipped `computer-use-sway`
MCP server: its tool surface, its published timeline stream, and its runtime
files. It describes what exists now, not a roadmap.

## Scope

`computer-use-sway` is a single-process MCP stdio server that inspects and operates
the current Sway session. It is not a recorder: recording and narration live in a
separate server, `seshat` (<https://github.com/blackopsrepl/seshat>).

It does:

- expose screen, window, pointer, keyboard and clipboard tools over MCP stdio
- recover the session environment (`XDG_RUNTIME_DIR`, `SWAYSOCK`, `WAYLAND_DISPLAY`)
  when an MCP host launches it with a stripped environment
- publish every dispatched action to the take timeline stream a recorder reads

It does not:

- record, encode, narrate or caption anything
- keep state between sessions, or own a child process beyond a single command
- embed or call an LLM, or generate narration prose
- open a network listener

## Protocol Surface

MCP protocol version `2024-11-05`, stdio transport, no notification or resource
surface. `initialize` returns the server name, version and operating instructions;
`tools/list` returns the twelve tool schemas; `tools/call` dispatches, and every
call — success or failure — is published to the timeline stream.

Tool errors are returned as MCP tool results with `isError: true` and a specific
message, never as transport errors. Unexpected exceptions are logged to stderr and
reported as a generic internal error.

## Tool Catalog

Twelve tools: five observation, seven input.

| tool | arguments | returns |
|---|---|---|
| `screen_info` | none | outputs, seat, focused window, binaries, bounds |
| `screenshot` | `include_cursor`, `output` (`image`\|`data_url`\|`both`), `save_path`, `region` | PNG as MCP image content, a data URL, or text-only metadata when `save_path` is set |
| `window_tree` | `include_scratchpad`, `max_depth` | simplified Sway window tree |
| `focus_window` | exactly one of `con_id`, `app_id`, `class`, `title`; `match` (`exact`\|`contains`\|`regex`) | focused window before, selected, and after |
| `move_pointer` | `x`, `y`, `mode` (`set`\|`move`) | cursor position |
| `click` | optional `x`, `y`; `button`, `count`, `interval_ms` | click summary |
| `drag` | `from`, `to`; `button`, `steps`, `duration_ms` | drag summary |
| `scroll` | optional `x`, `y`; `direction`, `clicks` | scroll summary |
| `type_text` | `text`, `delay_ms` | characters typed |
| `key` | `key`, `modifiers` | key sent |
| `clipboard_set` | `text` | bytes set |
| `clipboard_get` | `max_bytes` | clipboard text |

`screenshot` is the only tool that returns image content; `save_path` exists for
hosts whose per-request image budget makes repeated screenshots unusable.

## Timeline Publication

This server does not record. It publishes what it dispatches, so that a recorder
in a separate process can anchor narration to it:

```
$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl
{"at_monotonic": 12345.678, "tool": "click", "ok": true,
 "payload": {"x": 640, "y": 360}, "source": "computer-use-sway"}
```

- `at_monotonic` is CLOCK_MONOTONIC seconds from `time.monotonic()`. The clock is
  host-wide, so these timestamps are directly comparable with a recorder's own
  epoch; no handshake or session id is involved.
- `tool` is the dispatched tool name. `ok` reports whether the call succeeded —
  a published event records a **dispatch**, never a verified visible effect.
- `payload` is curated here, by the server that knows what its arguments mean:
  typed text becomes a character count and clipboard content a byte count, and
  neither value ever reaches the file.
- The stream is truncated once at server startup and appended to while it runs.
  Publishing is a side channel: a missing runtime directory, an unwritable path
  or any write error is swallowed, because narration must never be able to break
  an action.
- Only the twelve action and observation tools are published; `seshat`'s own
  recording tools are not this server's business.

The recording lifecycle, narration contract, artifact contract and fallback
anchors live in `seshat`'s WIREFRAME.md.

## Runtime Files

This server writes one file: the timeline stream at
`$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl` (directory `0700`, file
`0600`), truncated at startup and appended to while the server runs. Screenshots
written with `save_path` go to the caller's path at mode `0600`.

Recordings, timeline sidecars, narration work directories and caption files belong
to `seshat`.

## Diagnostics And CLI

```bash
computer-use-sway --self-test              # environment, binaries, outputs, seat, focus
computer-use-sway --doctor                 # full JSON report, including the stream path
computer-use-sway --install-codex-mcp      # register with Codex
computer-use-sway --uninstall-codex-mcp    # remove the registration
```

The dependency check covers what this server uses — `python3`, `swaymsg`, `grim`,
`wtype`, `wl-copy`, `wl-paste`, plus `codex` for the registration helpers — and
reports nothing about the recording stack, which it does not touch.

## Error Model

Expected desktop, validation, and missing-capability failures are MCP tool
results with `isError: true` and a clear text message. Unexpected Python
exceptions are logged to stderr and returned as a generic internal tool error.

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, and workflows.
- `WIREFRAME.md`: this shipped tool and runtime contract.
- `docs/architecture.md`: module layout, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `AGENTS.md`: repository rules and validation.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.

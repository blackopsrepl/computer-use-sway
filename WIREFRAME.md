# WIREFRAME.md

This document is the current-state contract for the shipped `computer-use-sway`
MCP server: its tool surface, modes, agent seat, verdicts, published timeline
stream and runtime files. It describes what exists now, not a roadmap. The
evidence for every behaviour below is in `docs/plan-1.0/FINDINGS.md`.

## Scope

`computer-use-sway` is a single-process MCP stdio server that lets an agent work
on the current Sway session beside the user. It is not a recorder: recording and
narration live in a separate server, `seshat` (<https://github.com/blackopsrepl/seshat>).

It does:

- give the agent its own seat (pointer, keyboard focus, clipboard), its own
  headless output and its own workspace `cus-agent`, in the user's live session
- ground targets on structure first: the AT-SPI accessibility tree, then OCR,
  pixels last
- deliver each action up a ladder (semantic → agent seat → idle-gated user seat)
  and return an effect verdict for it
- recover the session environment (`XDG_RUNTIME_DIR`, `SWAYSOCK`,
  `WAYLAND_DISPLAY`, the session bus) when an MCP host strips it
- publish every dispatched call to the take timeline stream a recorder reads

It does not:

- record, encode, narrate or caption anything
- embed or call an LLM, or generate narration prose
- open a network listener
- paste for text entry (GTK4 and Qt paste the user's clipboard, FINDINGS C2)

## Protocol Surface

MCP protocol version `2024-11-05`, stdio transport. `initialize` returns the
server name, version and operating instructions; `tools/list` returns 19 tool
schemas with MCP annotations (`readOnlyHint`, `destructiveHint: false`,
`openWorldHint: false`); `tools/call` dispatches, and every call, success or
failure, is published to the timeline stream.

Tool errors are MCP tool results with `isError: true`, a message and, where it
helps, a machine code (`no_match`, `stale_ref`, `unknown_ref`, `offscreen`,
`popup_no_pointer`, `a11y_stalled`, `agent_seat_lost`, …). Unexpected exceptions
are logged to stderr and reported as a generic internal error.

## Tool Catalog

19 tools: eight semantic, eleven primitive.

| tool | arguments | returns |
|---|---|---|
| `session` | `action` (`status`\|`start`\|`stop`), `mode`, `options` | mode, options, agent output, devices, bus status |
| `screen_info` | none | outputs (agent output marked), seats, focus, binaries |
| `observe` | `scope` (`agent`\|`focused`\|`visible`\|`window`), `window`, `query`, `max_elements`, `ocr`, `image` | a numbered scene: one line per element with a ref (`s7:3`), role, name, states, layout box, actions, value/text, `(ocr)` marker |
| `read` | `ref`, `start`, `length` | the element's full text or value |
| `act` | `target` (`{ref}`, `{query}` or `{x, y}`), `action`, `text`, `key`, `modifiers`, `value`, `direction`, `clicks`, `to`, `delivery`, `verify` | `delivery`, `effect`, `evidence`, `escalations`, `disturbed_user`, target |
| `run` | `steps` (`act`, `wait_for`, `assert`, `launch`, `observe`, `key`, `type_text`, `focus_window`), `stop_on`, `timeout_ms`, `observe_after` | per-step results, `ok`, `failed_step`, optional final scene |
| `wait_for` | `condition` (`element`, `window`, `text`, `pixels_stable`, `user_idle_ms`), `timeout_ms` | `satisfied`, `waited_ms`, detail |
| `launch` | `argv`, `cwd`, `env`, `timeout_ms`, `a11y` | the new window (on `cus-agent`), profile, flags added |
| `screenshot` | `window`, `target_output`, `region`, `include_cursor`, `output`, `save_path` | PNG of the user's screen by default; image content, data URL, or a `0600` file |
| `window_tree` | `include_scratchpad`, `max_depth` | simplified Sway tree |
| `focus_window` | selector, `seat`, `move_to_agent` | focus before and after |
| `move_pointer`, `click`, `drag`, `scroll` | coordinates, `seat`, `verify`, gesture arguments | summary and, with `verify`, an effect |
| `type_text`, `key` | text or key + modifiers, `seat`, `verify` | characters or key sent, effect |
| `clipboard_set`, `clipboard_get` | text or `max_bytes`, `seat` | bytes set, or the text |

`act` actions: `press`, `activate`, `toggle`, `focus`, `set_text`, `type`, `key`,
`click`, `double_click`, `right_click`, `select`, `set_value`, `scroll`,
`drag_to`.

`screenshot` is the only tool that returns an image unless `observe` is asked
for one (`image`). With no `region`, `window` or `target_output` it captures the
bounding box of the user's outputs; the agent output 100000 px away is captured
only with `target_output: "agent"`.

## Modes

| mode | agent input goes to | use |
|---|---|---|
| `seat` (default) | the config-declared seat `cus-agent`, its headless output and workspace | normal work beside the user |
| `user` | seat0, the user's own pointer and keyboard | driving the visible screen, e.g. for a `seshat` recording |
| `sandbox` | a private nested sway, D-Bus and AT-SPI bus under `$XDG_RUNTIME_DIR/computer-use-sway/sandbox-<pid>-<n>` | tests and the benchmark; never touches the user's session |

Options (`session start`): `foreground` (`idle`\|`always`\|`never`, default
`idle`), `idle_ms` (1500), `idle_max_wait_ms` (10000), `foreground_pointer`
(false), `settle_ms` (600), `ocr` (`auto`\|`always`\|`never`).

## Agent Seat

- The seat is declared once in `~/.config/sway/config.d/computer-use-sway.conf`
  by `computer-use-sway --install-sway-config`; it is never created at runtime
  (a runtime seat dies on `reload`, FINDINGS S7).
- Virtual pointer and keyboard are bound to that `wl_seat` over a stdlib
  Wayland client. GTK3, GTK4, Qt6, Firefox, foot, Alacritty and Ghostty accept
  them; Chromium, Electron and kitty do not (FINDINGS I5, I7) and are reached
  semantically or through the foreground step.
- The agent seat has devices only while the agent output exists. Start
  creates the output, parks the seat's cursor on it, then creates the
  devices; stop destroys the devices, then unplugs the output; recovery after
  a reload parks the cursor before recreating them. A seat with a pointer
  draws its own cursor, so the agent's cursor is never on the user's screen,
  and an idle server leaves nothing on the desktop (FINDINGS I8).
- The agent output is a headless output at x=100000; the user's outputs are
  pinned at their positions before it is created (FINDINGS O2).
- Every agent-seat binding is followed by IPC `nop` (FINDINGS O6).
- `launch` runs the app through the agent seat's launcher binding, keeping the
  activation token, under a subreaper (`reaper.py`), so its first window maps on
  `cus-agent` with seat0 focus unchanged (O11). The app's later windows
  (dialogs, second toplevels) land there too: the server adds sway `assign`
  criteria for the app's pid and app_id before they map, and moves any window
  of an agent-launched process that still escapes (O12).
- After a sway `reload` the server recreates the devices on the agent seat,
  re-pins outputs and the workspace, and re-adds placement criteria for live
  agent processes.

## Delivery and Verdicts

Each `act` climbs a ladder and stops at the first rung with an effect:

1. `semantic`: AT-SPI action, `SetTextContents`, `Selection.SelectChild`,
   `GrabFocus`, `SetCurrentValue`. GTK3 presses go to the pointer first,
   because GTK3 runs the handler inside `DoAction` and a modal dialog would
   block the tree (A12). Elements in popup menus have no pointer coordinates and
   are reached semantically only (A13).
2. `agent`: the agent seat's devices, for toolkits that accept a second seat.
3. `foreground`: seat0, only after the user has been idle for `idle_ms`
   (waiting up to `idle_max_wait_ms`); the user's focus is restored afterwards.

The effect, in order of evidence: `confirmed` (readback shows the intended
state), `changed` (AT-SPI or Sway events other than focus, or pixels changed
beyond caret noise), `no_change` (the window looked the same), `unverifiable`
(no evidence source). `no_change` and `unverifiable` mean not done. A text
readback that disagrees escalates even when events fired (Gecko,
FINDINGS A7).

## Timeline Publication

This server does not record. It publishes what it dispatches, so that a recorder
in a separate process can anchor narration to it:

```
$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl
{"at_monotonic": 12345.678, "tool": "act", "ok": true,
 "payload": {"action": "set_text", "delivery": "semantic", "effect": "confirmed",
             "characters": 5, "target": {"ref": "s3:2"}}, "source": "computer-use-sway"}
```

- `at_monotonic` is CLOCK_MONOTONIC seconds; the clock is host-wide.
- `ok` reports whether the call succeeded. A published event records a
  dispatch, never a verified visible effect; the verdict is inside `payload`.
- `payload` is curated here: typed and set text becomes `characters`,
  clipboard content becomes `bytes`, query strings become `name_chars` /
  `text_chars`, `launch` keeps only the program's basename, and the keys
  `text`, `value`, `content`, `clipboard`, `password`, `env` and `argv` are
  dropped at any depth.
- The stream is truncated once at server startup and appended to while it runs.
  Publishing is a side channel: any write error is swallowed.

## Runtime Files

All under `$XDG_RUNTIME_DIR` (directories `0700`, files `0600`) unless noted:

| path | what |
|---|---|
| `seshat/streams/computer-use-sway.jsonl` | the timeline stream |
| `computer-use-sway/session.json` | agent output name, saved user output positions, creation time |
| `computer-use-sway/launch/next.sh` | the launcher script the agent's binding runs; rewritten per launch, reset to a no-op after |
| `computer-use-sway/launch/child.pid` | the launched app's pid, written by the reaper for placement |
| `computer-use-sway/sandbox-<pid>-<n>/` | sandbox mode root; teardown removes only this explicit path |
| `~/.config/sway/config.d/computer-use-sway.conf` | written only by `--install-sway-config` (`0644`) |

Screenshots written with `save_path` go to the caller's path at mode `0600`.

## Diagnostics And CLI

```bash
computer-use-sway --install-sway-config     # one-time: declare the agent seat and bindings
computer-use-sway --uninstall-sway-config   # remove that file
computer-use-sway --self-test               # agent seat installed, Wayland globals, OCR, outputs, focus
computer-use-sway --doctor                  # full JSON report
computer-use-sway --install-codex-mcp       # register with Codex
computer-use-sway --uninstall-codex-mcp     # remove the registration
```

Required: `sway` (IPC socket) and `grim`. Optional: `tesseract` (OCR),
`wl-copy`/`wl-paste` (clipboard tools), at-spi2-core (structure; without it
`observe` falls back to OCR).

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, quickstart, measured claims.
- `WIREFRAME.md`: this shipped tool and runtime contract.
- `docs/architecture.md`: layers, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `AGENTS.md`: repository rules and validation.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.
- `docs/plan-1.0/FINDINGS.md`: the measured evidence.

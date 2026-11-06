# Shipped computer-use contract

`computer-use-sway` is a stdlib-only local MCP stdio server controlling the
**user's existing session directly**. No dual seat, agent output/workspace,
placement rules, focus restoration or raw-input routing remains.

## Permission and lifecycle

Agents must never invoke desktop tools without the human's explicit request.
A tool being available or a session already running is not consent. Enforce
human permission in MCP client policy as well: the server cannot authenticate
who authored a call.

MCP startup does not start desktop control. Explicit
`session(action=start, mode=user)` is required; `user` is the default.
Every other desktop call refuses `session_required` while stopped.
Status/stop are safe while stopped. `sandbox` starts a private headless Sway,
private runtime and buses for tests; it uses only its existing user seat too.
Legacy `seat` mode is rejected. Stop closes server devices/connections; normal
user apps remain. Failed startup unwinds its partial session.

## MCP surface

19 tools, strict published JSON schemas and annotations, protocol
`2024-11-05`, newline-delimited JSON-RPC over stdio. Stdout is protocol-only.

| Tool | Arguments / result |
|---|---|
| session | action status/start/stop; mode user/sandbox; options settle_ms, ocr; status/start/stop result |
| observe | scope focused (default), visible, window; selector/query/ocr/max_elements/image; numbered accessible/OCR scene |
| read | ref/start/length; full accessible text or value |
| act | target ref/query/point; action and payload; delivery auto/semantic/user; verification; evidence and effect |
| run | up to 50 act/wait_for/assert/launch/observe/key/type_text/focus_window steps, timeout, stop_on, observe_after; per-step results |
| wait_for | element/window/text/pixels_stable/user_idle_ms condition; timeout; satisfied/detail |
| launch | argv/cwd/env/timeout_ms/a11y; normal user-session subprocess launch, new window and added accessibility flags |
| screen_info | actual outputs/layout, user seat, mode, focus and binaries |
| screenshot | region/window/real target_output name; cursor/output/save_path; PNG or private file metadata |
| window_tree | include_scratchpad/max_depth; simplified tree |
| focus_window | selector, optional seat user; direct focus change |
| move_pointer/click/drag/scroll | gesture, optional seat user and verification; direct user pointer input |
| type_text/key | text or key/modifiers, optional seat user and verification; direct user keyboard input |
| clipboard_get/clipboard_set | max_bytes or text, optional seat user; direct user clipboard |

Actions: press, activate, toggle, focus, set_text, type, key, click,
double_click, right_click, scroll, drag_to, set_value, select.
`agent` scope/seat/delivery, `foreground` delivery, `move_to_agent`, old idle
routing options and the Sway config installer flags are rejected, not aliases.
Screenshots capture the real layout without output exclusion heuristics.

## Delivery and effects

Semantic accessibility first, then user-session devices. GTK3 handlers that
block on modal dialogs prefer pointer delivery; popup menu items are semantic
only. No idle gate or focus restoration applies. Input may move the human's
pointer, keyboard focus and workspace. Launch inherits normal Sway/application
placement and strips stale activation tokens; no private launch scripts,
bindings, subreaper, assignments, or window relocation.

`confirmed` means readback matches; `changed` means observable change;
`no_change`/`unverifiable` do not prove success. Each act returns evidence and
escalations. Publishing a call never establishes its visible effect.

## Timeline and recording boundary

Recording/narration/captions/muxing live only in the separate seshat server.
Every dispatched tools/call (success or error) retains the publication hook:

```
$XDG_RUNTIME_DIR/seshat/streams/computer-use-sway.jsonl
```

Events include monotonic time, tool, ok, curated payload and source.
Typed/set text becomes character counts, clipboard content byte counts,
queries lengths, launch only program basename; sensitive values/argv/env are
dropped recursively. Stream truncation is once at server startup. Publication
errors never fail tool calls. Batches publish dispatched substeps too.

## Files and CLI

Private sandbox files live under
`$XDG_RUNTIME_DIR/computer-use-sway/sandbox-<pid>-<n>/` and are removed only by
explicit owned-root cleanup. Timeline directories/files are 0700/0600.
Screenshots use caller-supplied save_path at 0600. No session.json, launch
scripts/pidfiles, Sway config fragments or private bindings are produced.

CLI: --self-test, --doctor, --install-codex-mcp, --uninstall-codex-mcp.
Diagnostics read the desktop only when explicitly requested. Registration is
not permission. No automatic config install/uninstall/reload exists; legacy
configuration cleanup is a separate human-approved operation.

## Development

`dependencies = []`; files stay below 500 lines. `make check`,
`make integration`, `make bench`, and `make release-gate` are the gates.
All compositor/toolkit tests and benchmarks use private sandboxes, never live
input. Historical plans and dated benchmark results describe the removed
architecture; consult README, docs/architecture.md, AGENTS.md and the shipped
skill for current behavior.

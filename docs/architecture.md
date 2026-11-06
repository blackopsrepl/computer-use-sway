# Architecture

The current design is explicit direct user-session control. The original
`plan-1.0/` is historical: its dual-seat architecture has been removed.

## Layers

| Layer | Modules | Responsibility |
|---|---|---|
| Transport | swayipc, wire, keymap, vinput, dbus | stdlib IPC, Wayland devices on the existing user seat, accessibility bus |
| Desktop | core, desktop, geometry, capture | errors, environment recovery, actual layout/tree, captures |
| Lifecycle | session, idle, install, sandbox | explicit start/stop; idle observation only; runtime path; private test compositor |
| Perception | atspi, toolkits, ocr, query, refs, scene | structure, toolkit facts, OCR, references |
| Actions | actions, actions_delivery, verify, launch, waits, plans | semantic/direct user input, effect verdicts, direct subprocess launch, waits, batches |
| Surface | tools*, specs*, validation, server, cli, timeline, version | strict argument checks, stdio tools, consent guidance, curated dispatch stream |

No agent seat, output ownership, output pinning, workspace placement, private
binding keys, focus restoration, or alternate raw-input delivery route exists.
`install.py` only resolves the private runtime directory; it installs no config.
Python runtime dependencies remain empty.

## Lifecycle and permission boundary

1. Starting the MCP server registers tools, but never opens a desktop session.
   Client policy must require the human's explicit request before invoking any
   desktop tool. The server cannot independently prove that human provenance.
2. `session(action=start)` defaults to `user`. Only `user` and private-test
   `sandbox` are accepted. Other desktop calls refuse with `session_required`.
   Status and stop do not connect to the desktop. A failed start cleans up.
3. Start discovers the existing user seat and subscribes to Sway events. Devices
   are created lazily on that same seat. No seats/outputs/workspaces are created
   in user mode, and no desktop layout/assignment configuration is installed.
4. `observe` defaults to focused (or selected window); visible is also supported.
   `act` resolves targets and uses semantic accessibility or direct user devices.
   GTK3 blocking modal handlers prefer direct pointer delivery; popup items use
   semantic actions. Input can change the human's pointer, focus and workspace;
   no idle policy or focus restoration claims apply.
5. `launch` starts an application inheriting this session's environment with
   stale activation tokens stripped. There is no assignment or relocation of
   its windows; normal Sway/application placement applies.
6. Stop destroys virtual devices, closes accessibility/Wayland/subscription
   connections, and leaves human apps/windows/layout alone. Sandbox stop kills
   its own process group and removes only its own explicit runtime root.

The MCP loop owns the Wayland/a11y clients. One subscription reader feeds the
Sway event log. `idle.py` observes user inactivity for `wait_for` only, never as
permission or a delivery gate.

## Protocol and timeline

Argument validation follows published schemas before tool dispatch. `run` steps
use the same checked entry point and publish their results. Every dispatched
`tools/call`, success or error, retains the timeline hook. Curation reduces typed
text to characters and clipboard content to bytes; recording remains in seshat.
Publishing errors never break a desktop call. Expected failures return tool
errors; unexpected failures are logged on stderr with a generic client message.

## Verification

Unit checks, the real stdio server, toolkit integration and benchmark operate
only in private sandbox compositors and buses. Regression tests check explicit
start, rejected legacy routes, actual direct pointer/keyboard input, and stable
seat/output/workspace identities. Historical benchmarks are not evidence of
current behavior. Every source file must remain below 500 lines.

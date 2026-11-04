# 1.0 Architecture — the target design

This is the contract the file-by-file plan implements. Every design decision
here is backed by a row in `FINDINGS.md` (cited as `[S4]`, `[O6]`, …). Where a
choice had alternatives, the highest-standard one was taken and the rejected
one is named, so an implementer never re-opens it.

## 1. What 1.0 is

A Sway-native computer-use MCP server that:

1. **Works beside the user, not instead of them.** It has its own Sway seat
   (`cus-agent`: own pointer, keyboard, focus, clipboard) and its own invisible
   output and workspace (`cus-agent`) [S1–S5, O1–O7]. The user keeps typing in
   their window while the agent works in another one.
2. **Grounds on structure, not pixels.** It targets elements by role and name
   from the accessibility tree [A1–A4], falls back to an OCR word map [P2], and
   uses raw pixels only when the caller explicitly asks for them.
3. **Acts at the most precise level available.** The order is: semantic
   accessibility action (no device, no focus) [A6–A7], then agent-seat device
   input [I2–I5], then a short, idle-gated foreground step on seat0 with focus
   restored [F1, S11].
4. **Reports what happened, not what was sent.** Every input returns a
   verdict (`confirmed | changed | no_change | unverifiable`) with its evidence
   [A11, A7]. The verdict is also published to the seshat timeline.
5. **Batches.** `run` executes a short plan server-side, with event-driven
   waits. One model turn covers what other harnesses spend 5–15 turns on.

It stays stdlib-only (`dependencies = []`), never embeds an LLM, and remains
desktop control only (recording stays in seshat).

## 2. Layers and modules

```
server.py ── MCP loop, dispatch, timeline publish (Result.meta → payload)
cli.py ───── --self-test --doctor --install-sway-config … (split out of server.py)
tools_*.py / specs_*.py ── tool wrappers + JSON schemas (split by family)
────────────────────────── semantic layer
session.py   Session: mode, options, lazy subsystems, teardown
plans.py     run: multi-step plans
waits.py     wait_for: event-driven conditions
actions.py   act: target resolution + delivery ladder
verify.py    baselines, evidence, verdicts
launch.py    launch apps onto the agent workspace
scene.py     observe: windows + elements → snapshot text
refs.py      snapshot/ref store and validation
query.py     role/name/text matching
geometry.py  a11y/ocr coordinates → layout points
toolkits.py  per-toolkit capability profiles (data from FINDINGS)
────────────────────────── platform layer
agent.py     agent seat + output lifecycle, focus bindings, reload recovery
sentinel.py  seat0 baseline/restore, per-seat idle (ext_idle_notifier_v1)
sandbox.py   private nested Sway + D-Bus + AT-SPI (sandbox mode, tests)
install.py   sway config.d snippet install/verify
capture.py   grim: PNG, PPM@2x, region hash
ocr.py       tesseract ensemble
atspi.py     AT-SPI client
dbus.py      stdlib D-Bus client
vinput.py    virtual pointer/keyboard per seat
keymap.py    xkb keymap + keysym tables
wire.py      stdlib Wayland wire client
swayipc.py   stdlib Sway IPC client + event subscription thread
desktop.py   Sway tree helpers (now on swayipc)
core.py      ToolError, Result, run_command, strict parsing, session env
timeline.py  seshat stream publication
```

**Removed:** `pointer.py` (`/dev/uinput`; replaced by `zwlr_virtual_pointer_v1` [I2]),
the `wtype` dependency (replaced by `vinput.VirtualKeyboard` [I3, I4]), and
the `swaymsg` dependency (replaced by `swayipc` over `$SWAYSOCK`).

**Binaries after 1.0:** `grim` (required for screenshots, OCR, pixel
verification), `tesseract` (optional: OCR fallback), `wl-copy`/`wl-paste`
(optional: clipboard tools), at-spi2-core on the system (optional: the
accessibility layer). A missing optional binary disables only its feature,
with a `ToolError` that names the package.

## 3. Modes

| mode | where actions land | who it is for |
|---|---|---|
| `seat` (default) | agent seat `cus-agent` on the live session; agent apps on output `cus-agent` (headless, at x=100000, invisible) | everyday co-working |
| `user` | seat0, the user's own pointer and keyboard (legacy 0.3 behaviour, now with semantic actions + verdicts) | narrated demos recorded by seshat on the visible output; explicit "drive my screen" |
| `sandbox` | a private nested headless Sway with its own D-Bus and AT-SPI | risky runs, CI, benchmarks |

The mode is chosen with the `session` tool. It is lazily started as `seat`
on the first tool call that needs it. In `seat` mode the agent may still
operate windows on the user's visible workspaces: it uses its own seat, so the
user's focus does not move [S3]. `launch` always places new apps on the agent
workspace.

## 4. The agent seat, precisely

- **Declared in config, never created at runtime.** A runtime seat is
  destroyed by `reload`, its devices then type into the user's window [S7], and
  pre-existing windows never accept the re-created seat. `install.py` writes
  `~/.config/sway/config.d/computer-use-sway.conf`:

  ```
  # computer-use-sway: agent seat (do not edit; managed by computer-use-sway --install-sway-config)
  seat cus-agent fallback false
  bindsym --no-repeat XF86Macro27 [con_mark="_cus_focus"] focus
  bindsym --no-repeat XF86Macro29 exec /run/user/<uid>/computer-use-sway/launch/next.sh
  ```

  There is deliberately **no** `workspace cus-agent` binding: sway donates
  names from `workspace <name>` bindings to new workspaces on any output, and
  the user's screen would receive it [O9].

  The exec path is absolute (written at install time from `$XDG_RUNTIME_DIR`),
  so sway's `$variable` expansion never touches it. `launch` rewrites
  `next.sh` (0700) before each tap. No binding is ever added at runtime, so
  every binding survives `reload`. It then runs `reload` once and verifies `get_seats` lists `cus-agent`.
  `session start` in `seat` mode refuses with a `ToolError` naming
  `computer-use-sway --install-sway-config` if the seat is missing. There is no
  runtime fallback.
- **Devices** are one `zwlr_virtual_pointer_v1` and one `zwp_virtual_keyboard_v1`
  created on the `cus-agent` wl_seat [S2]. Seat0 gets its own pair, created
  lazily and used only for `user` mode and foreground steps.
- **Focus** for the agent seat only: mark the target `[con_id=N] mark --add
  _cus_focus`, tap `XF86Macro27` on the agent keyboard, then IPC `nop` [S5, O6].
  The binding keysyms are unique because no physical key produces them [S6].
- **Every agent binding is followed by IPC `nop`.** This resets sway's handler
  seat to seat0, so a view mapping later never focuses all seats [O6].
- **Reload:** the `swayipc` subscription thread sees `workspace/reload` [S9] and
  sets `agent.stale`. Every `vinput` send checks the flag first and raises
  `AgentSeatLost`. Recovery runs before the next input and is idempotent:
  1. destroy the agent devices
  2. recreate them on the same `cus-agent` wl_seat [S8]
  3. re-pin the saved user output positions
  4. move the agent output back to x=100000
  5. re-pin `workspace cus-agent output <agent_out>`
  6. verify with `get_seats` that `cus-agent` holds exactly our two devices and
     seat0 holds none of them [O8]
- **Agent output:**
  1. Pin every active user output at its current position [O2].
  2. Run `create_output`.
  3. Diff the outputs to find its name.
  4. Set `output <n> position 100000 0 resolution 1920x1080`.
  5. Set `workspace cus-agent output <n>`.
  6. `rename workspace "<auto-name>" to cus-agent` [O9].

  To put the agent seat on that workspace: if it has a window, mark-focus that
  window; if it is empty, click its background with the agent pointer, then
  send `nop` [O10].

  The name and the user pins are stored in
  `$XDG_RUNTIME_DIR/computer-use-sway/session.json`, so a restarted server
  re-adopts an output that still exists instead of creating another.
  `session stop`, normal exit and SIGTERM run `output <n> unplug` [O3].
- **Seat0 sentinel.** Before every agent step, record seat0's focused con_id;
  after it, compare. If seat0 moved, restore it with IPC `[con_id=prev] focus`
  and set `disturbed_user: true` in the verdict.

## 5. Perception

**Snapshot** (`observe`): a numbered snapshot `sN` holding, per window in scope:

- the Sway node: con_id, app_id, title, pid, rect, workspace, output, and whether it is on the agent output
- the toolkit profile
- the elements

An element is a1 (from AT-SPI) or o1 (from OCR):

- `ref` (`sN:k`), `role` (normalized), `name`, a state subset, actions
- `bounds` in layout points
- `value`/`text` preview (≤ 80 chars)
- `source`

Which elements are kept: showing, with non-zero bounds, and (actionable, or
editable, or text-bearing, or focused, or a landmark: window, dialog, menu,
tab list), up to `max_elements` (default 150) per window, in reading order (y, then x).

**Rendering** is compact text, one element per line. An image is attached
only with `image: true`.

**AT-SPI → layout** [A4]:

```
layout = sway.rect.xy + sway.window_rect.xy
       - frame_extents.xy - csd_origin
       + element_extents (÷ output.scale when toolkit is Chromium/Electron)
```

`csd_origin` is (0,0) unless the frame extents' size differs from
`window_rect`'s size. In that case it is the min x/y of the frame's direct
children's boxes (ignoring boxes at -2³¹ or of size ≤ 1). Coordinates always
come from `WINDOW` type, never `SCREEN` [A5].

**OCR** [P1, P2] runs when a window has fewer than 3 kept a11y elements, when
the caller asks for it, or when a text query finds no a11y match. Steps:

1. Capture the window rect with `grim -t ppm -s 2`.
2. Run 4 variants in parallel.
3. Union the words and dedupe them.
4. Group words into lines by baseline.
5. Map image px ÷ 2 + window origin → layout.

**Refs stay valid across snapshots** for a11y elements while the (bus, path)
object still answers with the same role and name and stays `showing`. OCR refs
are valid only while the window's region hash is unchanged. A stale ref is a
`ToolError("stale_ref: …; call observe again")`, never a guess.

**Electron/Chromium** with no tree (A9): `launch` adds
`--force-renderer-accessibility` automatically. For already-running ones,
`observe` marks the window `a11y: off (relaunch with
--force-renderer-accessibility)` and uses OCR.

## 6. Action: the delivery ladder

`act(target, action, …)`. The target is one of:

- `{ref}`
- `{query: {role?, name?, text?, match?, window?, nth?}}`, resolved freshly
- `{x, y}`, a layout point

Delivery `auto` walks the rungs below and stops at the first verdict of
`confirmed` or `changed`:

| rung | when it applies | how |
|---|---|---|
| 1 semantic | a11y element; action has a semantic form | `press/activate/toggle` → `DoAction(<named index>)`; `focus` → `GrabFocus`; `set_text` → `SetTextContents` + readback (Gecko lies [A7]); `set_value` → `Value.CurrentValue` |
| 2 agent device | toolkit profile `multi_seat != False` [I5] and not `user` mode | agent focus binding on the window, then pointer click at the element center / keyboard type |
| 3 foreground | rungs 1–2 unavailable or `no_change`, and policy allows | wait for seat0 idle ≥ `idle_ms` [S11], then IPC focus → `GrabFocus` → seat0 keyboard → IPC focus restore [F1]. Pointer use on seat0 only with `foreground_pointer: true` (default false: the pointer position cannot be restored) |

`delivery` may pin a rung (`semantic | agent | foreground`). In `user` mode,
rung 2 uses seat0 directly and rung 3 does not exist.

A toolkit whose profile is unknown is tried on rung 2. A `no_change` there
records `multi_seat=False` for that app_id for the rest of the session.

## 7. Verification

Before an input, `verify.Baseline` takes:

- the target window's title
- seat focus for both seats
- for a11y targets: the target's text, value and states
- a region hash of the target window, for device and foreground rungs

After the input it gathers evidence for up to `settle_ms` (default 600 ms;
it stops early once pixels are stable twice, 60 ms apart):

1. **readback**: text equals the expected value, a state flipped as expected,
   focus arrived → `confirmed`
2. **AT-SPI signals** from the target app since the action → `changed`
3. **Sway events** for the target con (title/new/close/focus/urgent) → `changed`
4. **region hash** differs → `changed`
5. none of the above, but the window was capturable → `no_change`
6. no channel at all (off-output and no a11y) → `unverifiable`

The verdict always carries `delivery`, `escalations`, `evidence` (a list of
short strings), `disturbed_user` and `elapsed_ms`.

## 8. Tool surface (19 tools)

New semantic tools: `session`, `observe`, `read`, `act`, `wait_for`, `run`,
`launch`. Kept (with additions): `screen_info`, `screenshot`, `window_tree`,
`focus_window`, `move_pointer`, `click`, `drag`, `scroll`, `type_text`, `key`,
`clipboard_set`, `clipboard_get`.

Every input tool gains `seat: "agent" | "user"` (default: `agent` in `seat`
mode, otherwise `user`) and returns a verdict. Clipboard tools gain `seat`
(default `agent`). Paste is never used as a text-entry method [C2]. The exact
schemas are in PLAN-5.

## 9. Runtime files (contract change from 0.3)

The server writes, all under `$XDG_RUNTIME_DIR` (0700 dirs, 0600 files) unless
noted:

| path | what |
|---|---|
| `seshat/streams/computer-use-sway.jsonl` | timeline stream (unchanged contract; verdicts go inside `payload`) |
| `computer-use-sway/session.json` | agent output name, saved user output positions, mode |
| `computer-use-sway/launch/<n>.sh` | one-shot launcher scripts for `launch`, deleted after the window maps |
| `computer-use-sway/sandbox-<pid>/` | sandbox mode root (nested sway, buses); removed on stop. Teardown removes **only this explicit path** |
| `~/.config/sway/config.d/computer-use-sway.conf` | written only by the explicit `--install-sway-config` command (0644) |

## 10. Timeline payloads (curation rules)

These rules are added to the existing ones. Typed and set text becomes
`characters`, clipboard content becomes `bytes`, and neither value is
written. New keys:

- `act`: `action`, `ref`, `delivery`, `effect`, `characters` (for text
  actions), `disturbed_user`
- `run`: `steps`, `tools` (list of step tool names), `failed_step`
- `launch`: `program` (basename of argv[0] only)
- `observe`: `scope`, `window`, `role`, `elements` (count), `ocr` (bool). A
  query's `name`/`text` become `name_chars`/`text_chars`.
- `wait_for`: `kind`, `satisfied`, `waited_ms`
- `session`: `action`, `mode`

## 11. Threads and concurrency

- Main thread: the MCP stdin loop. It owns the Wayland connection, the a11y
  bus connection and all actions. Tools run one at a time.
- `swayipc.Subscription` thread: events `workspace, window, output, binding,
  shutdown` go into a bounded `EventLog` (deque of 4096, a Condition for
  waits). The reload callback only sets flags; it never sends input.
- Sandbox child processes are owned by `sandbox.Sandbox` and reaped on stop.

## 12. Non-goals for 1.0

- An embedded grounding model (contract: no LLM).
- libei/portal input, Hyprland, other compositors.
- Recording the agent output (seshat's domain; `session status` exposes the
  agent output name so seshat can target it).

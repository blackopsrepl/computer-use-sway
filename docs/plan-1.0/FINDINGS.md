# 1.0 Findings — what is proven, and how

Every row below was measured, not inferred. Evidence lives in `spikes/`; each
spike starts its own private D-Bus session, AT-SPI bus + registry, and a
headless nested Sway 1.10.1 (`xwayland disable`), so nothing touches the live
session. `spikes/run_all.py` re-runs all of them and prints PASS/FAIL per
claim; it is the acceptance oracle for the implementation.

Host used: Sway 1.10.1, at-spi2-core 2.56.3, GTK 3/4 (system python gi),
Qt 6 (`qml6`), Firefox 153 ESR, Chromium 156, Electron 43 (Obsidian's runtime
re-used as a bare Electron), tesseract 5.5.3.

Items marked **LIVE** were also checked against the real session (read-only or
fully reverted). Everything else ran in the sandbox.

## 1. Seats

| # | Fact | Evidence |
|---|---|---|
| S1 | A second Sway seat is a second `wl_seat` global. Clients see it by name (`wl_seat.name`). | spike1_seats, spike1b |
| S2 | `zwlr_virtual_pointer_manager_v1.create_virtual_pointer(seat)` and `zwp_virtual_keyboard_manager_v1.create_virtual_keyboard(seat)` attach the device to the named seat (`get_seats` lists them under it). | spike1 `P1_devices` |
| S3 | Seat focus is independent: agent types into B while seat0 keeps typing into A. Both texts arrive in the right windows. | spike1 `P2_text` = A:`u1u2`, B:`agent` |
| S4 | **Every Sway IPC command acts on seat0** (the default seat). `[con_id=X] focus` over IPC moves seat0 only. | spike1b `P3_ipc_focus_C` = `{agent:B, seat0:C}` |
| S5 | A **binding** runs in the seat of the keyboard that fired it. A binding fired by the agent's virtual keyboard moves only the agent seat. `[con_mark="_cus_target"] focus` gives exact, per-seat focus by con_id. | spike1b `P4_agent_binding_C` = `{agent:C, seat0:A}` |
| S6 | `--input-device` cannot tell virtual keyboards apart: every virtual keyboard has identifier `0:0:wlr_virtual_keyboard_v1`. The binding key must be a keysym no other keyboard sends (we use `XF86Macro27..30`, which have no physical key). | spike7 `R3_after_seat0_key` |
| S7 | `seat <name> fallback false` over IPC creates a seat at runtime. **`reload` destroys a runtime-created seat.** Its virtual devices fall back to seat0, and agent input then lands in the user's window. Windows that existed before the reload never accept the re-created seat again (GTK3 measured). | spike7 `R4_agent_typing_lands` = `{A:"zz"}`; spike7c part (a) `old_after_recovery=false` |
| S8 | A seat declared in a **config file** survives `reload` (same `wl_seat` global, so clients keep it). Its virtual devices still fall back to seat0 on reload. Destroying and re-creating them on the same `wl_seat` restores isolation. Pre-existing GTK3, GTK4 and Qt6 windows then accept agent input again. | spike7c part (b): 3/3 targets `preexisting_window_after_recovery=true`, `seat0_devices_after_recovery=[]` |
| S9 | `reload` emits IPC `workspace` event `change:"reload"` within ~5 ms. The workspace↔output pin and runtime bindings are dropped; runtime-created outputs survive. | spike7 `R4_events`; spike7b `reload_event_ms` |
| S10 | The wl_seat global for a newly created seat arrives **after** the IPC reply. A client must roundtrip/poll until it appears. | spike7b `KeyError` before the wait was added |
| S11 | Per-seat idle works: `ext_idle_notifier_v1.get_idle_notification(timeout, seat)` reports `idled`/`resumed` per seat. Agent input does not resume seat0. | spike12 `pass` |
| S12 | **Any runtime `seat` command moves the agent's virtual devices to seat0**, with no IPC event: `seat * xcursor_theme …`, `seat seat0 hide_cursor …` and `seat cus-agent …` alike (theme switchers run the first). Applying a seat config re-sorts every device by attach rules (`input_manager_apply_seat_config`), and virtual devices, whose identifiers are fixed strings, match none and fall back to seat0; `input * …` does not. Until 1.0.1 the agent kept typing through the moved devices, onto the user's seat. Input is now gated on the agent seat holding its devices (one `get_seats`, 0.055 ms, before every message) and they are recreated on the agent seat first. 1.0.1 fails all three commands; the fix passes, with nothing reaching the user's field and seat0's focus unchanged. | `tests/integration/test_seats.py` T2.4 |

## 2. Agent output and launching

| # | Fact | Evidence |
|---|---|---|
| O1 | `create_output` works on the DRM backend: sway 1.10.1 always adds a secondary headless backend (`sway/server.c:419`). **LIVE**: created, captured with `grim -o` (2 MB PNG), unplugged. | source + live run |
| O2 | **LIVE hazard:** if the user's outputs have no explicit position, `create_output` re-arranges the layout. DP-1 moved to x=101280, the pointer landed on the new output, and seat0 focus jumped to the new workspace. Pinning every active output at its current position (`output <n> position <x> <y>`) first prevents all of it. **LIVE** re-run: DP-1 stayed at 0,0 and seat0 focus never changed. | live runs 1 and 2 |
| O3 | `output <name> unplug` removes a created headless output and restores workspaces and focus. **LIVE** | live runs |
| O4 | Moving a window onto the agent workspace with IPC `move container to workspace` leaves seat0 alone. | spike1b `P5_focus_after_move` |
| O5 | `exec` from an **agent binding** maps the new window on the agent seat's focused workspace, via sway's launcher context matched by pid ancestry. This includes double-forked launchers. IPC `exec` maps on seat0's workspace. | spike1d `direct`/`forked` → `agent`; spike1b `P6_agent_binding_exec` |
| O6 | **Hazard:** when a view maps, `should_focus()` is evaluated against sway's *current handler seat* (the last seat that ran a command). If it returns true, `input_manager_set_focus()` focuses **every seat**, so seat0 jumps to the agent's new window (source: `sway/tree/view.c` `should_focus`, `input-manager.c` `input_manager_set_focus`). `no_focus` criteria do not help (the empty-workspace branch ignores them). Stripping `XDG_ACTIVATION_TOKEN` does not help. **Fix:** send IPC `nop` right after every agent-seat binding. That resets the handler seat to seat0, whose workspace differs from the map workspace, so `should_focus` is false. 3/3 runs clean. | spike11 before/after |
| O8 | `reload` drops runtime output positions: the agent output is auto-placed **adjacent** to the user's output (x=1280 in the sandbox), so the user's pointer could cross into it until recovery. The user's own output did not move (creation order). Recovery (re-pin saved user positions, move the agent output back to x=100000, re-pin the agent workspace) takes 5 ms. Seat0 focus and typing are unaffected. | spike13 `pass` |
| O7 | Agent output placed at x=100000 is disjoint from the user's layout, so the user's pointer can never wander onto it. Absolute virtual-pointer motion uses the layout bounding box as extent. | spike1b P5, spike11 |
| O9 | **Hazard:** never write a `workspace <name>` binding. When sway needs a new workspace for an output, `workspace_next_name()` takes free names from `workspace <name>` bindings (`sway/tree/workspace.c`, `workspace_name_from_binding`). A `bindsym … workspace cus-agent` in the config gave the **user's** output a workspace named `cus-agent`, and agent launches then mapped on the user's screen and focused both seats. Fix: rename the agent output's auto-created workspace over IPC (`rename workspace "<auto>" to cus-agent`), then put the agent seat on it by mark-focusing one of its windows, or, when it is empty, by clicking the empty background with the agent pointer. | spike14 before/after |
| O10 | **Production flow, end to end, 3/3 runs.** Config-declared seat and bindings (mark-focus, fixed-path exec) → bring-up → launch app 1 → agent types → `reload` → recovery → launch app 2 → agent types into both apps. The user typed throughout (`u1u2u3`, all in A) and seat0 focus never left A. Config-file and runtime bindings run in the same (firing) seat. | spike14, spike14b |
| O11 | **Keep sway's activation token on launch.** The agent-binding `exec` hands the app `XDG_ACTIVATION_TOKEN`/`DESKTOP_STARTUP_ID`; Qt6 and Chromium use it to place their first window. Stripped, both mapped on the user's workspace and took seat0 focus; kept, all of GTK3, GTK4, Qt6, Firefox and Chromium mapped on `cus-agent` with seat0 focus unchanged (5 toolkits × 2 variants, sandbox). `nop` after the binding (O6) is the whole focus fix. | `tests/integration/test_launch_placement.py` |
| O12 | **Sway consumes the launch context on the first window** (`view_assign_ctx` → `launcher_ctx_consume`, "prevent additional matches"). Every later toplevel of an agent-launched app falls back to seat0's focused workspace: a GTK3 confirm dialog mapped on workspace `1` and took seat0 focus (the user's typing went into it). An IPC `assign [pid=… app_id="^…$"] workspace cus-agent`, added before the window maps, puts it on `cus-agent` with seat0 focus unchanged. The reaper reports the app's pid so a pid criterion exists before the first map; a `window::new` listener moves any window that still escapes. | `tests/integration/test_launch_placement.py` `SecondaryWindowTests`; bench `dialogs-*`, `multiwindow-*` |

## 3. Input devices (stdlib Wayland client)

| # | Fact | Evidence |
|---|---|---|
| I1 | A ~250-line stdlib Wayland wire client (unix socket, `SCM_RIGHTS` for the keymap fd, `memfd_create`) is enough for registry, seats, virtual pointer, virtual keyboard and idle notifier. | spikes/wl.py |
| I2 | Drag via `zwlr_virtual_pointer_v1` (absolute motion + button): 20 held-motion events, press and release at different points. Scroll via `axis_discrete` arrives. **This replaces `/dev/uinput` and its udev rule.** | spike3 |
| I3 | A keymap built from `include "evdev"` + `pc+us+inet(evdev)` types ASCII on real physical keys. Any other keysym is bound to a slot rebound least-recently-used: 12 slots on the F13–F24 keys (xkb codes 191–202) for keys, and, since I6, six other keys for characters. **Slots must stay below xkb keycode 256:** with private codes at evdev ≥ 600, Chromium 156 and Electron 43 silently dropped every non-ASCII character (`aöb€c` → `abc`); on F13–F24 all of GTK3, GTK4, Qt6, Firefox, Chromium and Electron type it exactly. | spike8; slot spike on seat0 in the sandbox, 6 toolkits |
| I4 | **wlroots does not derive modifier state from a virtual keyboard's key events.** The client must send `zwp_virtual_keyboard_v1.modifiers(mask)` (Shift=1, Control=4, Mod1=8, Mod4=64) around the key. Without it shifted characters come out unshifted and Ctrl chords fail. | spike8 first vs second run |
| I5 | Raw input from a non-default seat, per toolkit: **accepted** by GTK3, GTK4, Qt6, Firefox; **ignored** by Chromium 156, Electron 43 and kitty, pointer and keyboard alike. Control runs with the same devices on seat0 succeed for all of them. | spike2 + spike4 `FORCE_SEAT=agent` |
| I6 | **Ghostty reads the physical key of F13–F24**: a character bound to an F-key slot arrived as the F-key's escape sequence (`ö` → `ESC[28~`). **Chromium drops keys it has no DOM code for**: on the six unassigned evdev codes (84, 195–199) `aöb` arrived as `ab`. Characters therefore use six keys Chromium maps and Ghostty has no key for: KEY_102ND, KEY_YEN, KEY_KPCOMMA, KEY_KPLEFTPAREN, KEY_KPRIGHTPAREN, KEY_OPEN (xkb `<LSGT> <AE13> <I129> <I187> <I188> <OPEN>`, found by diffing Ghostty's `keycodes.zig` against the DOM code table); keysyms that are keys stay on F13–F24. With that split, `Ab9 !@#$%^&*()_+{}|:"<>? é ü ß 日本 € ñ Ω` (8 distinct non-ASCII, so slots are rebound mid-text) arrives exactly in GTK3, GTK4, Qt6, Firefox, Chromium and Electron, in foot, Alacritty and Ghostty on both seats, and in kitty and WezTerm on seat0. | `tests/integration/test_seats.py` T1.2, `tests/integration/test_terminals.py` |
| I7 | Terminals and the agent seat: **foot 1.23, Alacritty 0.13 and Ghostty 1.4 accept it; kitty ignores it**. WezTerm (20240203) holds one keyboard and one pointer for all seats and releases them when any seat loses the capability (`window/src/os/wayland/seat.rs` `remove_capability`), so it loses the user's keyboard whenever another seat's keyboard goes away: when this server exits, on a sway `reload`, or with any second seat. That is WezTerm's bug; it is not a design input (1.0.0 worked around it with a permanent idle pointer and caused I8). | WezTerm probes in the 1.0.0 history |
| I8 | **A seat with a pointer draws its own cursor**, and sway keeps it inside the layout: with no agent output, the agent seat's cursor sits on a user output (sway `seat_update_capabilities` hides a seat's cursor only when the seat has no pointer). 1.0.0 kept an idle pointer on the agent seat for the life of the sway session (a workaround for WezTerm, I7) and showed a second cursor on the user's screen all the time; it also drew one during sessions, before the first agent motion. The agent seat now has devices only while the agent output exists: the output first, then `seat cus-agent cursor set` onto it, then the devices; on stop the devices go first. Measured in a sandbox with software cursors, where a window-less output is one flat colour unless a cursor is drawn on it: no cursor on the user output while running, after stop, after restart and after a reload; the agent's cursor drawn on the agent output; 1.0.0 fails 5 of these 5 checks. | `tests/integration/test_cursor.py` |

## 4. Accessibility (stdlib D-Bus)

| # | Fact | Evidence |
|---|---|---|
| A1 | A ~300-line stdlib D-Bus client (SASL EXTERNAL, marshalling incl. variants/dicts/structs, signals) drives AT-SPI. **LIVE (read-only)**: enumerated 9 apps on the real a11y bus, walked Zen's 7,615 nodes in 2.4 s; `Cache.GetItems` returned the same 7,615 in 191 ms. | spikes/dbus.py, atspi.py |
| A2 | App ↔ Sway window mapping: `GetConnectionUnixProcessID(bus_name)` == Sway `pid` (or one of its ancestors' children, e.g. Firefox/Chromium multi-process). | spike4 `A1_app` for all targets |
| A3 | A private session bus needs `at-spi-bus-launcher --launch-immediately` and `at-spi2-registryd` started explicitly (systemd activation is unavailable there). | spikes/sbx.py |
| A4 | Coordinates: `Component.GetExtents(WINDOW)` + Sway `rect + window_rect` gives layout points, after two corrections: (a) **CSD shadow margin**: subtract the origin of the bounding box of the frame's direct children when the frame extents differ from `window_rect` size (GTK3 CSD: 20/20 tiled, 26/23 floating); also subtract the frame's own extents origin (Firefox reports the frame at 20,20 / 26,23). (b) **Chromium/Electron report descendant extents in device pixels** at scale ≠ 1, so divide them by the output scale. With both, a click on the computed center hits the intended button in 12/12 cases (GTK3, GTK3-CSD floating, GTK4, Qt6, Firefox, Chromium, Electron, at scale 1 and 2). | spike4 matrix (`Hit` button, miss coordinates reported by the page) |
| A5 | `SCREEN` coordinates are unusable on Wayland (GTK4 returns 0,0 for children; others equal WINDOW). Never use them. | spike4 `btnS` |
| A6 | Semantic actions with no input device at all: `Action.DoAction` presses buttons in GTK3, GTK4, Qt6, Chromium, Electron, Firefox (Firefox reports `NActions=0` but `GetName(0)="press"` and `DoAction(0)` works). | spike4, spike4b |
| A7 | `EditableText.SetTextContents` works in GTK3, GTK4 and Qt6 (readback equal). **Gecko returns `true` and changes nothing**, so readback is mandatory. Chromium/Electron do not implement the method (`UnknownMethod`). | spike4, spike4b |
| A8 | `Component.GrabFocus` moves DOM focus inside Chromium/Electron (`focused` state set). | spike4b |
| A9 | Chromium and Electron expose no tree unless started with `--force-renderer-accessibility`. Setting `org.a11y.Status.IsEnabled/ScreenReaderEnabled` before launch and `ACCESSIBILITY_ENABLED=1` do **not** enable it. Gecko and Qt honour the bus status (Zen's tree is live on the real session). | spike4 `chromium-noforce`, `:::s`, `chromium-env`, `electron-env` |
| A10 | `Cache.GetItems` exists on Gecko, Qt, GTK4 (sparse) and not on GTK3. A pruned `GetChildren` walk (skip subtrees not `showing`) is the portable baseline. | spike6 `cache_getitems` |
| A11 | AT-SPI events arrive after `Registry.RegisterEvent` + `AddMatch`: GTK3 and Chromium emit `PropertyChange`/`TextChanged`; Qt emitted none for a press. Sway `window::title` events arrived for all (0.7 ms GTK/Qt, 203 ms Chromium). No single signal covers every toolkit. | spike5 |
| A12 | **GTK3 runs a pressed widget's handler inside `DoAction`.** When the handler starts a nested main loop (`gtk_dialog_run`), the app answers no AT-SPI call until the loop returns (every `GetChildren` timed out; the dialog was invisible to the tree). A press through the agent-seat pointer leaves the tree live (5/5). Qt's `QMessageBox::exec` from `DoAction` does not block (measured with a Qt widgets app). | bench `dialogs-*`; scratch probes `dlg2`/`dlg5`, `qtmodal` |
| A13 | **Popup menu contents report extents relative to the popup surface**, and no Wayland client or IPC learns where a popup sits: a GTK3 menu item reported `@100008,85`, its true position was `@100048,152`. Elements inside a `menu` have no pointer coordinates; only their semantic actions reach them. | bench `menus-*`; scratch probe `menu2` |
| A14 | A Qt widgets top-level without a window role is exposed as a `filler`, not a `frame`; it is matched to its Sway window by title. A single button is a real tree (two nodes), so "tree present" is "more than three nodes or any node with Action". | scratch probe `qtm` |

## 5. Pixels and OCR

| # | Fact | Evidence |
|---|---|---|
| P1 | `grim -t ppm -s 2 -g <region>` gives raw RGB at a fixed 2 px per layout point at any output scale, so image→layout is a division by 2. No PNG decoding is needed. | spike6 |
| P2 | Any single tesseract configuration fails on some toolkit or theme (dark multi-level GTK themes defeat global thresholds; inversion helps some, hurts others). A **union of four variants**, run in parallel and deduplicated by text + box overlap, found and clicked `Hit` in 10/10 cases (GTK3 ×2 scales, GTK3-CSD, GTK4 ×2, Qt6 ×2, Firefox, Chromium ×2). The variants: raw PPM; green-channel gray; inverted gray; mid-range binarized, all with `thresholding_method=2` (Sauvola). Each case took ~430 ms. | spike6 + spikes/ocr.py |
| P3 | A whole-layout capture spans the gap to the agent output at x=100000: 101,920×1080 px (110 megapixels) of which the user sees 1280×800. The default screenshot is the bounding box of the user's outputs. | bench legacy arm, before/after |
| P4 | A blinking text caret changes a 1–2 px column; byte-equality made every caret-bearing window "changed" and every OCR ref stale. A change narrower than 6 px over at most 64 rows is noise; an OCR ref compares only its own box. | `tests/integration/test_perception.py` `RefTests` |

## 6. Foreground path for single-seat engines

| # | Fact | Evidence |
|---|---|---|
| F1 | Chromium/Electron text entry without touching the user's pointer: IPC `[con_id=C] focus` → AT-SPI `GrabFocus(field)` → seat0 virtual keyboard types → IPC `[con_id=prev] focus`. The user's typing before and after stays in their window; seat0 focus is restored; total ~300 ms. | spike10 chromium + electron `pass` |
| F2 | Buttons, links and other actionable elements in Chromium/Electron need no foreground at all (A6). | spike4b |

## 7. Clipboard

| # | Fact | Evidence |
|---|---|---|
| C1 | `wl-copy --seat <s>` / `wl-paste --seat <s>` keep per-seat selections, and seat0's clipboard is untouched by agent copies. | spike9 |
| C2 | Paste honours the agent seat's selection in GTK3 and Firefox, but **GTK4 and Qt6 paste seat0's clipboard** even when Ctrl+V comes from the agent seat. Paste is therefore never a text-entry method for the agent. | spike9 matrix |

## 8. Compatibility facts that shape the contract

- seshat's stream reader keeps only `at_monotonic, tool, ok, payload, source`
  (`seshat/streams.py`). New data (effect verdicts) must go **inside `payload`**.
- The live sway config already has `include ~/.config/sway/config.d/*`, so the
  one-time install is a single file in that directory.
- Electron apps already running without `--force-renderer-accessibility`
  (Obsidian, Discord, Hermes itself: 3 nodes each, measured live) stay opaque
  until relaunched with the flag. OCR is their only structure source until then.

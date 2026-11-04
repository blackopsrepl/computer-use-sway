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

## 3. Input devices (stdlib Wayland client)

| # | Fact | Evidence |
|---|---|---|
| I1 | A ~250-line stdlib Wayland wire client (unix socket, `SCM_RIGHTS` for the keymap fd, `memfd_create`) is enough for registry, seats, virtual pointer, virtual keyboard and idle notifier. | spikes/wl.py |
| I2 | Drag via `zwlr_virtual_pointer_v1` (absolute motion + button): 20 held-motion events, press and release at different points. Scroll via `axis_discrete` arrives. **This replaces `/dev/uinput` and its udev rule.** | spike3 |
| I3 | A keymap built from `include "evdev"` + `pc+us+inet(evdev)`, plus private keycodes (evdev ≥ 600) for any other keysym, types ASCII on real physical keys and any Unicode through private keycodes. | spike8: `Ab9 !@#$%^&*()_+{}\|:"<>? é ü ß 日本 €` exact in GTK3, GTK4, Qt6, Firefox |
| I4 | **wlroots does not derive modifier state from a virtual keyboard's key events.** The client must send `zwp_virtual_keyboard_v1.modifiers(mask)` (Shift=1, Control=4, Mod1=8, Mod4=64) around the key. Without it shifted characters come out unshifted and Ctrl chords fail. | spike8 first vs second run |
| I5 | Raw input from a non-default seat, per toolkit: **accepted** by GTK3, GTK4, Qt6, Firefox; **ignored** by Chromium 156, Electron 43 and kitty, pointer and keyboard alike. Control runs with the same devices on seat0 succeed for all of them. | spike2 + spike4 `FORCE_SEAT=agent` |

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

## 5. Pixels and OCR

| # | Fact | Evidence |
|---|---|---|
| P1 | `grim -t ppm -s 2 -g <region>` gives raw RGB at a fixed 2 px per layout point at any output scale, so image→layout is a division by 2. No PNG decoding is needed. | spike6 |
| P2 | Any single tesseract configuration fails on some toolkit or theme (dark multi-level GTK themes defeat global thresholds; inversion helps some, hurts others). A **union of four variants**, run in parallel and deduplicated by text + box overlap, found and clicked `Hit` in 10/10 cases (GTK3 ×2 scales, GTK3-CSD, GTK4 ×2, Qt6 ×2, Firefox, Chromium ×2). The variants: raw PPM; green-channel gray; inverted gray; mid-range binarized, all with `thresholding_method=2` (Sauvola). Each case took ~430 ms. | spike6 + spikes/ocr.py |

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

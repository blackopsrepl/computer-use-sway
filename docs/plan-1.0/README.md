# computer-use-sway 1.0 — implementation plan

This directory is a complete, proven plan for rebuilding `computer-use-sway`
into a Sway-native computer-use server. The agent has its own seat and
workspace, grounds on structure before pixels, and every action returns a
verified effect. Every capability the plan relies on was measured first.

## Read in this order

1. **FINDINGS.md**: what is proven, with the spike that proves it. Every
   design rule cites a finding (`[S4]`, `[O9]`, …).
2. **ARCHITECTURE.md**: the target design and contracts.
3. **PLAN-1-transport.md**: stdlib Sway IPC, Wayland wire, virtual input,
   D-Bus. Removes swaymsg, wtype and /dev/uinput.
4. **PLAN-2-agent-session.md**: config-declared agent seat, agent output,
   reload recovery, idle gate, session modes, sandbox, install.
5. **PLAN-3-perception.md**: AT-SPI client, geometry, toolkit profiles,
   capture, OCR ensemble, refs, `observe`.
6. **PLAN-4-action.md**: verdicts, the delivery ladder (`act`), `launch`,
   `wait_for`, `run`.
7. **PLAN-5-surface-tests-release.md**: schemas, server/timeline changes,
   integration suite, benchmark, docs, release.
8. **spikes/**: the proven reference implementations. `wl.py`, `ipc.py`,
   `dbus.py`, `atspi.py`, `ocr.py`, `pgm.py` and `sbx.py` are the code to
   port. The `spikeN_*.py` files are the experiments.

## Before writing any code

```bash
cd docs/plan-1.0/spikes
python3 run_all.py            # ~15 min; must print "0 failing claim(s)"
```

The runner re-proves every finding on the current machine (sandboxed: a
private nested Sway, D-Bus and AT-SPI; nothing touches the live session).
If any claim fails, **stop and report the failing claim** instead of coding
around it. A failing claim means the platform changed and the plan's premise
with it.

Requirements for the runner: sway, grim, at-spi2-core, `/usr/bin/python3`
with `gi` (GTK3/GTK4 typelibs), qml6, firefox, chromium, tesseract. The
Electron claims use a bare Electron oracle. Build it first, into
`${XDG_CACHE_HOME:-~/.cache}/computer-use-sway/electron-oracle` (or point
`CUS_TEST_ELECTRON` at one): `spikes/make_electron_oracle.sh <dir of any
Electron app runtime>`, e.g. the Obsidian install directory. Spike roots go
under `.../computer-use-sway/spikes` (`CUS_SPIKE_SCRATCH` overrides).

## Rules for the implementer

1. **Follow the phases in order.** Each phase ends green (`make check` plus
   that phase's integration tests) before the next starts.
2. **Port, don't redesign.** The spike modules are measured. When the plan
   says "port from spikes/X", keep the protocol details: opcodes, the
   ordering of requests, the sleeps marked as measured. Harden error
   handling and types around them.
3. **Never re-open a decided trap.** These are measured hazards:
   - creating the seat at runtime [S7]
   - skipping the `nop` after agent bindings [O6]
   - adding a `workspace <name>` binding [O9]
   - `create_output` without pinning user outputs [O2]
   - pasting text through the agent clipboard [C2]
   - relying on `SetTextContents`'s return value [A7]
   - using SCREEN coordinates [A5]
   - omitting the virtual-keyboard `modifiers` request [I4]
   - a sandbox nested sway with Xwayland enabled
   - removing any path except the sandbox's own explicit root
4. **House rules** (AGENTS.md):
   - `dependencies = []`
   - no file at or above 500 lines
   - no hand-edited version/CHANGELOG
   - atomic conventional commits
   - the timeline never contains typed text, clipboard content, argv or env
5. **Tests are deterministic** in `tests/` (fakes, socketpairs). Anything
   needing a compositor goes in `tests/integration/` and uses the product's
   `sandbox.Sandbox`.
6. **Never live-fire input into the user's session** while developing. Use
   sandbox mode. The only live operations the plan performs are the explicit,
   user-run `--install-sway-config` and normal use after release.
7. When a fact in FINDINGS seems wrong on your machine, re-run that spike,
   then report. Do not guess.

## File map (create / modify / delete)

| file | action | plan |
|---|---|---|
| `src/computer_use_sway/swayipc.py` | create | 1.1 |
| `src/computer_use_sway/desktop.py` | modify | 1.2 |
| `src/computer_use_sway/wire.py` | create | 1.3 |
| `src/computer_use_sway/keymap.py` | create | 1.4 |
| `src/computer_use_sway/vinput.py` | create | 1.5 |
| `src/computer_use_sway/tools.py` → `tools_input.py`, `tools_observe.py` (+ registry) | split | 1.6 |
| `src/computer_use_sway/dbus.py` | create | 1.7 |
| `src/computer_use_sway/core.py` | modify | 1.8 |
| `src/computer_use_sway/pointer.py`, `tests/test_pointer.py` | delete | 1.9–1.10 |
| `src/computer_use_sway/install.py` | create | 2.1 |
| `src/computer_use_sway/agent.py` | create | 2.2 |
| `src/computer_use_sway/sentinel.py` | create | 2.3 |
| `src/computer_use_sway/session.py` | create | 2.4 |
| `src/computer_use_sway/sandbox.py` | create | 2.5 |
| `tools_input.py` / `specs_input.py` (seat param) | modify | 2.6 |
| `src/computer_use_sway/cli.py` (moved out of server.py) | create | 2.7 |
| `src/computer_use_sway/atspi.py` | create | 3.1 |
| `src/computer_use_sway/toolkits.py` | create | 3.2 |
| `src/computer_use_sway/geometry.py` | create | 3.3 |
| `src/computer_use_sway/capture.py` | create | 3.4 |
| `src/computer_use_sway/ocr.py` | create | 3.5 |
| `src/computer_use_sway/query.py` | create | 3.6 |
| `src/computer_use_sway/refs.py` | create | 3.7 |
| `src/computer_use_sway/scene.py` | create | 3.8 |
| `tools_observe.py` (observe, read, screenshot window/target_output) | modify | 3.9 |
| `src/computer_use_sway/verify.py` | create | 4.1 |
| `src/computer_use_sway/actions.py` (+ `actions_foreground.py` if needed) | create | 4.2 |
| `src/computer_use_sway/launch.py` | create | 4.3 |
| `src/computer_use_sway/waits.py` | create | 4.4 |
| `src/computer_use_sway/plans.py` | create | 4.5 |
| `src/computer_use_sway/tools_semantic.py` | create | 4.6 |
| `tools_input.py` (verdicts on low-level tools) | modify | 4.7 |
| `specs.py` → `specs_observe.py`, `specs_input.py`, `specs_semantic.py` | split | 5.1 |
| `src/computer_use_sway/server.py` | modify | 5.2 |
| `src/computer_use_sway/timeline.py` | modify | 5.3 |
| `cli.py` doctor/self-test | modify | 5.4 |
| `tests/integration/**` | create | 5.5 |
| `bench/**` | create | 5.6 |
| `Makefile`, `pyproject.toml` | modify | 5.7–5.8 |
| `AGENTS.md`, `WIREFRAME.md`, `README.md`, `docs/architecture.md`, `skill/solverforge-computer-use/SKILL.md`, `docs/codex.md` | modify | 5.9 |

Unit test files to create alongside: `test_swayipc, test_wire, test_keymap,
test_vinput, test_dbus, test_install, test_agent, test_sentinel,
test_session, test_sandbox, test_atspi, test_toolkits, test_geometry,
test_ocr, test_query, test_refs, test_scene, test_verify, test_actions,
test_launch, test_waits, test_plans`. Update `test_tools`, `test_protocol`
and `test_timeline`.

## Definition of done

- `make check && make integration && make bench` green on this host.
- `run_all.py` still prints 0 failing claims (the platform facts held for the whole build).
- T4.6 (`test_user_untouched`) passes 5 consecutive runs: the user's typing
  and focus are never disturbed in seat mode.
- Docs match shipped behaviour (PLAN-5 §5.9).
- Release 1.0.0 cut through `commit-and-tag-version`.
- Public claims of exceeding other harnesses wait for benchmark layer 2 (PLAN-5 §5.6).

# PLAN 2 — Agent seat, session, install, sandbox

Phase goal: the agent seat and output exist on the live session. They survive
`reload` and never disturb seat0. All low-level input tools gain
`seat: "agent" | "user"`.

Exit criteria:

- `make check` green.
- Integration tests `test_seat_isolation`, `test_reload_recovery`,
  `test_bringup` and `test_idle_gate` pass (PLAN-5 §T2). They are ports of
  spike1b, spike7c(b), spike13, spike14 and spike12, run against the product
  modules instead of the spike modules.

Facts this phase relies on: S1–S11, O1–O10 in FINDINGS.md. Re-read O2, O6, O8
and O9 before writing a single line of `agent.py`: each is a trap that was
measured to hurt the user.

---

## 2.1 `src/computer_use_sway/install.py` — CREATE (≈190 lines)

**Public API**

```python
SEAT = "cus-agent"
FOCUS_KEY, LAUNCH_KEY = "XF86Macro27", "XF86Macro29"
FOCUS_MARK = "_cus_focus"
def config_path() -> Path        # $XDG_CONFIG_HOME or ~/.config, + sway/config.d/computer-use-sway.conf
def launcher_path() -> Path      # $XDG_RUNTIME_DIR/computer-use-sway/launch/next.sh (absolute)
def render() -> str
def included(sway_config_text: str, path: Path) -> bool
def status() -> dict             # {"file": bool, "included": bool, "seat_live": bool, "conflicts": [..], "ok": bool}
def install(reload: bool = True) -> dict
def uninstall(reload: bool = True) -> dict
```

**`render()`** returns exactly:

```
# computer-use-sway agent seat — managed by `computer-use-sway --install-sway-config`.
# Do not add a `workspace <name>` binding here: sway donates such names to new
# workspaces on any output (see docs/plan-1.0/FINDINGS.md O9).
seat cus-agent fallback false
bindsym --no-repeat XF86Macro27 [con_mark="_cus_focus"] focus
bindsym --no-repeat XF86Macro29 exec <launcher_path()>
```

**`included()`**: parse every line of the live config (`swayipc.connect().config()`)
that starts with `include`. Expand `~` and `$HOME`, glob it, and return
whether `path` matches any glob. This handles the live user config's
`include ~/.config/sway/config.d/*`.

**`status().conflicts`**: any line of the live config, *outside our own
file*, that binds `XF86Macro27` or `XF86Macro29`.

**`install()`**:

1. Refuse with `ToolError` when `included()` is false. The message tells the
   user to add `include ~/.config/sway/config.d/*` to their sway config.
   Never edit the user's main config.
2. Refuse when `conflicts` is non-empty (name the lines).
3. Create the launcher dir (0700) and write a placeholder `next.sh` (0700,
   `#!/bin/sh\nexit 0\n`).
4. Write the config file (0644) only when its content differs.
5. If `reload`: run IPC `reload`. Then poll `seats()` for up to 2 s until
   `cus-agent` exists; if it never appears, raise.
6. Return `status()`.

**`uninstall()`**: remove the file, `reload`, return `status()`.

**Note on `$XDG_RUNTIME_DIR`:** the launcher path is absolute, written at
install time. If the runtime dir changes (a different uid), `status()`
reports `ok: false` with reason `launcher path mismatch`.

**Tests** (`tests/test_install.py`):

- `render()` content: no `workspace` binding
- `included()` with `~/.config/sway/config.d/*`, an absolute glob, a missing
  include, and an include of another dir
- conflict detection
- `install()` writes 0644, is idempotent, refuses without include

Use a tmp `XDG_CONFIG_HOME` and a fake Ipc.

---

## 2.2 `src/computer_use_sway/agent.py` — CREATE (≈420 lines)

The single owner of the agent seat, devices and output.

**Public API**

```python
class Agent:
    def __init__(self, ipc: swayipc.Ipc, conn: wire.Connection, log: swayipc.EventLog, state: StateFile) -> None
    output: str | None              # agent output name
    devices: vinput.SeatDevices | None
    def start(self) -> dict          # idempotent
    def stop(self) -> None           # idempotent
    def gate(self) -> None           # vinput gate: raise AgentSeatLost when stale
    def ensure_ready(self) -> None   # recover if stale; called before every agent operation
    def focus(self, con_id: int) -> None
    def to_workspace(self) -> None
    def tap(self, key: str) -> None  # chord on agent keyboard + IPC "nop"  [O6]
    def status(self) -> dict

class StateFile:   # $XDG_RUNTIME_DIR/computer-use-sway/session.json, 0600, dir 0700
    def load(self) -> dict
    def save(self, data: dict) -> None
    def clear(self) -> None
```

**`start()`**, in this exact order. Each step is idempotent.

1. Preflight: `install.status()`. If `seat_live` is false, raise
   `ToolError("agent seat 'cus-agent' is not configured; run: computer-use-sway --install-sway-config")`.
   **Never create the seat at runtime** [S7].
2. Register `self._on_event` as an `EventLog` listener. It only sets flags:
   - `workspace` + `change == "reload"` → `self.stale = "reload"`
   - `output` → `self.layout_dirty = True`
   - `shutdown` → `self.stale = "shutdown"`
3. Devices: `SeatDevices(conn, "cus-agent", gate=self.gate)`.
4. Output: from `StateFile`, if the saved output name still exists in
   `ipc.outputs()` **and** has a workspace named `cus-agent`, adopt it.
   Otherwise create one:
   1. `saved_user = {name: (x, y) for active outputs}`
   2. `output <name> position <x> <y>` for each of them [O2]
   3. `create_output`
   4. `new = (names after) − (names before)`; exactly one, else raise
   5. `output <new> position 100000 0 resolution 1920x1080`
   6. `workspace cus-agent output <new>`
   7. rename: the workspace on `<new>` whose name is not `cus-agent` →
      `rename workspace "<auto>" to cus-agent` [O9]
   8. save `{output, saved_user, created_at}` to the StateFile
5. Seat0 safety check: capture seat0 focus before step 4 and compare after.
   If it changed, restore it with IPC `[con_id=<prev>] focus` and record
   `disturbed_user` in the result. The live run with pinning did not move it;
   this is defence in depth.

**`ensure_ready()`** runs at the start of every agent operation:

- `stale == "reload"`:
  1. `devices.recreate()` [S8]
  2. re-apply each saved user pin [O8]
  3. `output <agent> position 100000 0 resolution 1920x1080`
  4. `workspace cus-agent output <agent>`
  5. verify `ipc.seats()`: `cus-agent` has exactly 2 devices, and seat0's
     device count did not grow (keep the seat0 count from start in memory).
     A mismatch raises `ToolError("agent seat recovery failed: …")`.
  6. clear `stale`
- `stale == "shutdown"`: raise `ToolError("sway is shutting down")`.
- `layout_dirty` (a user hot-plugged a monitor):
  1. re-read the outputs
  2. update `saved_user` from the new positions, excluding the agent output
  3. re-pin them
  4. re-assert the agent output position
  5. clear the flag

**`gate()`**: raise `vinput.AgentSeatLost("agent seat lost to a sway reload; retrying after recovery")`
when `self.stale` is set. Callers (`actions.py`) catch `AgentSeatLost` once,
call `ensure_ready()`, and retry the step **only if it was not partially
delivered**. For typing, report `partial: true` and the number of characters
sent; never retype.

**`focus(con_id)`** [S5]:
1. `ipc.command(f"[con_id={con_id}] mark --add {FOCUS_MARK}")`
2. `self.tap(FOCUS_KEY)`
3. poll `ipc.seats()` for up to 300 ms until `cus-agent` focus == con_id,
   else raise `ToolError`
4. `ipc.command(f"[con_id={con_id}] unmark {FOCUS_MARK}")`

**`to_workspace()`** [O10]: if a window exists on workspace `cus-agent`,
`focus()` it. Otherwise click the center of the agent output with
`devices.pointer.click`; the output is empty there, so the click hits the
workspace background. Then send IPC `nop`.

**`tap(key)`**: `devices.keyboard.chord(key)`, then **always**
`ipc.command("nop")` [O6].

**`stop()`**:
1. `output <agent> unplug`, if this process created or adopted it [O3]
2. `devices.destroy()`
3. `StateFile.clear()`
4. remove the listener

User output pins stay applied. They equal the current positions, so they are
invisible, and removing them would require a reload.

**`status()`**: `{seat, output, workspace: "cus-agent", devices_ok, stale, created_by_this_process}`.

**Tests** (`tests/test_agent.py`, fake Ipc that records commands, fake devices):

- order: the pins come before `create_output`
- `rename workspace` is issued with the auto name
- `nop` follows every `tap`
- the reload event triggers recreate + re-pin + verify
- adopt path when the state file names a live output
- `start()` refuses when the seat is missing
- `gate` raises when stale

---

## 2.3 `src/computer_use_sway/sentinel.py` — CREATE (≈170 lines)

**Public API**

```python
class Sentinel:
    def __init__(self, ipc, conn: wire.Connection, user_seat: str) -> None
    def snapshot(self) -> dict            # {"focus": con_id_of_seat0}
    def restore(self, snap: dict) -> bool # IPC [con_id=..] focus if moved; True if it had to restore
    def user_idle_ms(self) -> int | None  # ms since seat0 last went idle, else 0 when active; None if unsupported
    def wait_user_idle(self, idle_ms: int, max_wait_ms: int) -> bool
```

**Idle tracking** [S11]:

- Bind `ext_idle_notifier_v1` and request
  `get_idle_notification(new_id, timeout=idle_ms_threshold, seat0_obj)`.
  Opcodes: request `get_idle_notification = 1`; events `idled = 0`, `resumed = 1`.
- Keep `self.idle_since` (monotonic) and `self.active`.
- `wait_user_idle(idle_ms, max_wait)`:
  1. Re-create the notification with timeout `idle_ms` whenever the requested
     threshold changes.
  2. `conn.dispatch()` in 50 ms slices until `idled` arrives or `max_wait` passes.
  3. Return whether idle.
- Unsupported (global absent) → `wait_user_idle` returns True. Report
  `idle_gate: "unsupported"` in verdicts.

**Note:** our own seat0 virtual keyboard input resets seat0 idle, so the
foreground step itself shows up as activity. That is correct, and it never
blocks: the idle check happens *before* the foreground step.

**Tests** (`tests/test_sentinel.py`, fake connection):

- encoding of `get_idle_notification`
- an `idled` event flips state
- restore issues IPC focus only when the focus moved

---

## 2.4 `src/computer_use_sway/session.py` — CREATE (≈260 lines)

**Public API**

```python
MODES = ("seat", "user", "sandbox")
DEFAULT_OPTIONS = {"foreground": "idle", "idle_ms": 1500, "idle_max_wait_ms": 10000,
                   "foreground_pointer": False, "settle_ms": 600, "ocr": "auto"}

class Session:
    mode: str
    options: dict
    ipc: swayipc.Ipc
    log: swayipc.EventLog
    def start(self, mode: str = "seat", options: dict | None = None) -> dict
    def stop(self) -> dict
    def status(self) -> dict
    def conn(self) -> wire.Connection        # lazy
    def agent(self) -> agent.Agent            # lazy start in seat/sandbox modes; ToolError in user mode
    def user_devices(self) -> vinput.SeatDevices
    def devices(self, seat: str) -> vinput.SeatDevices   # "agent" -> agent().devices after ensure_ready(); "user" -> user_devices()
    def default_seat(self) -> str             # "agent" in seat/sandbox mode, "user" in user mode
    def a11y(self) -> atspi.Client            # lazy; PLAN 3
    def sentinel(self) -> sentinel.Sentinel

def current() -> Session                      # process-wide singleton
```

**Rules**

1. Lazy start: the first call to `current().agent()` in a fresh process
   runs `start("seat")` with the default options.
2. Starting `sandbox` mode:
   1. stop any running mode
   2. `sandbox.Sandbox.start()`
   3. re-point the process environment for this session (`SWAYSOCK`,
      `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR` for wayland, the
      `DBUS_SESSION_BUS_ADDRESS` used by atspi) to the sandbox
   4. start `Agent` against the sandbox (its config declares the seat)

   Stopping sandbox mode restores the original environment.
3. The subscription thread (`swayipc.Subscription` on
   `["workspace","window","output","binding","shutdown"]`) starts with the
   session and is restarted when a mode change changes `SWAYSOCK`.
4. `atexit` and SIGTERM/SIGINT handlers call `stop()`. Hook them from
   `server.run_mcp_server()`: SIGTERM raises `SystemExit`, then `finally:
   current().stop()`.
5. `status()` returns `{mode, options, agent: agent.status() | None,
   user_seat, a11y: {"bus": bool, "enabled": {...}}, binaries: {...},
   ocr: bool}`.

**Tests** (`tests/test_session.py`):

- lazy start calls `Agent.start` once
- `user` mode refuses `agent()`
- `default_seat` per mode
- option validation: unknown option → ToolError; `foreground` ∈
  `{"idle","always","never"}`; numeric bounds

---

## 2.5 `src/computer_use_sway/sandbox.py` — CREATE (≈240 lines)

Port from `spikes/sbx.py` and harden it.

**Public API**

```python
class Sandbox:
    def __init__(self, width: int = 1920, height: int = 1080, scale: float = 1.0) -> None
    root: Path   # $XDG_RUNTIME_DIR/computer-use-sway/sandbox-<pid>-<n>
    env: dict    # for children: XDG_RUNTIME_DIR=root, WAYLAND_DISPLAY, SWAYSOCK, DBUS_SESSION_BUS_ADDRESS, GDK_BACKEND=wayland, QT_QPA_PLATFORM=wayland, MOZ_ENABLE_WAYLAND=1
    def start(self) -> None
    def spawn(self, argv: list[str], extra_env: dict | None = None) -> subprocess.Popen
    def stop(self) -> None
```

**Rules**

1. The sandbox config is `install.render()` with the launcher path pointing
   inside the sandbox root, plus:
   `xwayland disable`, `output HEADLESS-1 resolution {w}x{h} scale {s}`,
   `seat seat0 fallback true`.
   **`xwayland disable` is mandatory**: a nested sway with Xwayland grabs the
   live `/tmp/.X11-unix/X0` (seen in the first probe). See also the memory
   note on teardown hazards.
2. Processes, in order:
   1. `dbus-daemon --session --nofork --address=unix:path=<root>/bus`
      (wait ≤ 2 s for the socket)
   2. `at-spi-bus-launcher --launch-immediately` and
      `at-spi2-registryd --use-gnome-session` [A3]
   3. `sway -c <root>/config` with `WLR_BACKENDS=headless`,
      `WLR_LIBINPUT_NO_DEVICES=1`, `WLR_RENDERER=pixman`, `SWAYSOCK=<root>/sway.sock`
   4. wait ≤ 5 s for `<root>/wayland-1` and the IPC socket
3. at-spi binaries are discovered in `/usr/libexec/at-spi2/`,
   `/usr/lib/at-spi2-core/` and `/usr/libexec/`. If missing, the sandbox still
   starts and `status` reports `a11y: unavailable`.
4. `stop()`:
   1. terminate children in reverse order (wait 5 s, then kill)
   2. `shutil.rmtree(self.root)` **only if** `self.root` is the exact path
      this instance created, is a directory, and lies under
      `$XDG_RUNTIME_DIR/computer-use-sway/`

   Assert all three before removing. Never compute a parent and remove it
   (house hazard).
5. Children's stderr goes to `<root>/<name>.log` (kept until stop) so a
   failed start can quote the log tail in its ToolError.

**Tests** (`tests/test_sandbox.py`):

- `stop()` refuses to remove a path outside the runtime prefix (monkeypatched root)
- config contains `xwayland disable` and the seat line
- env keys present

The real start is covered by the integration suite.

---

## 2.6 Seat parameter on low-level tools — MODIFY `tools_input.py`, `specs_input.py`

Every input tool (`move_pointer, click, drag, scroll, type_text, key`) gains:

```json
"seat": {"type": "string", "enum": ["agent", "user"], "description": "agent: the agent's own seat (default in seat/sandbox mode). user: the user's seat0 pointer and keyboard (default in user mode)."}
```

Resolution: `seat = args.get("seat") or session.current().default_seat()`.
Agent seat path:

1. `agent.ensure_ready()`
2. `sentinel.snapshot()`
3. perform the input
4. `sentinel.restore(snap)`
5. include `disturbed_user` in the result

`focus_window` gains `seat` too:

- `user`: IPC focus, as before [S4]
- `agent`: `agent.focus(con_id)`

New option `move_to_agent: bool` (default false): IPC `[con_id=N] move
container to workspace cus-agent` first [O4]. This hides the window from the
user's screen, so the description must say so.

`clipboard_set` / `clipboard_get` gain `seat` (default `"agent"` in seat
mode). Implement with `wl-copy --seat <name>` / `wl-paste --seat <name>`
[C1]. The tool description states: "Pasting from the agent clipboard works in
GTK3 and Firefox; GTK4 and Qt6 paste the user's clipboard regardless [C2].
To enter text use act/type_text, not paste."

---

## 2.7 `src/computer_use_sway/cli.py` — CREATE (move from server.py, ≈260 lines)

Move `install_codex_mcp`, `uninstall_codex_mcp`, `self_test`, `doctor`,
`parse_args`, `main` and `cli` out of `server.py`. Keep
`server.cli` importable for the console script by adding
`from .cli import cli` in server.py (or point `pyproject.toml`'s script at
`computer_use_sway.cli:cli`).

New flags: `--install-sway-config`, `--uninstall-sway-config`. Each prints
`install.status()` as JSON and exits 0 on ok, 1 otherwise. Doctor additions
are in PLAN 5 §5.4.

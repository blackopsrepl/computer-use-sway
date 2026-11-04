# PLAN 1 — Transport layer (stdlib IPC, Wayland, input, D-Bus)

Phase goal: the server talks to Sway, Wayland and D-Bus over sockets with the
standard library only. `swaymsg`, `wtype` and `/dev/uinput` disappear. The 12
existing tools keep their behaviour on seat0, now over the new transports.

Exit criteria for the phase:

- `make check` is green.
- `make integration` passes `test_transport_*` (written in PLAN-5 §T1).
- `rg -n "swaymsg|wtype|uinput" src/` returns only doctor/self-test strings
  that name removed binaries in an "obsolete" note.

Conventions for every new module in this plan:

- `from __future__ import annotations`, type hints on all public functions,
  module docstring explaining *why* (house style; see `pointer.py`'s docstring
  for the tone).
- Expected failures raise `core.ToolError` with an actionable message. Never
  let `OSError`/`socket.error` escape a public function: wrap them.
- No file may reach 500 lines. The budgets below are targets; split further
  if needed.
- Port from the spike files named in each section. They are proven
  reference implementations. Harden them; do not redesign them.

---

## 1.1 `src/computer_use_sway/swayipc.py` — CREATE (≈260 lines)

Port from `docs/plan-1.0/spikes/ipc.py`.

**Public API**

```python
MAGIC = b"i3-ipc"
RUN_COMMAND, GET_WORKSPACES, SUBSCRIBE, GET_OUTPUTS, GET_TREE = 0, 1, 2, 3, 4
GET_MARKS, GET_VERSION, GET_CONFIG, SEND_TICK = 5, 7, 9, 10
GET_INPUTS, GET_SEATS = 100, 101
EVENT_NAMES: dict[int, str]  # 0 workspace,1 output,2 mode,3 window,5 binding,6 shutdown,7 tick,0x14 input

class Ipc:
    def __init__(self, path: str | None = None, timeout: float = 5.0) -> None
    def call(self, kind: int, payload: str = "") -> Any
    def command(self, cmd: str) -> list[dict]        # raises ToolError if any result success:false
    def tree(self) -> dict
    def outputs(self) -> list[dict]
    def workspaces(self) -> list[dict]
    def seats(self) -> list[dict]
    def config(self) -> str                          # GET_CONFIG["config"]
    def close(self) -> None

class EventLog:
    def __init__(self, maxlen: int = 4096) -> None
    def append(self, name: str, body: dict) -> None   # stamps time.monotonic()
    def since(self, t: float, name: str | None = None) -> list[tuple[float, str, dict]]
    def wait_for(self, predicate, since: float, timeout: float) -> tuple | None
    def add_listener(self, fn) -> None                # fn(name, body); called on the reader thread

class Subscription(threading.Thread):
    def __init__(self, path: str, kinds: list[str], log: EventLog) -> None
    def stop(self) -> None

def socket_path() -> str     # $SWAYSOCK after core.ensure_session_environment(); ToolError if missing
def connect() -> Ipc         # module-level cached Ipc for the main thread; reconnects once on EPIPE
```

**Rules**

1. Framing: `MAGIC + struct.pack("=II", len, type) + payload`. Read exactly
   14 header bytes, then exactly `len` body bytes (loop on `recv`).
2. `command()` returns the reply list. If any element has `success: false`,
   raise `ToolError(f"sway: {element.get('error','command failed')} ({cmd})")`.
3. `Ipc` is used from the main thread only. `Subscription` has its own socket.
   `call()` holds a `threading.Lock` anyway (cheap and future-proof).
4. `connect()` caches one `Ipc`. On `BrokenPipeError`/`ConnectionError` it
   reconnects once and retries the call. A second failure raises
   `ToolError("lost the Sway IPC socket: …")`.
5. `Subscription.run()` loops `_read()`. On a socket error it sets
   `self.dead = True`, appends `("ipc_lost", {})` to the log, and returns.
   Listeners are called **after** append, inside `try/except Exception`
   (a listener can never kill the thread).
6. `EventLog.wait_for` waits on a `threading.Condition`. It returns the first
   event at or after `since` that satisfies the predicate, or `None` on timeout.
7. Event names: the high bit (`0x80000000`) is masked off before the
   `EVENT_NAMES` lookup.

**Tests** (`tests/test_swayipc.py`): use `socket.socketpair()` as a fake Sway:

- framing round trip
- `command()` raises on `success:false`
- 2-chunk body reassembly
- event high-bit decode
- `EventLog.wait_for` timeout and hit
- a listener exception does not stop the thread

---

## 1.2 `src/computer_use_sway/desktop.py` — MODIFY

- Replace every `core.read_json_command(["swaymsg", …])` and
  `core.run_command(["swaymsg", …])` with `swayipc.connect().tree()/outputs()/seats()/command(...)`.
- `require_session()` no longer requires the `swaymsg` binary. It checks
  `SWAYSOCK`, `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`, then calls
  `swayipc.connect().outputs()`.
- `simplify_window()` gains: `pid`, `window_rect`, `geometry`, `output` (the
  output whose rect contains the node's rect center), `shell`, `marks`.
- `collect_windows()` keeps its signature. Add
  `windows_by_id(tree) -> dict[int, dict]` and
  `window_for_pid(tree, pid) -> dict | None`. `window_for_pid` also matches
  when the window's pid is an ancestor or descendant of `pid`, using
  `/proc/<pid>/stat` field 4 (copy `ancestors()` from `spikes/spike4_a11y.py`).
- `validate_coordinates()` accepts points on **any** active output, including
  the agent output at x=100000 (it already iterates all outputs, so no logic change).
- `sway_cursor()` is deleted (no caller remains after PLAN 1.6).
- `detect_seat()` keeps returning the user seat name (`seat0` or the first
  pointer-capable seat that is **not** `cus-agent`).
- Add `layout_extent() -> tuple[int, int]`: max x+width and max y+height over
  active outputs. This is the absolute-motion extent for virtual pointers [O7].

Budget: stays < 300 lines. Tests: update `tests/test_tools.py` patches from
`read_json_command` to a fake `swayipc.Ipc` (a small class with canned
`tree/outputs/seats`).

---

## 1.3 `src/computer_use_sway/wire.py` — CREATE (≈320 lines)

Port from `spikes/wl.py` (the `Connection` class and helpers only).

**Public API**

```python
class WaylandError(core.ToolError): ...

def wl_string(value: str) -> bytes
def wl_fixed(value: float) -> int

class Connection:
    def __init__(self, display: str | None = None, runtime_dir: str | None = None) -> None
    globals: dict[int, tuple[str, int]]          # name -> (interface, version)
    def new_id(self) -> int
    def send(self, obj: int, opcode: int, payload: bytes = b"", fds: list[int] | None = None) -> None
    def set_handler(self, obj: int, fn) -> None   # fn(opcode:int, body:bytes)
    def dispatch(self, timeout: float = 0.0) -> None
    def roundtrip(self, timeout: float = 2.0) -> None
    def bind(self, iface: str, max_version: int, name: int | None = None) -> tuple[int, int]
    def has_global(self, iface: str) -> bool
    def seats(self) -> dict[str, int]                         # bind every wl_seat once; cache by global name
    def wait_seat(self, name: str, timeout: float = 2.0) -> int  # poll roundtrip until present [S10]
    def close(self) -> None
```

**Rules**

1. Object 1 is `wl_display`. Handle its `error` event by raising
   `WaylandError("protocol error on object N code C: text")`, and its
   `delete_id` event by freeing ids from `self.handlers`.
2. Track `wl_registry.global_remove` and drop the global. `seats()` must
   re-bind when a seat global is removed and re-added (the reload case is
   config-declared, so the global persists [S8]; still handle it).
3. `seats()` binds each `wl_seat` global (version ≤ 2 is enough, since only
   `name` is needed) exactly once per global name, keeps the mapping
   `seat_name -> object_id`, and returns a copy.
4. `send()` uses `sendmsg` with `SCM_RIGHTS` when `fds` is given. Partial
   writes: loop until everything is sent (use `sendall` when there are no fds).
5. Reading: append to a buffer; parse while `len(buf) >= 8 and len(buf) >= size`.
   An unknown object id is ignored.
6. Socket errors become `WaylandError("lost the Wayland connection: …")`.
7. Not thread-safe by design: used from the main thread only (ARCHITECTURE §11).

**Tests** (`tests/test_wire.py`), with a socketpair fake compositor:

- string padding: lengths 0–5
- fixed encoding
- registry global parse from canned bytes
- `bind` request encoding
- fd passing: send a memfd and receive it on the other end with `recvmsg`
- a display error is raised as `WaylandError`

---

## 1.4 `src/computer_use_sway/keymap.py` — CREATE (≈170 lines)

Port the tables from `spikes/wl.py` (`US_ROWS`, `US`, `NAMED`, `MOD_KEYS`,
`PRIVATE_BASE`, `KEYMAP`).

**Public API**

```python
US: dict[str, tuple[int, bool]]        # char -> (evdev code, needs_shift)
NAMED: dict[str, int]                  # keysym name -> evdev code
MOD_KEYS: dict[str, int]               # "shift","ctrl","alt","logo" -> evdev code
MOD_MASK: dict[str, int]               # "shift":1,"ctrl":4,"alt":8,"logo":64   [I4]
SLOTS = [(183 + i, f"FK{13 + i}") for i in range(12)]   # F13-F24; must stay < xkb 256 [I3]
AGENT_KEYSYMS = ("XF86Macro27", "XF86Macro29")   # binding keys [S6, O9]
def keysym_for_char(ch: str) -> str    # " "->space, "\n"->Return, "\t"->Tab, ASCII printable->itself, else "U%04X"
def build_keymap(slots: list[str | None]) -> bytes  # NUL-terminated xkb text; LRU slot rebinding in vinput
def normalize_key(name: str) -> str    # tool-level names -> keysym: "enter"->"Return", "esc"->"Escape", "backspace"->"BackSpace", "pageup"->"Prior", "pagedown"->"Next", "del"->"Delete"; single char -> itself; otherwise unchanged
```

**Rules**

- Copy `NAMED` from the spike and extend it with `F13`–`F24` (evdev 183–194),
  `Print` (99), `Pause` (119), `Scroll_Lock` (70), `Caps_Lock` (58),
  `Num_Lock` (69), `KP_Enter` (96).
- The keymap includes `evdev` keycodes and `pc+us+inet(evdev)` symbols, then
  appends `<Pn> = PRIVATE_BASE+n+8` with `key <Pn> {[ <keysym> ]};` for each
  private keysym (exact template in the spike).

**Tests** (`tests/test_keymap.py`):

- every printable ASCII character maps (95 chars)
- `A` needs shift and `a` does not
- `build_keymap([])` contains `include "evdev"`
- private keysyms get consecutive codes
- `normalize_key` table

---

## 1.5 `src/computer_use_sway/vinput.py` — CREATE (≈330 lines)

Port `VirtualPointer` and `VirtualKeyboard` from `spikes/wl.py` (the final
versions, including `set_mods`).

**Public API**

```python
BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}
class AgentSeatLost(core.ToolError): ...

class VirtualPointer:
    def __init__(self, conn: wire.Connection, seat_obj: int, gate=None) -> None
    def move_to(self, x: int, y: int) -> None               # absolute; extent from desktop.layout_extent()
    def button(self, name: str, pressed: bool) -> None
    def click(self, x: int, y: int, button: str = "left", count: int = 1, interval_ms: int = 80) -> None
    def drag(self, start, end, button="left", steps=12, duration_ms=350) -> None
    def scroll(self, direction: str, clicks: int) -> None   # up/down/left/right -> axis 0/1, discrete ±1 per click
    def destroy(self) -> None

class VirtualKeyboard:
    def __init__(self, conn: wire.Connection, seat_obj: int, gate=None) -> None   # uploads keymap with AGENT_KEYSYMS
    def type_text(self, text: str, delay_ms: int = 0) -> None
    def chord(self, key: str, modifiers: list[str] | None = None) -> None   # key is a keysym or normalize_key() name
    def destroy(self) -> None

class SeatDevices:
    """One pointer + one keyboard on one named seat, recreated on demand."""
    def __init__(self, conn: wire.Connection, seat_name: str, gate=None) -> None
    pointer: VirtualPointer
    keyboard: VirtualKeyboard
    def recreate(self) -> None     # destroy both, conn.wait_seat(name), create both, sleep 0.3 s [S8]
    def destroy(self) -> None
```

**Rules**

1. `gate` is a zero-argument callable or None. If given, it is called before
   **every** protocol event a device sends (motion, button, axis, key,
   modifiers). If it raises `AgentSeatLost`, nothing is sent. `agent.py`
   provides the gate (PLAN 2.2).
2. Pointer motion: `motion_absolute(time, x, y, ext_w, ext_h)` then `frame`.
   Flush with `conn.roundtrip()` after each logical step: one click, one drag
   step, one scroll click.
3. `click`: move, sleep 50 ms, press, sleep 30 ms, release, then repeat for
   `count` with `interval_ms` between clicks (values from spike1_seats_lib).
4. `drag` [I2]:
   1. Move to start; sleep 50 ms.
   2. Press; sleep 50 ms.
   3. Make `steps` absolute moves along the line, with
      `min(20 ms, duration/steps)` pauses.
   4. Sleep 100 ms; release; sleep 100 ms.

   No warm-up jiggle is needed: absolute motion positions the device.
5. `scroll`: send `axis_source(wheel=0)`, then
   `axis_discrete(time, axis, fixed(15*n), n)`, then `frame`, per click.
6. Keyboard typing [I3, I4]:
   - A char in `US` → `chord_code(code, ["shift"] if shifted else [])`.
   - `\n` → `Return`; `\t` → `Tab`.
   - Anything else → private keysym `U%04X`. Re-upload the keymap only when a
     new private keysym appears; the private list is append-only for the
     device's lifetime.
7. `chord_code`:
   1. Press the modifier keys.
   2. Send `modifiers(mask)`.
   3. Press the key, roundtrip, sleep 12 ms.
   4. Release the key.
   5. Release the modifiers, send `modifiers(0)`, roundtrip.

   **Never skip the `modifiers` request** [I4].
8. Limits: `type_text` refuses NUL and more than `core.TEXT_LIMIT` characters
   (existing constants).
9. The pause after device creation is 0.3 s (measured: earlier events can be
   dropped).

**Tests** (`tests/test_vinput.py`), with a fake `Connection` that records
`(obj, opcode, payload)`:

- click sends motion_absolute+frame, then button press/release with the right codes
- `type_text("A")` sends shift press, modifiers(1), key, modifiers(0)
- `type_text("é")` triggers exactly one keymap upload with `U00E9`
- a gate that raises stops before any send
- scroll encoding

---

## 1.6 `src/computer_use_sway/tools.py` → split — MODIFY

Low-level input tools move to **`tools_input.py`** (CREATE, ≈330 lines):
`focus_window, move_pointer, click, drag, scroll, type_text, key, clipboard_set`.
Observation tools move to **`tools_observe.py`** (CREATE):
`screen_info, screenshot, window_tree, clipboard_get` (+ `observe`, `read` in
PLAN 3). `tools.py` keeps only the `TOOLS` registry, built from the family
modules. In this phase the new tools all run on **seat0** through
`session.user_devices()`. Until PLAN 2 exists, that is a tiny module-level
lazy `SeatDevices(conn, desktop.detect_seat())`.

- `move_pointer(x, y, mode)`: `mode=set` → `pointer.move_to`. `mode=move` is
  relative to the current position, which the server only knows when it put
  the pointer there itself. Keep a `last_position` per `SeatDevices`. For an
  unknown origin, fall back to IPC `seat <s> cursor move dx dy`, which is
  still valid for plain motion. (Warp limitations only matter under a held
  button.)
- `click`: `pointer.click(...)`. With no x/y, click at `last_position`, or
  raise `ToolError("click without x,y needs a known pointer position; pass x and y")`.
- `drag`: `pointer.drag(...)`. Delete `pointer.py` and `tests/test_pointer.py`.
- `scroll`: `pointer.scroll`.
- `type_text`: `keyboard.type_text`. Keep `delay_ms` semantics.
- `key`: `keyboard.chord(normalize_key(key), modifiers)`.
- `focus_window`: unchanged (IPC focus acts on seat0 [S4]).
- `clipboard_*`: unchanged here (the `seat` parameter arrives in PLAN 2).
- Remove `wtype` from every `require_binaries` list. Remove `swaymsg` the same way.

Keep return payload keys backward compatible (`clicked`, `dragged`, …).
PLAN 4 wraps them with verdicts.

---

## 1.7 `src/computer_use_sway/dbus.py` — CREATE (≈340 lines)

Port from `spikes/dbus.py`.

**Additions over the spike**

1. `Bus.__init__` raises `ToolError("cannot connect to D-Bus at …")` on
   connect/auth failure.
2. Bounds checks in `Reader`: raise `DBusError("org.freedesktop.DBus.Error.InvalidArgs", "truncated message")` on overrun.
3. Serial wraps at 2³²−1 back to 1.
4. `call()` keeps signals that arrive while waiting in `self.signals`, a
   deque of 2048 entries.
5. `signals_since(t: float, sender: str | None = None) -> list` and `clear_signals()`.
6. Messages larger than 128 MiB are refused (spec limit).
7. Signatures supported: `y b n q i u x t d s o g v a ( ) { }` and `h`. Unix
   fds are refused with a `DBusError`; AT-SPI does not use them.

**Tests** (`tests/test_dbus.py`):

- marshal/unmarshal round trip for `s`, `o`, `g`, `v(s)`, `a{sv}`,
  `a(so)`, `(iiii)`, nested arrays
- header parse of a canned `METHOD_RETURN`
- big-endian message decode
- `split_signature` on `a{s(ii)}iv`
- error reply → `DBusError` name

---

## 1.8 `src/computer_use_sway/core.py` — MODIFY

- Delete `BUTTONS`, `SCROLL_BUTTONS`, `MODIFIERS` (moved to `vinput`/`keymap`).
- Delete `read_json_command` once nothing imports it.
- Add:

```python
@dataclass
class Result:
    content: list[dict]
    meta: dict            # curated, timeline-safe values only (PLAN 5 §5.2)

def text_result(value: Any, meta: dict | None = None) -> Result
```

  `json_text()` stays for tools that have no meta.

---

## 1.9 `tests/test_pointer.py` — DELETE

## 1.10 `src/computer_use_sway/pointer.py` — DELETE

Make sure `server.py`'s star import of `.pointer` is removed and `self_test()`
drops the `virtual_pointer` (/dev/uinput) section. Doctor reports
`zwlr_virtual_pointer_manager_v1` and `zwp_virtual_keyboard_manager_v1`
availability from `wire.Connection().globals` instead (PLAN 5 §5.4).

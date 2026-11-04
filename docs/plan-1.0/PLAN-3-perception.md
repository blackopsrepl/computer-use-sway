# PLAN 3 — Perception: accessibility, geometry, OCR, snapshots

Phase goal: `observe` returns a compact, ref-indexed map of what is on screen,
from structure first and pixels last, with layout coordinates that hit their
targets at any scale, with or without CSD.

Exit criteria:

- `make check` green.
- Integration `test_a11y_geometry_matrix` (the 12-case spike4 matrix, through
  `scene.observe` + `act(click)` on the `Hit` element) and
  `test_ocr_ensemble` (10 cases) pass.

---

## 3.1 `src/computer_use_sway/atspi.py` — CREATE (≈420 lines)

Port from `spikes/atspi.py`; extend with the pieces below.

**Public API**

```python
IFACE = {"acc": "org.a11y.atspi.Accessible", "comp": "...Component", "action": "...Action",
         "text": "...Text", "etext": "...EditableText", "value": "...Value", "app": "...Application",
         "cache": "...Cache", "sel": "...Selection"}
COORD_WINDOW = 1          # never use SCREEN [A5]
STATES: list[str]          # copy from spike (bit order matters)

Ref = tuple[str, str]      # (bus_name, object_path)

@dataclass
class Node:
    ref: Ref; role: str; name: str; states: set[str]; ifaces: set[str]
    extents: tuple[int, int, int, int] | None   # WINDOW coords, None if no Component / invalid
    depth: int; parent: Ref | None

class Client:
    def __init__(self, session_bus_address: str | None = None) -> None
    def available(self) -> bool
    def ensure_enabled(self) -> dict        # set org.a11y.Status IsEnabled/ScreenReaderEnabled true if false; remember to restore
    def restore_enabled(self) -> None
    def apps(self) -> list[Ref]
    def app_pid(self, app: Ref) -> int | None
    def app_for_window(self, pid: int) -> Ref | None      # exact pid, else ancestor/descendant match [A2]
    def toolkit(self, app: Ref) -> str | None            # Application.ToolkitName
    def frame_for_window(self, app: Ref, title: str) -> Ref | None   # child of app with role frame/window; prefer name==title, then "showing"+"active"
    def walk(self, root: Ref, max_nodes: int = 4000, prune_hidden: bool = True) -> list[Node]
    def node(self, ref: Ref) -> Node                     # fresh read (role, name, states, ifaces, extents)
    def children(self, ref: Ref) -> list[Ref]
    def actions(self, ref: Ref) -> list[str]             # GetName(i) for i in range(max(NActions, probe)) [A6 Gecko]
    def do_action(self, ref: Ref, name_or_index) -> bool
    def grab_focus(self, ref: Ref) -> bool
    def text(self, ref: Ref, start: int = 0, end: int = -1) -> str
    def set_text(self, ref: Ref, value: str) -> bool     # SetTextContents; UnknownMethod -> ToolError code "no_editable_text"
    def value(self, ref: Ref) -> float | None
    def set_value(self, ref: Ref, v: float) -> None       # Properties.Set Value.CurrentValue
    def register_events(self) -> None                    # object:text-changed, object:state-changed, object:property-change, object:children-changed, window:activate
    def events_since(self, t: float, sender: str | None = None) -> list[tuple[float, str, str]]  # (t, member, detail)
```

**Rules**

1. Connection: the session bus comes from `DBUS_SESSION_BUS_ADDRESS`
   (session/sandbox env). The a11y bus address comes from
   `org.a11y.Bus.GetAddress`. When the a11y bus is unavailable,
   `available() == False` and every other call raises
   `ToolError("accessibility bus unavailable: …")`.
2. `walk()`:
   - DFS with an explicit stack.
   - Per node, read role (`GetRoleName`), name (Property `Name`), states
     (`GetState` → `STATES`), ifaces (`GetInterfaces`) and, if the node has
     Component, `GetExtents(COORD_WINDOW)`.
   - `prune_hidden`: do not descend into children of nodes at depth > 1 that
     lack `showing` (frames are kept).
   - Skip nodes whose extents are `(-1,-1,-1,-1)` or contain −2³¹ (invisible
     widgets) when computing visibility, but still descend if they are
     `showing` (Firefox's "Menu Bar" toolbar reports −1 extents and holds
     showing children).
   - Swallow `DBusError` per node (stale objects disappear mid-walk) and continue.
3. `actions()`: Gecko reports `NActions = 0` but `GetName(0) == "press"` [A6].
   Read `NActions`; if it is 0, probe `GetName(0)` and include the name when
   it is non-empty.
4. `do_action(name_or_index)`: a name is matched case-insensitively against
   `actions()`. The synonyms `press ↔ click ↔ activate ↔ Click ↔ Press` are
   tried in that order.
5. Timeouts: 2 s per call. A walk is bounded by `max_nodes`, plus a 3 s
   wall-clock budget that truncates (report `truncated: true`).
6. Never cache `Node` objects across `observe` calls except through
   `refs.py`, which re-validates them.

**Tests** (`tests/test_atspi.py`, fake Bus that answers a canned tree):

- state bit decoding
- Gecko zero-NActions probe
- action synonym resolution
- prune behaviour (hidden subtree skipped, −1 extents subtree with showing
  children descended)
- `app_for_window` ancestor match

---

## 3.2 `src/computer_use_sway/toolkits.py` — CREATE (≈160 lines)

Capabilities measured in FINDINGS, as data. Never infer them from version strings.

```python
@dataclass(frozen=True)
class Profile:
    name: str
    multi_seat: bool | None          # raw input from a second seat accepted [I5]
    editable_text: str               # "reliable" | "lies" | "absent"  [A7]
    device_pixel_extents: bool       # divide extents by output scale [A4b]
    paste_follows_seat: bool | None  # [C2]
    launch_flags: tuple[str, ...]    # added by launch for a11y [A9]
    a11y_by_bus_status: bool         # honours org.a11y.Status [A9]

PROFILES = {
  "gtk3":     Profile("gtk3", True,  "reliable", False, True,  (), True),
  "gtk4":     Profile("gtk4", True,  "reliable", False, False, (), True),
  "qt":       Profile("qt",   True,  "reliable", False, False, (), True),
  "gecko":    Profile("gecko",True,  "lies",     False, True,  (), True),
  "chromium": Profile("chromium", False, "absent", True, None, ("--force-renderer-accessibility",), False),
  "terminal": Profile("terminal", False, "absent", False, None, (), False),   # kitty measured; foot/alacritty/wezterm assumed until measured
  "unknown":  Profile("unknown", None, "absent", False, None, (), False),
}

def classify(toolkit_name: str | None, app_id: str | None, argv0: str | None = None) -> Profile
class Learned:  # per-session overrides keyed by app_id
    def mark_single_seat(self, app_id: str) -> None
    def profile(self, base: Profile, app_id: str | None) -> Profile
```

**`classify` rules (in order)**

1. AT-SPI `ToolkitName`:
   - `"gtk"` → GTK. Take the version from the `Application.Version`
     property's major: 3 → gtk3, 4 → gtk4. If the version is unavailable,
     `"GTK"` (uppercase, as GTK4 reports) → gtk4 and `"gtk"` → gtk3.
     Measured: GTK3 reported `gtk`, GTK4 reported `GTK`.
   - `"Qt"` → qt; `"Gecko"` → gecko; `"Chromium"` → chromium.
2. No a11y:
   - app_id in {`kitty`, `foot`, `footclient`, `Alacritty`,
     `org.wezfurlong.wezterm`, `com.mitchellh.ghostty`} → terminal
   - app_id or argv0 containing `chrom`, `electron`, or a known Electron app
     id (`md.obsidian.Obsidian`, `discord`, `com.nousresearch.hermes`,
     `code`, `Slack`) → chromium
3. Otherwise unknown.

**Tests**: table-driven classify cases; `Learned` override.

---

## 3.3 `src/computer_use_sway/geometry.py` — CREATE (≈170 lines)

```python
@dataclass
class WindowGeo:
    origin: tuple[int, int]     # sway rect.xy + window_rect.xy (layout points)
    size: tuple[int, int]       # window_rect w,h
    output_scale: float
    frame_extents: tuple[int, int, int, int]   # a11y frame WINDOW extents
    csd_origin: tuple[int, int]                # computed by csd_origin()

def csd_origin(frame_extents, child_boxes, window_rect_size) -> tuple[int, int]
def a11y_to_layout(geo: WindowGeo, ext, device_pixels: bool) -> tuple[int, int, int, int]
def center(box) -> tuple[int, int]
def ocr_to_layout(window_origin, box_px, image_scale: int = 2) -> tuple[int, int, int, int]
def output_scale_at(outputs, x, y) -> float
```

**Formulas** [A4] (copy the spike's `f3`, restated):

```
if device_pixels: ext = round(ext / output_scale)          # every component
lx = origin.x + ext.x - frame_extents.x - csd_origin.x
ly = origin.y + ext.y - frame_extents.y - csd_origin.y
```

`csd_origin` = (0,0) when `frame_extents.size == window_rect.size`.
Otherwise it is the min x and min y over `child_boxes`, ignoring boxes with
any coordinate ≤ −10000 or with w ≤ 1 or h ≤ 1. With no valid boxes it is
`((fw − ww)//2, (fh − wh)//2)`.

**Unit tests (exact inputs and click points measured by `spikes/spike4_matrix.py`; every one was a verified hit; do not change them):**

| case | origin = rect.xy + window_rect.xy | frame ext (WINDOW) | window_rect size | csd_origin | element ext | device px / scale | expected center |
|---|---|---|---|---|---|---|---|
| gtk3 tiled s1 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (120,248,1036,34) | no | (640,291) |
| gtk3-csd floating s1 | (150,50) | (0,0,952,752) | 900×700 | (26,23) from children | (146,318,660,34) | no | (600,362) |
| gtk4 tiled s1/s2 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (120,248,1036,34) | no | (640,291) |
| qt6 tiled s1/s2 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (120,244,80,32) | no | (162,286) |
| firefox tiled s1 | (2,26) | (20,20,1276,772) | 1276×772 | (0,0) | (60,345,200,80) | no | (142,391) |
| firefox tiled s2 | (2,26) | (20,20,1276,772) | 1276×772 | (0,0) | (60,344,200,80) | no | (142,390) |
| chromium s1 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (40,240,200,80) | no | (142,306) |
| chromium s2 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (80,480,400,160) | yes, 2.0 | (142,306) |
| electron s1 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (40,268,200,80) | no | (142,334) |
| electron s2 | (2,26) | (0,0,1276,772) | 1276×772 | (0,0) | (80,536,400,160) | yes, 2.0 | (142,334) |

The gtk3-csd tiled case (measured earlier: frame 1316×812 vs window 1276×772,
children origin (20,20), element (140,251,1036,34), origin (2,26) → (640,274))
covers the tiled CSD branch. Add a `csd_origin` test using its child boxes
`[(20,20,1276,47), (140,187,1036,485)]` → `(20,20)`, and one with only
invalid boxes `[(-2147483648,-2147483648,1,1)]` → falls back to
`((1316−1276)//2, (812−772)//2) = (20,20)`.

---

## 3.4 `src/computer_use_sway/capture.py` — CREATE (≈190 lines)

```python
def png(region: dict | None = None, output: str | None = None, cursor: bool = False) -> bytes
def ppm2x(region: dict) -> bytes                 # grim -t ppm -s 2 -g "x,y wxh" -   [P1]
def region_hash(region: dict) -> str             # blake2b-128 hex of grim -t ppm -s 1 (cheap)
def settle(region: dict, interval_ms: int = 60, max_ms: int = 600) -> tuple[str, bool]  # (last hash, stable?)
def window_region(win: dict) -> dict             # rect.xy + window_rect.xy, window_rect.wh
```

**Rules**

- `grim` is missing → `ToolError("grim is required for screenshots, OCR and pixel verification (install grim)")`.
- Regions must lie on one output. Validate with `desktop.validate_region`
  (it already accepts any active output, including the agent output).
- `settle`: hash every `interval_ms` until two consecutive hashes are equal
  or `max_ms` elapses.

---

## 3.5 `src/computer_use_sway/ocr.py` — CREATE (≈220 lines)

Port from `spikes/ocr.py` + `spikes/pgm.py` (`parse_ppm` only; drop the PNG decoder).

```python
@dataclass
class Word: text: str; box_px: tuple[int, int, int, int]; conf: float; variant: str
@dataclass
class Line: text: str; box_px: tuple[int, int, int, int]; words: list[Word]

def available() -> bool                       # shutil.which("tesseract")
def recognize(ppm: bytes, psm: str = "11") -> list[Word]   # the 4-variant ensemble [P2]
def lines(words: list[Word]) -> list[Line]
def find(lines: list[Line], text: str, match: str = "contains") -> list[tuple[Line, tuple]]   # returns the matched sub-box
```

**Rules**

1. Variants: `raw` (the PPM itself), `gray` (green channel → P5),
   `gray_inv`, `mid` (green binarized at the (min+max)/2 with minority→black).
   All use `--psm 11 -c thresholding_method=2`. Run them in parallel with
   `subprocess.Popen` and a 20 s timeout each. Temp files go under
   `tempfile.mkdtemp(dir=$TMPDIR or $XDG_RUNTIME_DIR/computer-use-sway)` and
   are removed in `finally`.
2. Keep words with conf ≥ 50. Dedupe: same text and center inside a kept
   box; keep the highest conf.
3. `lines()`:
   1. Sort words by (y-center, x).
   2. Join a word to the current line when its y-center is within 0.6 ×
      the line's median height and its x gap ≤ 1.5 × the median height.
   3. Line text is the words joined by spaces.
4. `find()` matches the query against line text (case-insensitive for
   `contains`/`exact`; `regex` is case-sensitive). For a match inside a line,
   the box is the union of the boxes of the words that make up the matched
   span. Compute it by word-index alignment: the matched character range →
   word indices.
5. Missing tesseract → `ToolError("OCR needs tesseract (install tesseract and an English traineddata)")`.

**Tests** (`tests/test_ocr.py`):

- variant transforms on a 4×2 synthetic PPM (exact bytes)
- dedupe
- `lines()` grouping
- `find()` multi-word span box (e.g. "Save as" inside "File Save as Export")

Tesseract itself is only exercised in integration.

---

## 3.6 `src/computer_use_sway/query.py` — CREATE (≈140 lines)

```python
ROLE_SYNONYMS = {"button": {"push button", "button", "toggle button"},
                 "entry": {"entry", "text", "text box", "password text", "editbar", "terminal"},
                 "checkbox": {"check box", "checkbox"}, "radio": {"radio button"},
                 "link": {"link"}, "menuitem": {"menu item", "check menu item", "radio menu item"},
                 "tab": {"page tab"}, "combobox": {"combo box"}, "slider": {"slider", "scroll bar", "spin button"},
                 "list": {"list", "list box", "tree table", "table"}, "item": {"list item", "table cell", "tree item"}}
@dataclass
class Query: role: str | None; name: str | None; text: str | None; match: str; window: dict | None; nth: int
def parse(arg: dict) -> Query           # strict validation -> ToolError
def role_matches(q_role, node_role) -> bool
def name_matches(q, value: str | None) -> bool
```

`match` ∈ `exact|contains|regex`, default `contains` (case-insensitive).
`nth` is 0-based, default 0. `window` is a selector
`{con_id}|{app_id}|{title}` with the same matching rules as `focus_window`.

---

## 3.7 `src/computer_use_sway/refs.py` — CREATE (≈190 lines)

```python
@dataclass
class Element:
    ref: str; source: str          # "a11y" | "ocr"
    con_id: int; role: str; name: str; states: list[str]; actions: list[str]
    bounds: tuple[int, int, int, int]   # layout points
    preview: str | None
    a11y: atspi.Ref | None            # for source a11y
    region_hash: str | None           # for source ocr: window hash at capture

class RefStore:
    def new_snapshot(self) -> int     # returns N; keeps last 8 snapshots
    def add(self, snap: int, el: Element) -> str     # assigns f"s{snap}:{k}"
    def get(self, ref: str) -> Element               # ToolError("unknown_ref …") if evicted/unknown
    def validate(self, ref: str, client, capture) -> Element   # rules below; returns a refreshed Element (bounds re-read)
```

**`validate`**:

- a11y: `client.node(el.a11y)` must succeed with the same role and name and
  `showing` in states. Re-read extents and recompute bounds through
  `geometry` (the window may have moved). Failure →
  `ToolError("stale_ref s4:12: element changed or disappeared; call observe again")`.
- ocr: `capture.region_hash(window)` must equal `el.region_hash`, else the
  same stale error.

---

## 3.8 `src/computer_use_sway/scene.py` — CREATE (≈430 lines)

```python
def observe(session, scope: str = "agent", window: dict | None = None, query: dict | None = None,
            max_elements: int = 150, ocr: str = "auto", image: bool = False) -> core.Result
```

**Scope**

- `agent`: windows on workspace `cus-agent`
- `focused`: the window focused by the default seat
- `visible`: all windows whose workspace is visible on any output (the user's
  screen plus agent)
- `window`: exactly the `window` selector

**Per window**

1. Read the Sway node; resolve the a11y app with `client.app_for_window(pid)`
   and the frame with `frame_for_window(app, title)`.
2. Profile: `toolkits.classify(...)` + learned overrides.
3. Walk the frame (pruned) and keep the elements defined in ARCHITECTURE §5.
4. Bounds via `geometry.a11y_to_layout`. `csd_origin` uses the frame's direct
   children from the walk (no extra calls).
5. OCR when `ocr == "always"`, or when (`ocr == "auto"` and fewer than 3 kept
   a11y elements), or when `query.text` found no a11y match. OCR lines become
   elements with role `text` and `source ocr`.
6. Chromium-family window with a11y absent: add the window note
   `a11y: off — relaunch with --force-renderer-accessibility (launch does this automatically)`.
7. With `query`: filter to the matching elements only, but always include the
   window header lines.

**Output text format** (exact; one line per element; ≤ 120 chars, the name
truncated with `…`):

```
snapshot s7 · mode seat · agent workspace cus-agent (HEADLESS-2)
window 41 "Settings" app=org.gnome.Settings toolkit=gtk4 seats=agent+user ws=cus-agent @100002,26 1916x1052 a11y=on
  s7:1 button "Back" [focusable] @100014,38 34x34 actions=press
  s7:2 entry "Search" [focused,editable] @100060,38 300x34 value=""
  s7:3 text "Wi-Fi" @100020,120 60x18 (ocr)
```

`seats=agent+user` means the profile has multi_seat True; `user only` means
False; `unknown` means None.

`Result.meta`: `{"scope", "windows": n, "elements": n, "ocr": bool,
"truncated": bool}` for the timeline. With `image: true`, append PNG image
content of the scoped windows: one crop per window, or the agent output for
scope `agent` (via `capture.png(region)`).

**Tests** (`tests/test_scene.py`, fakes for client/capture/ocr):

- the element filter
- the rendering format, byte-exact for a canned window
- the query filter keeps headers
- OCR auto trigger at < 3 elements
- the Chromium a11y-off note

---

## 3.9 `tools_observe.py` additions — MODIFY

- `observe` → `scene.observe(session.current(), **validated args)`.
- `read(ref, start=0, length=4000)` → the full text (`Text` iface) or value
  of an element, plus states. For OCR refs: the line text.
- `screenshot` gains `window: {con_id|app_id|title}` (crop to `window_region`)
  and `output: "agent"` (alias for the agent output name). The existing
  `output` parameter is the MCP content mode, so name the new parameter
  **`target_output`** to avoid the clash, and document it. `save_path`
  behaviour is unchanged.
- `screen_info` adds `agent: session.status()["agent"]` and
  `mode: session.mode`.

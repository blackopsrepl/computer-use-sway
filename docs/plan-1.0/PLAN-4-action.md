# PLAN 4 — Action: delivery ladder, verdicts, launch, waits, plans

Phase goal: `act`, `launch`, `wait_for` and `run`. Every input reports a
verdict backed by evidence. The agent climbs from semantic actions to its own
seat to a guarded foreground step, and never disturbs the user silently.

Exit criteria:

- `make check` green.
- Integration passes `test_act_ladder`, `test_launch`, `test_verdicts`,
  `test_run_form`, `test_foreground_single_seat` and `test_user_untouched`
  (PLAN-5 §T4).

---

## 4.1 `src/computer_use_sway/verify.py` — CREATE (≈280 lines)

```python
EFFECTS = ("confirmed", "changed", "no_change", "unverifiable")

@dataclass
class Expectation:
    text: str | None = None          # target text must equal this after
    state_on: set[str] = field(default_factory=set)    # e.g. {"checked"} / {"focused"}
    state_off: set[str] = field(default_factory=set)

@dataclass
class Baseline:
    t: float; con_id: int | None; title: str | None
    seat_focus: dict[str, int | None]
    a11y_text: str | None; a11y_states: set[str] | None
    region: dict | None; region_hash: str | None

def take_baseline(session, element: refs.Element | None, con_id: int | None, want_pixels: bool) -> Baseline
def verdict(session, base: Baseline, element: refs.Element | None, expect: Expectation | None,
            settle_ms: int) -> dict
```

**`verdict()` algorithm** (stop at the first rule that holds):

1. **Readback** (a11y element and `expect` given): re-read the element's
   text/states (`atspi.node`, `atspi.text`).
   - The `expect.text` equality holds and every `state_on`/`state_off` holds
     → `confirmed`, evidence `readback:text` / `readback:state:<s>`.
   - `expect.text` was given and does **not** hold → remember
     `readback_failed` (it is decisive for Gecko's lying SetTextContents [A7])
     and continue at rule 2. The final effect can then be at most `changed`.
2. **AT-SPI events** since `base.t` from the element's app bus name:
   `object:text-changed`, `state-changed`, `property-change` (accessible-name
   / value), `children-changed`. Any present → `changed`, evidence
   `atspi:<member>`.
3. **Sway events** since `base.t` with `container.id == base.con_id`
   (`title`, `focus`, `urgent`, `new`, `close`, `move`), or a `window::new`
   from the same pid → `changed`, evidence `sway:<change>`.
4. **Pixels** (only when `base.region_hash` exists): `capture.settle(region,
   60, settle_ms)`. Hash differs from the baseline → `changed`, evidence
   `pixels`.
5. A region was capturable → `no_change`.
6. Otherwise → `unverifiable`.

Waiting: rules 2–3 read from logs that fill asynchronously. Before evaluating
them, wait until the first event arrives or `settle_ms` elapses. For a11y,
pump `atspi.Client` signals in 50 ms slices; for Sway, use `EventLog.wait_for`.

`verdict()` returns
`{"effect", "evidence": [...], "elapsed_ms", "readback_failed": bool}`.
`actions.py` adds `delivery`, `escalations`, `disturbed_user`.

**Tests** (`tests/test_verify.py`), fake session with injectable logs/hashes:

- each rule in order
- readback_failed caps the effect at `changed`
- no region and no events → `unverifiable`
- the settle wait returns early on the first event

---

## 4.2 `src/computer_use_sway/actions.py` — CREATE (≈440 lines; split `actions_foreground.py` out if it grows)

```python
ACTIONS = ("press", "activate", "toggle", "focus", "set_text", "type", "key", "click",
           "double_click", "right_click", "scroll", "drag_to", "set_value", "select")

def act(session, target: dict, action: str, text: str | None = None, key: str | None = None,
        modifiers: list[str] | None = None, value: float | None = None, direction: str | None = None,
        clicks: int = 3, to: dict | None = None, delivery: str = "auto", verify: bool = True) -> core.Result
```

**Target resolution** (`resolve_target(session, target) -> (element | None, con_id, point)`):

- `{"ref": "s7:3"}` → `refs.validate`.
- `{"query": {...}}` → run `scene.observe(scope="visible", query=…)`
  internally, but without rendering (use `scene.collect`, the internal
  function that returns elements). Take `nth`. No match →
  `ToolError("no element matches …")`.
- `{"x": int, "y": int}` → no element; con_id = the window under the point
  (search the Sway tree for a visible window whose content rect contains the
  point); the point is the target.

**Rung table** (rung 1 = semantic, 2 = agent device, 3 = foreground):

| action | rung 1 (needs a11y element) | rung 2/3 device form | expectation |
|---|---|---|---|
| press / activate | `do_action("press")` (synonyms) | click at center | none (rules 2–5) |
| toggle | `do_action("toggle"\|"click")` | click at center | `checked` flips |
| focus | `grab_focus` | click at center | `state_on={"focused"}` |
| set_text | `set_text(text)` (profile `editable_text == "reliable"`; for `lies`, try it and require readback) | focus element, `ctrl+a`, `BackSpace`, type text | `text == text` |
| type | – | focus element (if any), type text | text endswith/contains the typed text when readable |
| key | – | chord | none |
| click / double_click / right_click | – | pointer at center/point | none |
| scroll | – | pointer scroll at center/point | pixels |
| drag_to | – | pointer drag from center/point to `to` (ref/query/point) | pixels |
| set_value | `set_value(value)` | – | value equals |
| select | `do_action("select"\|"activate")` | click | `state_on={"selected"}` |

**Ladder** (`delivery == "auto"`):

1. If rung 1 applies: execute it and take the verdict. On `confirmed` or
   `changed`, return. On `no_change` or an exception (`UnknownMethod` →
   `no_editable_text`), record an escalation and continue.
2. Rung 2 applies unless the session is in `user` mode or the profile's
   `multi_seat is False`:
   1. `agent.ensure_ready()`
   2. `sentinel.snapshot()`
   3. `agent.focus(con_id)` (always, so keyboard input lands in the right
      window)
   4. the device form (pointer on `session.devices("agent")`)
   5. take the verdict
   6. `sentinel.restore(snap)` → `disturbed_user`

   On `no_change` with profile `multi_seat is None`, call
   `learned.mark_single_seat(app_id)` [toolkit cache] and continue.
3. Rung 3 (foreground), allowed when `options.foreground != "never"`:
   1. With `foreground == "idle"`, call `sentinel.wait_user_idle(idle_ms,
      idle_max_wait_ms)`. If the user stays active, return the last verdict
      with `blocked: "user_active"` (not an error; the caller decides).
   2. `snap = sentinel.snapshot()`
   3. IPC `[con_id=C] focus`
   4. if element: `grab_focus` [A8]
   5. keyboard forms: `session.user_devices().keyboard`. Pointer forms
      require `options.foreground_pointer`, else return
      `blocked: "foreground_pointer_disabled"` with a hint.
   6. take the verdict
   7. **always** (in `finally`): IPC `[con_id=snap.focus] focus` [F1]
   8. `disturbed_user = True` (it was a visible focus change, however brief)

In `user` mode, rung 2 uses seat0 directly (`session.user_devices()`) with no
focus binding (IPC focus first), and rung 3 does not exist.

`delivery` = `semantic` | `agent` | `foreground` pins one rung, with no
fallback. `verify: false` skips verdicts (effect `unverifiable`, evidence
`["verification disabled"]`).

**`AgentSeatLost` handling**: catch it once per rung, call
`agent.ensure_ready()`, and retry the rung if no event had been sent. Typing
reports `partial: true, characters_sent: n` and does not retry.

**Result**: the text is a JSON object with
`{target: {ref|query|point, con_id, role, name}, action, effect, evidence,
delivery, escalations: [{rung, effect|error}], disturbed_user, blocked?,
partial?, elapsed_ms}`. Meta (timeline): `action`, `ref`, `delivery`,
`effect`, `disturbed_user`, and `characters` for text actions. Never include
`text` itself.

**Tests** (`tests/test_actions.py`, fake session):

- for each action, the rung-1 call and the expectation mapping
- ladder escalation on `no_change`
- multi_seat False skips rung 2
- foreground blocked when the user is active
- focus restore in `finally` even when typing raises
- `user` mode path
- `AgentSeatLost` retry path
- meta has no text

---

## 4.3 `src/computer_use_sway/launch.py` — CREATE (≈220 lines)

```python
def launch(session, argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None,
           timeout_ms: int = 20000, a11y: bool = True) -> core.Result
```

**Seat/sandbox mode** [O5, O6, O9, O10]:

1. Validate: `argv` is a non-empty list of strings; `argv[0]` resolves via
   `shutil.which` or is an absolute executable; `env` keys match `[A-Z_][A-Z0-9_]*`.
2. Profile guess: `toolkits.classify(None, None, argv0=basename)`. If the
   profile has `launch_flags` and `a11y` is true, append each flag not
   already present in `argv`. Chromium/Electron get
   `--force-renderer-accessibility` [A9].
3. Write `install.launcher_path()` (0700):

   ```sh
   #!/bin/sh
   cd <shlex.quote(cwd)> || exit 1          # only if cwd
   export K=<shlex.quote(v)> ...            # only if env
   exec <shlex.join(argv)>
   ```

   Keep the activation token sway passes to the exec: Qt and Chromium
   place their first window by it, and without it they map on the user's
   workspace and take seat0 focus [O11].
4. `before = set(con ids)`; `t0 = monotonic()`
5. `agent.ensure_ready()`; `agent.to_workspace()`; `agent.tap(LAUNCH_KEY)`
   (the tap includes `nop`).
6. Wait with `EventLog.wait_for` for a `window::new` whose container is on
   workspace `cus-agent` and whose pid is the launcher's descendant or any
   new pid. The launcher pid is unknown, because sway spawns it, so accept
   any new window on `cus-agent` after `t0` whose con_id is not in `before`.
   Also poll the tree every 250 ms as a fallback. Timeout → `ToolError`
   listing the windows that did appear elsewhere, plus the log hint.
7. Wait until the new window has a non-empty title or 1.5 s passes.
   `agent.focus(new_con)`.
8. Overwrite `next.sh` with the placeholder (`exit 0`) so a stray tap does
   nothing.
9. Seat0 check (a snapshot taken in step 4): restore if it moved, and report.
10. Return the window (`simplify_window`), the profile, `a11y` (whether
    `app_for_window` resolves within 3 s), and the `seats` capability.

**User mode**: `subprocess.Popen(argv, start_new_session=True, env=…)` with
the token variables removed, then wait for any new window. No placement.

Meta: `{"program": basename(argv[0])}` only.

**Tests** (`tests/test_launch.py`):

- script content (quoting with spaces and quotes in args, env export, cwd)
- the a11y flag added once
- placeholder restored after success and after failure
- timeout message

---

## 4.4 `src/computer_use_sway/waits.py` — CREATE (≈220 lines)

```python
def wait_for(session, condition: dict, timeout_ms: int = 10000) -> core.Result
```

Conditions (exactly one key):

- `{"element": {query…}, "state"?: "present"|"absent"|<atspi state>}`: poll
  `scene.collect` every 150 ms, waking early on AT-SPI signals or Sway window
  events.
- `{"window": {"app_id"|"title"|"con_id": …, "match"?}, "event"?: "new"|"close"|"title"|"focus"}`:
  `EventLog.wait_for` against the selector; for `new`, use events after the call started.
- `{"text": "…", "window"?: selector}`: OCR polling every 500 ms (or a11y
  name/text search first, then OCR).
- `{"pixels_stable": selector, "for_ms"?: 300}`: `capture.settle`.
- `{"user_idle_ms": n}`: `sentinel.wait_user_idle`.

Result: `{satisfied, waited_ms, detail}`. Not satisfied → `isError: false`
with `satisfied: false`. A timed-out wait is information, not a failure;
`run` treats it as a failed step. Meta: `{kind, satisfied, waited_ms}`.

---

## 4.5 `src/computer_use_sway/plans.py` — CREATE (≈200 lines)

```python
STEP_TOOLS = {"act", "wait_for", "launch", "observe", "key", "type_text", "focus_window", "assert"}
def run(session, steps: list[dict], stop_on: str = "failure", timeout_ms: int = 120000,
        observe_after: dict | None = None) -> core.Result
```

- Each step is `{"tool": name, "args": {…}}`. Dispatch through the same
  functions the MCP tools use (`tools.TOOLS[name]`), so validation is
  identical. Run `assert` as `wait_for` with `timeout_ms` 0 (an immediate check).
- At most 50 steps, plus a total timeout.
- Failure is any of: `ToolError`; `act` effect `no_change` or `unverifiable`
  when the step has `"require": "changed"` (the default for act is to require
  `changed` or `confirmed`); `wait_for` not satisfied; `blocked` set.
- `stop_on`: `failure` (default) | `never`.
- Each step also publishes its own timeline event, through the same
  `server` publish hook, so seshat sees every action.
- With `observe_after`, run `observe` with those args at the end and append it.

Output: `{"ok", "failed_step": i | None, "steps": [{tool, ok, summary,
effect?}], "observe"?: text}`. Each summary is ≤ 200 chars (truncated
JSON). Meta: `{"steps": n, "tools": [...], "failed_step"}`.

**Tests** (`tests/test_plans.py`, fake TOOLS):

- stop on the first failure
- `never` continues
- the step limit
- unknown tool → error before any step runs
- `require` semantics
- nested `run` refused

---

## 4.6 `tools_semantic.py` — CREATE (≈260 lines)

Thin wrappers that validate arguments (strict types, enums, bounds) and call:

- `session(action: "status"|"start"|"stop", mode?, options?)` →
  `Session.start/stop/status`. In `start`, unknown options are a ToolError.
- `act(...)` → `actions.act`
- `launch(...)` → `launch.launch`
- `wait_for(condition, timeout_ms)` → `waits.wait_for`
- `run(steps, stop_on, timeout_ms, observe_after)` → `plans.run`

Validation bounds:

| argument | bound |
|---|---|
| `timeout_ms` | 0–120000 |
| `clicks` | 1–50 |
| `text` | ≤ `core.TEXT_LIMIT` characters, no NUL |
| `modifiers` | ⊆ {ctrl, shift, alt, logo} |
| `value` | a finite number |
| `delivery` | ∈ {auto, semantic, agent, foreground} |

---

## 4.7 Low-level tools return verdicts — MODIFY `tools_input.py`

`click`, `drag`, `scroll`, `type_text` and `key` wrap their device call:

1. `base = verify.take_baseline(session, None, con_id_under_point_or_focused, want_pixels=True)`
2. the action
3. `verdict(...)`

Results keep the 0.3 keys (`clicked`, `x`, `y`, …) and add `effect`,
`evidence` and `disturbed_user`. A low-level tool never escalates. It reports.

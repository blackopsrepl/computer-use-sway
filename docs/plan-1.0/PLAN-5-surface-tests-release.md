# PLAN 5 — Surface, server, timeline, tests, benchmark, docs, release

---

## 5.1 Specs — SPLIT `specs.py` into `specs_observe.py`, `specs_input.py`, `specs_semantic.py`

`specs.py` keeps `tool_specs()`, which concatenates the three family lists in
this order:

`session, screen_info, observe, read, act, run, wait_for, launch, screenshot,
window_tree, focus_window, move_pointer, click, drag, scroll, type_text, key,
clipboard_set, clipboard_get` (19).

Add MCP annotations (`readOnlyHint` true for `screen_info, observe, read,
screenshot, window_tree, clipboard_get, wait_for`; `destructiveHint` false
everywhere; `openWorldHint` false). The protocol version stays `2024-11-05`
and clients ignore unknown keys, but **verify** with the current Hermes MCP
client that annotations do not break `tools/list` (the integration test T5.1
exercises a real `tools/list` through `hermes mcp`, or skips it with a reason).

Exact argument schemas (types; `additionalProperties: false` everywhere):

| tool | properties (required in **bold**) |
|---|---|
| session | **action** enum[status,start,stop]; mode enum[seat,user,sandbox]; options object{foreground enum[idle,always,never], idle_ms int 0–60000, idle_max_wait_ms int 0–120000, foreground_pointer bool, settle_ms int 0–5000, ocr enum[auto,always,never]} |
| observe | scope enum[agent,focused,visible,window] (default agent in seat mode, focused in user mode); window selector; query object{role str, name str, text str, match enum[exact,contains,regex], nth int≥0}; max_elements int 1–500; ocr enum[auto,always,never]; image bool |
| read | **ref** str; start int≥0; length int 1–100000 |
| act | **target** (oneOf: {ref}, {query}, {x,y}); **action** enum[ACTIONS]; text str; key str; modifiers array enum[ctrl,shift,alt,logo]; value number; direction enum[up,down,left,right]; clicks int 1–50; to (same oneOf as target); delivery enum[auto,semantic,agent,foreground]; verify bool |
| run | **steps** array (1–50) of {**tool** enum[act,wait_for,launch,observe,key,type_text,focus_window,assert], **args** object, require enum[changed,any]}; stop_on enum[failure,never]; timeout_ms int; observe_after object |
| wait_for | **condition** object (exactly one of element/window/text/pixels_stable/user_idle_ms, plus modifiers as in PLAN-4 §4.4); timeout_ms int 0–120000 |
| launch | **argv** array[str] (1–64); cwd str; env object<str,str>; timeout_ms int; a11y bool |
| screenshot | include_cursor bool; output enum[image,data_url,both]; save_path str; region object; window selector; target_output str |
| focus_window | con_id/app_id/class/title (exactly one); match; seat enum[agent,user]; move_to_agent bool |
| move_pointer / click / drag / scroll / type_text / key | the 0.3 properties + seat enum[agent,user] |
| clipboard_set / clipboard_get | the 0.3 properties + seat enum[agent,user] |

A selector is `{con_id:int} | {app_id:str} | {title:str}` plus an optional `match`.

Tool descriptions: one or two sentences each, stating the precise
guarantee. Copy these verbatim:

- **act**: "Do one thing to one element and report whether it worked. Tries a
  semantic accessibility action first, then the agent's own seat, then (only
  if the user is idle and policy allows) a brief foreground step with the
  user's focus restored. The result's effect is confirmed/changed/no_change/unverifiable with evidence."
- **observe**: "Map what is on screen as numbered elements (role, name, state,
  bounds) from the accessibility tree, with an OCR fallback. Use the refs with
  act/read. Prefer this to screenshots."
- **run**: "Execute up to 50 steps (act, wait_for, launch, observe, key,
  type_text, focus_window, assert) server-side in one call; stops at the first failure."

---

## 5.2 `server.py` — MODIFY

1. Remove the star imports of moved modules; import `tools`, `specs`,
   `timeline`, `session`, and `ToolError`/`Result` from core.
2. The `tools/call` dispatch:

```python
started = time.monotonic()
try:
    out = TOOLS[name](arguments)
    content, meta = (out.content, out.meta) if isinstance(out, Result) else (out, {})
    append_event(name, arguments, started, True, meta)
    return ok(content)
except ToolError as exc:
    append_event(name, arguments, started, False, {"error": exc.code} if getattr(exc, "code", None) else {})
    ...
```

3. `run_mcp_server()`: wrap the loop in `try/finally: session.current().stop()`.
   The SIGTERM handler raises `SystemExit(0)` (existing behaviour).
4. `OPERATING_INSTRUCTIONS`: rewrite as below (keep the safety sentences from
   0.3 verbatim at the end):

> "Work through observe → act (or run for several steps). observe returns
> numbered refs from the accessibility tree; act on refs, not coordinates. Every
> input returns an effect: treat no_change/unverifiable as not done. By default
> you act on your own agent seat and workspace (cus-agent); launch puts new apps
> there. The user keeps their pointer, keyboard and focus. Use screenshot only
> when you need pixels. …(0.3 safety text: untrusted screen text,
> confirmations before destructive/sensitive actions, seshat note)…"

5. `core.ToolError` gains an optional `code` attribute (`stale_ref`,
   `no_editable_text`, `user_active`, `agent_seat_missing`, …). The message
   text stays human-readable.

## 5.3 `timeline.py` — MODIFY

- `TIMELINE_ACTION_TOOLS` = all 19 tools.
- `append_event(name, arguments, at, ok, meta=None)`: the payload is the
  curated argument keys (existing table, extended below) **merged with
  `meta`**. `meta` is curated by the tool, so merge it as-is, but run it
  through `_scrub()`: drop any key named `text`, `value`, `content`,
  `clipboard`, `password`, `env` or `argv`, recursively. This is
  defence in depth.
- New payload key table:

```python
"session": ("action", "mode"),
"observe": ("scope", "max_elements", "ocr", "image"),      # query handled below
"read": ("ref",),
"act": ("action", "delivery", "verify", "direction", "clicks", "key", "modifiers"),   # target handled below
"run": ("stop_on",),
"wait_for": ("timeout_ms",),
"launch": ("a11y",),
# low-level tools: existing keys + "seat"
```

- Special curation:
  - `observe.query` → `{role, match, name_chars, text_chars}`
  - `act.target` → `{ref}` or `{point:{x,y}}` or `{query_role, name_chars}`
  - `act.text` → `characters`
  - `launch.argv` → `program` = basename(argv[0])
  - `run.steps` → `steps` count + `tools` list
- Update `tests/test_timeline.py`: every new tool, plus a test that no
  `text`/`argv`/`env` value ever reaches the file (write events with secrets
  in the args, read the file back, and assert the secret strings are absent).

## 5.4 `cli.py` doctor / self-test — MODIFY (after the PLAN 2.7 move)

`--doctor` adds:

- `wayland`: the globals present among `zwlr_virtual_pointer_manager_v1`,
  `zwp_virtual_keyboard_manager_v1`, `ext_idle_notifier_v1`,
  `zwlr_screencopy_manager_v1`
- `agent_seat`: `install.status()`
- `a11y`: bus reachable, `org.a11y.Status` values, app count
- `electron_without_a11y`: Sway windows classified chromium whose a11y app
  has ≤ 3 nodes, with the hint to relaunch with the flag
- `ocr`: tesseract path and version; `grim`
- `obsolete`: `wtype` and `/dev/uinput` are no longer used (informational)

`--self-test` fails (exit 1) when `agent_seat.ok` is false, with the install command.

---

## 5.5 Integration test suite — CREATE `tests/integration/`

Runner: `make integration` →
`PYTHONPATH=src python3 -m unittest discover -s tests/integration -t .`.
Every test class uses the **product** `sandbox.Sandbox` and the product
modules. The spikes are references, not dependencies. Skip (never fail) with
a precise reason when `sway`, `grim`, at-spi or a target app is missing. The
Electron target reads `CUS_TEST_ELECTRON=/path/to/electron` and skips when it
is unset.

Fixtures: copy `spikes/oracle_gtk3.py` and `spikes/targets.py` to
`tests/integration/apps/`. The targets keep the `T:` title protocol: entry
`Field`, buttons `Go`/`Hit`, the miss reporter.

| id | test module | ported from | asserts |
|---|---|---|---|
| T1.1 | test_transport_ipc | – | `swayipc` get_tree/outputs/command against the sandbox; the subscription sees `workspace` on `workspace 2` |
| T1.2 | test_transport_input | spike3, spike8 | drag held-motion ≥ 15 events and press≠release point; scroll events; Unicode + shift + ctrl+a on GTK3/GTK4/Qt6/Firefox (seat0) |
| T2.1 | test_seat_isolation | spike1b | agent types into B while the user types into A; IPC focus acts on seat0; binding focus acts on agent only |
| T2.2 | test_bringup | spike11, spike14 | `Agent.start()` from nothing; launch on `cus-agent`; seat0 focus unchanged; the workspace is named `cus-agent` on the agent output and **no user output has a workspace named `cus-agent`** |
| T2.3 | test_reload_recovery | spike7c, spike13, spike14 | after `reload`: gate raises; `ensure_ready` restores device counts (agent 2, seat0 unchanged), the agent output at x=100000, user output positions unchanged; pre-existing GTK3/GTK4/Qt6 windows accept agent typing |
| T2.4 | test_idle_gate | spike12 | seat0 idle/resumed; agent input does not resume seat0 |
| T3.1 | test_a11y_geometry_matrix | spike4 matrix | for each of the 12 cases: `observe(scope=window)` lists `Hit`; `act(ref, click, delivery=agent or foreground per profile)` → the page/app reports `T:hit` (not `T:miss`); `effect` ∈ {confirmed, changed} |
| T3.2 | test_ocr_ensemble | spike6 | 10 cases: `observe(ocr=always)` lists a text element `Hit`; clicking it gives `T:hit` |
| T3.3 | test_refs | – | a ref stays valid after the window moves (floating move); goes stale after the element is destroyed; OCR ref stale after the window content changes |
| T4.1 | test_act_ladder | spike4/4b | GTK3 `set_text` → rung 1 `confirmed`; Firefox `set_text` → rung 1 readback_failed → rung 2 `confirmed`; Chromium `set_text` → rung 1 absent → rung 2 skipped (profile) → rung 3 with idle gate → `confirmed`, user focus restored |
| T4.2 | test_launch | spike1d, spike14 | gtk3/gtk4/qt6/firefox/chromium launched land on `cus-agent`; chromium gets the a11y flag (its tree has the `Field` element); seat0 untouched |
| T4.3 | test_verdicts | spike5 | click on empty background → `no_change`; press `Go` → `confirmed`/`changed`; an off-output window with no a11y → `unverifiable` |
| T4.4 | test_run_form | – | one `run` call: launch gtk4 target, set_text Field "hello", press Go, `wait_for` title `T:clicked`, assert → ok, ≤ 1 MCP call |
| T4.5 | test_foreground_single_seat | spike10 | Chromium/Electron typing via `act(type)` with the user typing before/after; user text intact; focus restored |
| T4.6 | test_user_untouched | all | a background "user" thread types into oracle A every 200 ms during an entire T4.4 run; A's text equals exactly what the user typed; seat0 focus never left A (sampled at 20 Hz) |
| T5.1 | test_mcp_stdio | – | spawn `python -m computer_use_sway` with the sandbox env; `initialize`, `tools/list` (19 tools), `tools/call observe`, `act`; the timeline file has 2+ events with verdict keys inside `payload` and no secret text |

Wall-clock budget for the full suite: < 15 min on this host (the spikes take ~13 min).

---

## 5.6 Benchmark — CREATE `bench/`

**Layer 1: mechanical, deterministic, run in CI** (`make bench`).
`bench/tasks/*.json`, 24 tasks in 6 families:

- forms
- dialogs
- menus
- lists/selection
- multi-window
- text-heavy (OCR)

Each task has a setup (a target app in the sandbox) and an oracle (a
window-title or file-content check). Two scripted arms per task:

- `legacy`: screenshot → coordinates (taken from the oracle's known geometry,
  standing in for the 0.3/Claude/OpenAI pixel loop at a perfect-grounding
  best case) → click/type → screenshot
- `v1`: observe → act or run

Metrics per task: success, MCP calls, image bytes returned, total response
bytes, wall time, seat0 disturbances (focus changes + typing intrusions
measured by the T4.6 harness). Output `bench/results/<date>.json` plus a
markdown table. Gate: `v1` success ≥ legacy success on every task, and
`v1` disturbances == 0 in seat mode.

**Layer 2: model-in-the-loop, run manually** (`bench/eval.md`). The same tasks
with a real agent (Hermes or Claude Code, via MCP) in sandbox mode. Arms:

- this server 1.0
- this server 0.3.2 (git worktree)
- Hermes `computer_use` with cua-driver at the newest release
  (`CUA_DRIVER_RS_ENABLE_WAYLAND=1` and the sandbox's `WAYLAND_DISPLAY`)

Score: success, turns, tokens, images, wall time. This is the evidence for any
public "exceeds" claim. Until layer 2 has run, docs must say "designed to"
and must not say "exceeds".

---

## 5.7 Makefile — MODIFY

```make
.PHONY: check test py-compile lint-files integration bench doctor build clean
integration:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests/integration -t .
bench:
	PYTHONPATH=src $(PYTHON) -m bench.run
```

`check` stays fast and deterministic (no sway). The release gate is
`make check && make integration && make bench`.

## 5.8 `pyproject.toml` — MODIFY

Description: "MCP server for computer use on Sway: its own seat and workspace,
accessibility-first grounding, verified actions". Keep `dependencies = []`.
**Do not touch `version`** (release tooling owns it). If the console script
moves to `cli:cli`, update `[project.scripts]`.

---

## 5.9 Documentation — MODIFY (all must match shipped behaviour)

- **AGENTS.md / Product Contract**: replace "The stream is the only file this
  server writes" with the runtime-files table from ARCHITECTURE §9. Add:
  - "The agent seat is config-declared, never created at runtime."
  - "Every agent-seat binding is followed by IPC `nop`."
  - "Never add a `workspace <name>` binding."
  - "Sandbox teardown removes only its own explicit root."

  Keep the other contract lines. Add `make integration` to Validation.
- **WIREFRAME.md**: rewrite Tool Catalog (19 tools), add Modes, Agent Seat,
  Verdicts and Runtime Files sections, and update Timeline Publication (meta
  inside payload; curation table).
- **docs/architecture.md**: replace it with a condensed ARCHITECTURE.md (layers
  and lifecycle). Link FINDINGS for the evidence.
- **README.md**:
  - requirements: grim; optional tesseract, wl-clipboard, at-spi2-core
  - drop the wtype/uinput/udev section
  - install: `computer-use-sway --install-sway-config`
  - a 6-line quickstart (observe/act/run/launch)
  - the claims table from FINDINGS, phrased as "measured on Sway 1.10.1"
- **skill/solverforge-computer-use/SKILL.md**: the operating procedure becomes:
  1. `session status`
  2. `observe`
  3. act on refs, or `run` for multi-step
  4. read the effect; treat `no_change` as not done
  5. screenshot only for visual questions
  6. `launch` for new apps
  7. never paste for text entry
  8. user mode for seshat recordings of the visible screen

  Remove the "derive coordinates from screenshots" rule.
- **docs/codex.md**: unchanged except for the server description.

## 5.10 Release

One coherent iteration → **1.0.0** through `commit-and-tag-version` (house
rule: no hand-edited versions or CHANGELOG). Commits are atomic and
conventional. The breaking commit uses `feat!:` with a `BREAKING CHANGE:`
footer listing:

- the removed `wtype` and `/dev/uinput` requirements
- the new required one-time `--install-sway-config`
- the default seat is now the agent seat
- the new tool results (effect)

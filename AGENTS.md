# Repository Guidelines

Guidance for coding agents working in this repository.

## Product Contract

- `computer-use-sway` is a zero-runtime-dependency Python MCP stdio server that
  controls a Sway/Wayland desktop. `dependencies = []` in `pyproject.toml` is a
  hard contract; a missing binary is a clear `ToolError`, never a hard import
  failure.
- Desktop control only. Recording, the take timeline, speech synthesis, captions
  and muxing live in the separate `seshat` server
  (<https://github.com/blackopsrepl/seshat>). Do not reintroduce them here: a
  second implementation of that contract is the exact failure this split removes.
- The server is deterministic, never embeds an LLM, and never generates narration
  prose.
- The `tools/call` dispatch layer publishes every dispatched action/observation
  call to the take timeline stream a recorder reads; keep that hook intact. A
  published event means a call was *dispatched* — never that the action had its
  intended visible effect.
- Outside its own runtime directory (`$XDG_RUNTIME_DIR/computer-use-sway/`:
  private sandbox files and captures) the stream is the only file this server
  writes, besides a caller-given `save_path`. Stream payloads are curated here before they are
  written: typed text becomes a character count, clipboard
  content becomes a byte count, and neither value ever reaches the file.
- Publishing is a side channel: a missing runtime directory or any write error is
  swallowed, because narration must never be able to break an action.
- Direct user-session control only. No second seat/output/workspace, placement,
  focus restoration, idle-gated delivery, private bindings or config installer.
- Agents must never invoke desktop use without the human's explicit request.
  MCP availability and a running session are not consent. Client approval policy
  must enforce this too; the server cannot authenticate human provenance.
- No implicit startup: desktop calls refuse `session_required` until explicit
  `session start` (default `user`). `sandbox` remains private test isolation.
- Every `act` returns an effect; publication alone is never visible success.
- Tests never touch the user's live session: integration tests and the bench
  run in sandbox sways with a private runtime directory and buses.

## Project Structure

Layers in `src/computer_use_sway/` (dependencies point down; details in
`docs/architecture.md`):

- transports: `swayipc.py`, `wire.py`, `keymap.py`, `vinput.py`, `dbus.py`.
- desktop: `core.py` (errors, subprocesses, environment recovery), `desktop.py`,
  `geometry.py`, `capture.py`.
- lifecycle: `install.py` (runtime path only), `idle.py` (observation only),
  `session.py`, `sandbox.py`.
- perception: `atspi.py`, `toolkits.py`, `ocr.py`, `query.py`, `refs.py`,
  `scene.py`.
- action: `actions.py`, `actions_delivery.py`, `verify.py`, `launch.py`,
  `waits.py`, `plans.py`.
- surface: `tools*.py` / `specs*.py` (MCP tools and schemas), `server.py` (the
  protocol loop and the publishing `tools/call` dispatch), `cli.py`,
  `timeline.py` (payload curation and the stream), `version.py`.
- `tests/`: deterministic unit tests; `tests/integration/`: sandbox-sway
  suites; `bench/`: the benchmark; `docs/plan-1.0/`: the plan, the measured
  FINDINGS every design choice cites, and the spikes that measured them.

## Engineering Rules

- Any file that reaches **500 lines** must be split into multiple files. This is
  enforced by `scripts/check_file_length.py` through `make check`. Move code into
  a new focused module instead of growing an existing one; `CHANGELOG.md` and
  binary assets are exempt.
- Do not hand-edit `CHANGELOG.md` or version numbers. `.versionrc.js` owns
  `pyproject.toml` and `SERVER_VERSION`, and releases run through
  `commit-and-tag-version`.
- When a task is simple, do the simple thing. Do not expand scope into unrelated
  critical paths.

## Validation

Run:

```bash
make check        # unit tests, bytecode compilation, file-length check
make integration  # sandbox sways: needs sway, grim, tesseract, at-spi2-core and the test apps
make bench        # benchmark layer 1; exits non-zero when its gate fails
make release-gate # check, integration, bench and no sandbox leftovers
```

`make check` gates every commit; `make release-gate` gates every release and
any change to delivery, perception or the direct user session. A gate's verdict is its
exit status. Do not pipe a gate through `grep`, `head` or `tail`, or append
commands after it, when you judge it: a pipeline reports its last command's
status, and a failure then reads as a pass. Save the full log and read the
status. New behavior belongs in current docs with its verification evidence.
`docs/plan-1.0/` is historical; never restore its retired dual-seat design.

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, screenshots, and the pointer to
  `seshat` for recording and narration.
- `WIREFRAME.md`: shipped MCP tool surface, timeline publication, runtime files.
- `docs/architecture.md`: layers, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.
- `AGENTS.md`: repository rules and validation.

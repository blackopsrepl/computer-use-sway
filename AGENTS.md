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
- The stream is the only file this server writes, and its payloads are curated
  here before they are written: typed text becomes a character count, clipboard
  content becomes a byte count, and neither value ever reaches the file.
- Publishing is a side channel: a missing runtime directory or any write error is
  swallowed, because narration must never be able to break an action.

## Project Structure

- `src/computer_use_sway/core.py`: tool errors, subprocess execution, session
  environment recovery, and strict parsing helpers.
- `desktop.py`: Sway output, seat, tree, window, coordinate, and cursor helpers.
- `timeline.py`: the published timeline stream — payload curation, stream path,
  startup truncation, and `append_event`.
- `tools.py` / `specs.py`: MCP tool wrappers and JSON schemas.
- `server.py`: MCP protocol loop, the publishing `tools/call` dispatch, and the
  CLI facade.
- `version.py`: server name and version.
- `tests/`: deterministic unit tests.

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
make check
```

That runs the unit tests, bytecode compilation, and the file-length check.

## Documentation Surfaces

Keep these synchronized with shipped behavior:

- `README.md`: public overview, requirements, screenshots, and the pointer to
  `seshat` for recording and narration.
- `WIREFRAME.md`: shipped MCP tool surface, timeline publication, runtime files.
- `docs/architecture.md`: layers, boundaries, and lifecycle.
- `docs/codex.md`: Codex registration.
- `skill/solverforge-computer-use/SKILL.md`: agent operating procedure.
- `AGENTS.md`: repository rules and validation.

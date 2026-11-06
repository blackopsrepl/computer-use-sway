# Codex setup

Install `python3 -m pip install .`, then register explicitly:

```bash
computer-use-sway --install-codex-mcp
codex mcp get computer-use-sway --json
```

Registration is not consent to desktop use. Keep this server disabled by
default or require human approval in the client. Agents must only invoke its
desktop tools when the human explicitly requests desktop interaction/inspection.
The server cannot authenticate human provenance of an MCP request.

After authorization, call `session(action=start)` explicitly (default `user`).
No desktop tool auto-starts it. All input directly controls the human's pointer,
keyboard focus, workspace and clipboard; no second seat or isolation exists.
No Sway configuration installation is required or supported.

The launcher resolves `COMPUTER_USE_SWAY_COMMAND` (shell words), the invoked
console script, an installed command on PATH, then the source module fallback.
Inspect with `computer-use-sway --doctor` or `--self-test`; these diagnostics
read the session and must also only be run for an authorized desktop task.

```bash
computer-use-sway --uninstall-codex-mcp
```

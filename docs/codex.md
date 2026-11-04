# Codex Setup

Install the package first:

```bash
python3 -m pip install .
computer-use-sway --install-sway-config
```

The server gives the agent its own Sway seat and workspace beside you, grounds
targets on the accessibility tree first, and returns an effect verdict for every
action (see `WIREFRAME.md`). The second command declares that seat once.

Register:

```bash
computer-use-sway --install-codex-mcp
```

The registration command resolves in this order:

1. `COMPUTER_USE_SWAY_COMMAND`, parsed as shell words.
2. `computer-use-sway` found on `PATH`.
3. `python -m computer_use_sway` as a source-checkout fallback.

Inspect:

```bash
codex mcp get computer-use-sway --json
computer-use-sway --doctor
```

Unregister:

```bash
computer-use-sway --uninstall-codex-mcp
```

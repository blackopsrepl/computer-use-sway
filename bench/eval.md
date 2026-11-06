# Benchmark

Two layers. Both run in sandbox sways (private sway, D-Bus and AT-SPI bus);
neither touches the user's session.

## Layer 1: scripted, deterministic (`make bench`)

`bench/run.py` runs the 24 tasks in `bench/tasks/` against GTK3. Two arms:
`legacy` is a pixel loop with oracle coordinates; `v1` uses observe/act/run.
Both now use direct user-session input in their private sandbox. No concurrent
human typing is simulated: sharing focus is intentional, not a regression.
The gate requires every v1 task to succeed, including wherever legacy succeeds.
Calls, bytes, and wall time are measured. Results carry the direct-user contract;
no focus-isolation or lost-typing metric is claimed. Empty task selections fail.

Dated results predating this change are historical dual-seat measurements;
they do not establish performance or isolation for the current implementation.

## Layer 2: a real agent (`python -m bench.eval`)

`bench/eval.py` gives the same task goals to Claude Code in headless mode
(`claude -p`, built-in tools disabled, only the MCP server `desk` allowed) and
swaps only the server:

| arm | server |
|---|---|
| `v1` | this working tree |
| `v0.3.2` | this server at tag v0.3.2 (`CUS_LEGACY_TREE=<git worktree>`) |
| `cua` | `cua-driver mcp` at the newest stable release, `CUA_DRIVER_RS_ENABLE_WAYLAND=1` (`--cua <path>`) |

Each sandbox gets an idle pointer and keyboard on seat0 before the agent
starts, as a real session has its hardware; without them 0.3.2's sway cursor
commands and wtype lost input that no user's desktop would lose. Every row
records the agent's last words and the tools it used, next to what the
oracle shows.

Every server is wrapped in `bench/mcp_meter.py`, which counts tool calls,
images, image bytes and response bytes the same way for each arm. The agent's
own report gives turns, tokens, cost and wall time. Success is the bench app's
window title, read from sway after the agent stops. Results:
`bench/results/eval-<date>.json`.

```bash
git worktree add --detach /tmp/cus-0.3.2 v0.3.2
PYTHONPATH=src CUS_LEGACY_TREE=/tmp/cus-0.3.2 \
  python3 -m bench.eval --arms v1,v0.3.2,cua --cua /path/to/cua-driver
```

Same agent, prompt, tasks and sandbox for every arm, so the comparison is
between tool surfaces, not between agents. Public "exceeds" claims cite a
layer-2 result file.

## Historical result, 2026-11-04 (`bench/results/eval-2026-11-04T231112.json`)

Claude Code (default model), one run per task and arm, 24 tasks.

| arm | success | turns | tokens | cost | wall | calls | images | image bytes |
|---|---|---|---|---|---|---|---|---|
| v1 | 24/24 | 228 | 1.86 M | $2.08 | 295 s | 204 | 30 | 0.33 MB |
| v0.3.2 | 24/24 | 453 | 2.71 M | $3.86 | 533 s | 429 | 166 | 1.86 MB |
| cua-driver 0.38.0 | 24/24 | 192 | 9.67 M | $4.44 | 490 s | 168 | 90 | 2.62 MB |

Every arm completes every task. v1 does it with the fewest tokens, the lowest
cost, the least time and the fewest images; cua-driver with the fewest calls
and turns. No user is active in layer 2, so disturbance is measured only by
layer 1.


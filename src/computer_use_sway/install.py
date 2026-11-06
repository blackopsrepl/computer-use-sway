"""Private runtime directory; no Sway configuration is installed."""
from pathlib import Path
import os
from . import core


def runtime_root() -> Path:
    core.ensure_session_environment()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise core.ToolError("XDG_RUNTIME_DIR is not set")
    return Path(runtime) / "computer-use-sway"

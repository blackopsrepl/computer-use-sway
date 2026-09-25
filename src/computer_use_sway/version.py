"""Server identity constants shared by the CLI facade and the tool layer.

This module is a leaf: it imports nothing from the package, so both
``server.py`` (the MCP facade) and ``tools.py`` (the tool layer) can import the
server name and version without a circular import.
"""

from __future__ import annotations

SERVER_NAME = "computer-use-sway"
SERVER_VERSION = "0.2.5"

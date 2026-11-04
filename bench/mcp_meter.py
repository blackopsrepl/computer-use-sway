#!/usr/bin/env python3
"""MCP stdio meter: relay between an MCP client and a server, counting what the
server returns. Usage: mcp_meter.py LOG -- SERVER_ARGV...

Writes one JSON line per tools/call response to LOG: tool, response bytes,
image count and image bytes. Stdlib only; the bench's layer 2 wraps every arm
with it so the three arms are measured the same way.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: mcp_meter.py LOG -- SERVER_ARGV...", file=sys.stderr)
        return 2
    log = open(argv[0], "a", buffering=1, encoding="utf-8")
    server = subprocess.Popen(argv[2:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert server.stdin is not None and server.stdout is not None
    pending: dict[object, str] = {}
    lock = threading.Lock()

    def client_to_server() -> None:
        for line in sys.stdin.buffer:
            try:
                msg = json.loads(line)
                if msg.get("method") == "tools/call":
                    with lock:
                        pending[msg.get("id")] = msg.get("params", {}).get("name", "?")
            except ValueError:
                pass
            server.stdin.write(line)  # type: ignore[union-attr]
            server.stdin.flush()  # type: ignore[union-attr]
        server.stdin.close()  # type: ignore[union-attr]

    threading.Thread(target=client_to_server, daemon=True).start()
    for line in server.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        with lock:
            tool = pending.pop(msg.get("id"), None)
        if tool is None:
            continue
        content = (msg.get("result") or {}).get("content") or []
        images = [c for c in content if isinstance(c, dict) and c.get("type") == "image"]
        image_bytes = 0
        for image in images:
            try:
                image_bytes += len(base64.b64decode(image.get("data", "")))
            except ValueError:
                pass
        log.write(json.dumps({"tool": tool, "bytes": len(line), "images": len(images),
                              "image_bytes": image_bytes,
                              "error": bool((msg.get("result") or {}).get("isError") or msg.get("error"))}) + "\n")
    return server.wait()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

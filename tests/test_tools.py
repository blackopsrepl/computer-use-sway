from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import core, server

from support import single_output


class ScreenInfoTests(unittest.TestCase):
    def test_reports_server_identity_and_binaries(self) -> None:
        with patch.object(core, "require_binaries"), patch.object(
            server.desktop, "get_outputs", return_value=single_output()
        ), patch.object(
            server.desktop,
            "active_output_rects",
            return_value=[{"x": 0, "y": 0, "width": 1920, "height": 1080}],
        ), patch.object(
            server.desktop, "detect_seat", return_value="seat0"
        ), patch.object(
            server.desktop, "get_focused_window", return_value=None
        ):
            payload = json.loads(server.tool_screen_info({})[0]["text"])

        self.assertEqual(payload["server"]["name"], "computer-use-sway")
        self.assertEqual(payload["server"]["version"], server.SERVER_VERSION)
        self.assertEqual(payload["bounds"], {"x": 0, "y": 0, "width": 1920, "height": 1080})
        self.assertIn("edge-tts", payload["binaries"])


class ScreenshotTests(unittest.TestCase):
    def test_encodes_png_for_image_and_data_url_modes(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        completed = core.CommandResult(stdout=png, stderr=b"", returncode=0)
        with patch.object(core, "require_binaries"), patch.object(
            server.desktop, "validate_region", return_value=None
        ), patch.object(core, "run_command", return_value=completed):
            content = server.tool_screenshot({"output": "both"})

        meta = json.loads(content[0]["text"])
        self.assertEqual(meta["bytes"], len(png))
        image = [item for item in content if item["type"] == "image"][0]
        self.assertEqual(base64.b64decode(image["data"]), png)
        data_url = [item for item in content if item.get("text", "").startswith("data:image/png")][0]
        self.assertEqual(
            data_url["text"],
            "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        )


class DragTests(unittest.TestCase):
    def test_release_failure_is_logged_and_drag_still_reported(self) -> None:
        def sway_cursor(action: str, *_args: str) -> None:
            if action == "release":
                raise core.ToolError("release failed")

        with patch.object(
            server.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ), patch.object(
            server.desktop, "sway_cursor", side_effect=sway_cursor
        ), patch.object(core, "eprint") as eprint:
            payload = json.loads(
                server.tool_drag(
                    {"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}}
                )[0]["text"]
            )

        self.assertTrue(payload["dragged"])
        eprint.assert_called_once()
        self.assertIn("release failed", eprint.call_args[0][0])


if __name__ == "__main__":
    unittest.main()

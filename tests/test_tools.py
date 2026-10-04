from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from computer_use_sway import core, server


def single_output(name: str = "DP-1", width: int = 1920, height: int = 1080) -> list[dict]:
    return [
        {
            "name": name,
            "active": True,
            "rect": {"x": 0, "y": 0, "width": width, "height": height},
        }
    ]


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
        self.assertIn("grim", payload["binaries"])

    def test_narration_binaries_are_no_longer_reported(self) -> None:
        """This server does not narrate; its dependency report must not claim it does."""
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

        for name in ("edge-tts", "piper", "wf-recorder", "ffmpeg", "ffprobe", "tesseract"):
            self.assertNotIn(name, payload["binaries"])


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

    def test_save_path_writes_private_png_and_returns_text_only(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        completed = core.CommandResult(stdout=png, stderr=b"", returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "shot.png")
            with patch.object(core, "require_binaries"), patch.object(
                server.desktop, "validate_region", return_value=None
            ), patch.object(core, "run_command", return_value=completed):
                content = server.tool_screenshot({"save_path": target})
            self.assertEqual(len(content), 1)
            self.assertEqual(content[0]["type"], "text")
            meta = json.loads(content[0]["text"])
            self.assertEqual(meta["bytes"], len(png))
            self.assertEqual(meta["saved_to"], target)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), png)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    def test_save_path_requires_existing_parent(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        completed = core.CommandResult(stdout=png, stderr=b"", returncode=0)
        with patch.object(core, "require_binaries"), patch.object(
            server.desktop, "validate_region", return_value=None
        ), patch.object(core, "run_command", return_value=completed):
            with self.assertRaises(server.ToolError):
                server.tool_screenshot({"save_path": "/nonexistent-pvd-dir/shot.png"})

    def test_save_path_and_output_are_mutually_exclusive(self) -> None:
        with patch.object(core, "require_binaries"):
            with self.assertRaises(server.ToolError):
                server.tool_screenshot({"save_path": "/tmp/shot.png", "output": "both"})


class DragTests(unittest.TestCase):
    """`tool_drag` validates its arguments and hands the movement to the virtual
    pointer. It used to drive the whole path through Sway's cursor warp, which
    is why the drag never worked; the release is no longer a Sway command at
    all, so the old test's premise — a failing `release` must be logged — is
    gone with it.
    """

    def test_delegates_movement_to_the_virtual_pointer(self) -> None:
        calls: list[tuple] = []
        with patch.object(
            server.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ), patch.object(server.pointer, "drag", side_effect=lambda *a, **k: calls.append((a, k))):
            payload = json.loads(
                server.tool_drag(
                    {
                        "from": {"x": 1, "y": 2},
                        "to": {"x": 30, "y": 40},
                        "steps": 5,
                        "duration_ms": 200,
                    }
                )[0]["text"]
            )

        self.assertTrue(payload["dragged"])
        self.assertEqual(payload["steps"], 5)
        (args, kwargs), = calls
        self.assertEqual(args[0], (1, 2))
        self.assertEqual(args[1], (30, 40))
        self.assertEqual(kwargs["steps"], 5)
        self.assertEqual(kwargs["duration_ms"], 200)
        # The virtual pointer needs the absolute warp passed in rather than
        # importing the desktop layer itself.
        self.assertIs(kwargs["sway_cursor"], server.desktop.sway_cursor)

    def test_a_pointer_failure_surfaces_rather_than_reporting_a_drag(self) -> None:
        """Answering `dragged: true` for a drag that did not happen is the exact
        failure this whole change exists to remove."""
        with patch.object(
            server.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ), patch.object(
            server.pointer, "drag", side_effect=core.ToolError("no virtual pointer")
        ):
            with self.assertRaises(server.ToolError) as caught:
                server.tool_drag({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}})

        self.assertIn("no virtual pointer", str(caught.exception))

    def test_steps_and_duration_are_bounded(self) -> None:
        with patch.object(
            server.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ):
            with self.assertRaises(server.ToolError):
                server.tool_drag({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}, "steps": 0})
            with self.assertRaises(server.ToolError):
                server.tool_drag(
                    {"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}, "duration_ms": 99_999}
                )


if __name__ == "__main__":
    unittest.main()

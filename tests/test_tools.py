from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from computer_use_sway import capture, core, session, tools_input, tools_observe


def single_output(name: str = "DP-1", width: int = 1920, height: int = 1080) -> list[dict]:
    return [{"name": name, "active": True, "rect": {"x": 0, "y": 0, "width": width, "height": height}}]


class FakeSession:
    mode = "seat"
    user_seat = "seat0"
    _agent = None

    def ensure_started(self) -> None:
        pass

    def default_seat(self) -> str:
        return "agent"


class ScreenInfoTests(unittest.TestCase):
    def payload(self) -> dict:
        with patch.object(session, "current", return_value=FakeSession()), patch.object(
            tools_observe.desktop, "get_outputs", return_value=single_output()
        ), patch.object(tools_observe.desktop, "get_focused_window", return_value=None):
            return json.loads(tools_observe.tool_screen_info({}).content[0]["text"])

    def test_reports_server_identity_mode_and_binaries(self) -> None:
        payload = self.payload()
        self.assertEqual(payload["server"]["name"], "computer-use-sway")
        self.assertEqual(payload["bounds"], {"x": 0, "y": 0, "width": 1920, "height": 1080})
        self.assertEqual(payload["mode"], "seat")
        self.assertEqual(payload["default_input_seat"], "agent")
        self.assertIn("grim", payload["binaries"])

    def test_obsolete_and_narration_binaries_are_not_reported(self) -> None:
        payload = self.payload()
        for name in ("wtype", "swaymsg", "edge-tts", "piper", "wf-recorder", "ffmpeg"):
            self.assertNotIn(name, payload["binaries"])


class ScreenshotTests(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"payload"

    def shot(self, arguments: dict) -> core.Result:
        with patch.object(capture, "png", return_value=self.PNG), patch.object(
            tools_observe.desktop, "validate_region", return_value=None
        ):
            return tools_observe.tool_screenshot(arguments)

    def test_encodes_png_for_image_and_data_url_modes(self) -> None:
        content = self.shot({"output": "both"}).content
        meta = json.loads(content[0]["text"])
        self.assertEqual(meta["bytes"], len(self.PNG))
        image = [item for item in content if item["type"] == "image"][0]
        self.assertEqual(base64.b64decode(image["data"]), self.PNG)
        data_url = [item for item in content if item.get("text", "").startswith("data:image/png")][0]
        self.assertEqual(data_url["text"], "data:image/png;base64," + base64.b64encode(self.PNG).decode("ascii"))

    def test_save_path_writes_private_png_and_returns_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "shot.png")
            content = self.shot({"save_path": target}).content
            self.assertEqual(len(content), 1)
            meta = json.loads(content[0]["text"])
            self.assertEqual(meta["saved_to"], target)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), self.PNG)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    def test_save_path_requires_existing_parent(self) -> None:
        with self.assertRaises(core.ToolError):
            self.shot({"save_path": "/nonexistent-pvd-dir/shot.png"})

    def test_save_path_and_output_are_mutually_exclusive(self) -> None:
        with self.assertRaises(core.ToolError):
            self.shot({"save_path": "/tmp/shot.png", "output": "both"})

    def test_region_window_and_output_are_exclusive(self) -> None:
        with self.assertRaises(core.ToolError):
            tools_observe.screenshot_region({"region": {"x": 0}, "target_output": "DP-1"})

    def test_timeline_meta_never_carries_pixels(self) -> None:
        result = self.shot({"output": "image"})
        self.assertNotIn("data", json.dumps(result.meta))


class FakePointer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.position = None

    def drag(self, *args, **kwargs) -> None:
        self.calls.append(("drag", args, kwargs))

    def click(self, *args, **kwargs) -> None:
        self.calls.append(("click", args, kwargs))


class FakeDevices:
    def __init__(self) -> None:
        self.pointer = FakePointer()


class InputToolTests(unittest.TestCase):
    def use(self, devices: FakeDevices):
        class Ctx:
            def __enter__(self_inner):
                use = tools_input.SeatUse("agent", devices)
                return use

            def __exit__(self_inner, *exc):
                return False

        return patch.object(tools_input, "using_seat", lambda arguments: Ctx())

    def test_drag_hands_the_path_to_the_seat_pointer(self) -> None:
        devices = FakeDevices()
        with self.use(devices), patch.object(
            tools_input.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ), patch.object(tools_input, "pointer_target", return_value=None):
            result = tools_input.tool_drag(
                {"from": {"x": 1, "y": 2}, "to": {"x": 30, "y": 40}, "steps": 5, "duration_ms": 200, "verify": False}
            )
        payload = json.loads(result.content[0]["text"])
        self.assertTrue(payload["dragged"])
        self.assertEqual(payload["seat"], "agent")
        (_kind, args, kwargs), = devices.pointer.calls
        self.assertEqual(args, ((1, 2), (30, 40)))
        self.assertEqual(kwargs["steps"], 5)

    def test_a_pointer_failure_surfaces_rather_than_reporting_a_drag(self) -> None:
        devices = FakeDevices()
        devices.pointer.drag = lambda *a, **k: (_ for _ in ()).throw(core.ToolError("compositor gone"))
        with self.use(devices), patch.object(
            tools_input.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))
        ), patch.object(tools_input, "pointer_target", return_value=None):
            with self.assertRaises(core.ToolError):
                tools_input.tool_drag({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}, "verify": False})

    def test_bounds_are_enforced(self) -> None:
        with patch.object(tools_input.desktop, "validate_coordinates", side_effect=lambda x, y: (int(x), int(y))):
            with self.assertRaises(core.ToolError):
                tools_input.tool_drag({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}, "steps": 0})
            with self.assertRaises(core.ToolError):
                tools_input.tool_drag({"from": {"x": 1, "y": 2}, "to": {"x": 3, "y": 4}, "duration_ms": 99_999})
            with self.assertRaises(core.ToolError):
                tools_input.tool_click({"x": 1, "y": 1, "count": 4})

    def test_seat_must_be_agent_or_user(self) -> None:
        with self.assertRaises(core.ToolError):
            tools_input.resolve_seat({"seat": "seat0"})

    def test_modifiers_are_validated_and_deduplicated(self) -> None:
        self.assertEqual(tools_input.parse_modifiers(["Ctrl", "ctrl", "shift"]), ["ctrl", "shift"])
        with self.assertRaises(core.ToolError):
            tools_input.parse_modifiers(["hyper"])

    def test_type_text_rejects_nul_and_oversize(self) -> None:
        with self.assertRaises(core.ToolError):
            tools_input.tool_type_text({"text": "a\x00b"})
        with self.assertRaises(core.ToolError):
            tools_input.tool_type_text({"text": "x" * (core.TEXT_LIMIT + 1)})


if __name__ == "__main__":
    unittest.main()

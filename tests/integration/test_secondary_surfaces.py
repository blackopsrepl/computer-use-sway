"""Secondary surfaces: modal dialogs, popup menus, Qt widgets top-levels
(FINDINGS O12, A12, A13, A14), against the real toolkits in a sandbox sway."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from computer_use_sway import core, desktop

from harness import Box, needs_sway

ROOT = Path(__file__).resolve().parents[2]
BENCH_APP = str(ROOT / "bench" / "app.py")
QT_MODAL = Path(__file__).resolve().parent / "apps" / "qtmodal"
PY = "/usr/bin/python3"


def press(b: Box, name: str, **query) -> dict:
    return b.call("act", {"target": {"query": {"name": name, "match": "exact", **query}}, "action": "press"})


def title_becomes(b: Box, expect: str) -> bool:
    return bool(b.wait(lambda: any(w.get("name") == expect for w in desktop.all_windows()), 5))


@needs_sway
class Gtk3DialogTests(unittest.TestCase):
    def test_a12_a_modal_dialog_stays_visible_to_the_tree(self) -> None:
        with Box() as b:
            log = str(Path(b.assets) / "bench.jsonl")
            b.call("launch", {"argv": [PY, BENCH_APP, "dialogs", "x.pdf", log]})
            out = press(b, "Delete x.pdf")
            self.assertEqual(out["delivery"], "agent", "gtk3 presses go through the pointer first")
            found = b.call("wait_for", {"condition": {"element": {"name": "Delete", "match": "exact",
                                                                  "role": "push button"}}, "timeout_ms": 5000})
            self.assertTrue(found["satisfied"], found)
            press(b, "Delete", role="push button")
            self.assertTrue(title_becomes(b, "B:done deleted"))


@needs_sway
class PopupMenuTests(unittest.TestCase):
    def test_a13_menu_items_are_popup_and_pressed_semantically(self) -> None:
        with Box() as b:
            log = str(Path(b.assets) / "bench.jsonl")
            b.call("launch", {"argv": [PY, BENCH_APP, "menus", "-", log]})
            press(b, "File")
            listing = b.call("observe", {"scope": "agent", "query": {"name": "Export PDF", "match": "exact"}})
            self.assertIn("[popup]", listing)
            out = press(b, "Export PDF")
            self.assertEqual(out["delivery"], "semantic")
            self.assertTrue(title_becomes(b, "B:done Export PDF"))
            with self.assertRaises(core.ToolError):  # no pointer form for popup contents
                b.call("act", {"target": {"query": {"name": "Open", "match": "exact"}}, "action": "double_click"})


def build_qt_modal(into: Path) -> Path | None:
    qmake = shutil.which("qmake6")
    if qmake is None or shutil.which("make") is None:
        return None
    for name in ("main.cpp", "qtmodal.pro"):
        shutil.copy(QT_MODAL / name, into / name)
    for cmd in ([qmake], ["make", "-s"]):
        if subprocess.run(cmd, cwd=into, capture_output=True).returncode != 0:
            return None
    return into / "qtmodal"


@needs_sway
class QtWidgetsTests(unittest.TestCase):
    def test_a14_filler_top_level_and_a_modal_exec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = build_qt_modal(Path(tmp))
            if binary is None:
                self.skipTest("needs qmake6 and Qt6 Widgets headers")
            with Box() as b:
                b.call("launch", {"argv": [str(binary)], "env": {"QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1"}})
                listing = b.call("observe", {"scope": "agent"})
                self.assertIn('push button "Delete x.pdf"', listing)
                out = press(b, "Delete x.pdf")
                self.assertEqual(out["delivery"], "semantic")
                time.sleep(0.3)
                found = b.call("wait_for", {"condition": {"element": {"name": "Delete", "match": "exact",
                                                                      "role": "push button"}},
                                            "timeout_ms": 5000})
                self.assertTrue(found["satisfied"], found)
                press(b, "Delete", role="push button")
                self.assertTrue(title_becomes(b, "B:done deleted"))


if __name__ == "__main__":
    unittest.main()

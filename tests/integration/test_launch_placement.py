"""Normal user-session launch and subsequent dialogs in a private sandbox."""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

from computer_use_sway import desktop, launch, session

from harness import targets

NEEDS = {"gtk3": "/usr/bin/python3", "gtk4": "/usr/bin/python3", "qt6": "qml6", "firefox": "firefox",
         "chromium": "chromium", "electron": targets.ELECTRON}


@unittest.skipUnless(shutil.which("sway") and shutil.which("dbus-daemon"), "needs sway and dbus-daemon")
class UserLaunchTests(unittest.TestCase):
    def test_every_toolkit_lands_on_the_current_user_workspace(self) -> None:
        for name, binary in NEEDS.items():
            with self.subTest(name):
                if not shutil.which(binary) and not os.access(binary, os.X_OK):
                    self.skipTest(f"{name} is not installed")
                sess = session.Session()
                sess.start("sandbox")
                try:
                    assets = str(sess.sandbox().root / "assets")
                    targets.write_assets(assets)
                    argv, env = targets.argv(name, assets)
                    out = json.loads(launch.launch(sess, argv, env=env, timeout_ms=60000).content[0]["text"])
                    self.assertEqual(out["window"]["workspace"], "1")
                    self.assertNotIn("moved_from", out["window"], "placed by sway, not moved afterwards")
                    self.assertEqual(desktop.seat_focus("seat0"), out["window"]["id"])
                    self.assertTrue(out["disturbed_user"])
                finally:
                    sess.stop()


BENCH_APP = Path(__file__).resolve().parents[2] / "bench" / "app.py"


@unittest.skipUnless(shutil.which("sway") and shutil.which("dbus-daemon"), "needs sway and dbus-daemon")
class SecondaryWindowTests(unittest.TestCase):
    def test_a_later_dialog_uses_normal_user_workspace(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from harness import Box

        with Box() as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            log = str(Path(b.assets) / "bench.jsonl")
            b.call("launch", {"argv": ["/usr/bin/python3", str(BENCH_APP), "dialogs", "x.pdf", log]})
            b.call("wait_for", {"condition": {"element": {"name": "Delete x.pdf", "match": "exact"}},
                                "timeout_ms": 5000})
            b.call("act", {"target": {"query": {"name": "Delete x.pdf", "match": "exact"}}, "action": "press"})
            dialog = b.wait(lambda: next((w for w in desktop.all_windows() if w.get("name") == "Confirm"), None), 10)
            self.assertEqual(dialog["workspace"], "1")
            self.assertEqual(b.seat_focus("seat0"), dialog["id"])


if __name__ == "__main__":
    unittest.main()

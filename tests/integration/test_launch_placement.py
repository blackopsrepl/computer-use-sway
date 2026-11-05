"""T4.2 launch placement: every toolkit maps on cus-agent and seat0 never moves.

Runs in a private nested sway (product sandbox mode); nothing touches the
live session. Regression for FINDINGS O11 (stripping the activation token
sent Qt6 and Chromium to the user's workspace).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

from computer_use_sway import desktop, launch, session

SPIKES = Path(__file__).resolve().parents[2] / "docs" / "plan-1.0" / "spikes"
sys.path.insert(0, str(SPIKES))
import targets  # noqa: E402

NEEDS = {"gtk3": "/usr/bin/python3", "gtk4": "/usr/bin/python3", "qt6": "qml6", "firefox": "firefox",
         "chromium": "chromium", "electron": targets.ELECTRON}


@unittest.skipUnless(shutil.which("sway") and shutil.which("dbus-daemon"), "needs sway and dbus-daemon")
class LaunchPlacementTests(unittest.TestCase):
    def test_every_toolkit_lands_on_the_agent_workspace(self) -> None:
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
                    before = desktop.seat_focus("seat0")
                    out = json.loads(launch.launch(sess, argv, env=env, timeout_ms=60000).content[0]["text"])
                    self.assertEqual(out["window"]["workspace"], "cus-agent")
                    self.assertNotIn("moved_from", out["window"], "placed by sway, not moved afterwards")
                    self.assertEqual(desktop.seat_focus("seat0"), before)
                    self.assertFalse(out["disturbed_user"])
                finally:
                    sess.stop()


BENCH_APP = Path(__file__).resolve().parents[2] / "bench" / "app.py"


@unittest.skipUnless(shutil.which("sway") and shutil.which("dbus-daemon"), "needs sway and dbus-daemon")
class SecondaryWindowTests(unittest.TestCase):
    def test_a_later_dialog_also_lands_on_the_agent_workspace(self) -> None:
        """sway consumes the launch context on the first window; a dialog
        opened afterwards must not map on (and take focus from) the user."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from harness import Box

        with Box() as b:
            a = b.oracle("A")
            b.user_pointer().click(*b.center_of(a, "A", "entry"))
            log = str(Path(b.assets) / "bench.jsonl")
            b.call("launch", {"argv": ["/usr/bin/python3", str(BENCH_APP), "dialogs", "x.pdf", log]})
            b.call("act", {"target": {"query": {"name": "Delete x.pdf", "match": "exact"}}, "action": "press"})
            dialog = b.wait(lambda: next((w for w in desktop.all_windows() if w.get("name") == "Confirm"), None), 10)
            self.assertEqual(dialog["workspace"], "cus-agent")
            self.assertEqual(b.seat_focus("seat0"), a["id"])


if __name__ == "__main__":
    unittest.main()

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
            if not shutil.which(binary) and not os.access(binary, os.X_OK):
                continue
            with self.subTest(name):
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


if __name__ == "__main__":
    unittest.main()

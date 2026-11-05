"""Sandbox harness (spike): private D-Bus session + headless nested Sway.

Nothing here touches the user's live session: own XDG_RUNTIME_DIR, own
SWAYSOCK, own WAYLAND_DISPLAY, own session bus (so its own AT-SPI bus),
and `xwayland disable` so the nested Sway never grabs the live X0 socket.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

# Spike roots and artefacts; CUS_SPIKE_SCRATCH overrides.
SCRATCH = os.environ.get("CUS_SPIKE_SCRATCH") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "computer-use-sway", "spikes")

BASE_CONFIG = """xwayland disable
output HEADLESS-1 resolution 1280x800 position 0 0
seat seat0 fallback true
seat agent fallback false
"""


class Sandbox:
    def __init__(self, name: str = "sbx", extra_config: str = ""):
        self.root = os.path.join(SCRATCH, name)
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root, mode=0o700)
        self.procs: list[subprocess.Popen] = []
        cfg = os.path.join(self.root, "config")
        with open(cfg, "w") as f:
            f.write(BASE_CONFIG + extra_config)
        base = {k: v for k, v in os.environ.items()
                if k not in ("WAYLAND_DISPLAY", "DISPLAY", "SWAYSOCK", "DBUS_SESSION_BUS_ADDRESS",
                             "AT_SPI_BUS_ADDRESS", "I3SOCK")}
        base["XDG_RUNTIME_DIR"] = self.root
        bus = f"unix:path={self.root}/bus"
        self.procs.append(subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", f"--address={bus}"],
            env=base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        base["DBUS_SESSION_BUS_ADDRESS"] = bus
        for _ in range(100):
            if os.path.exists(f"{self.root}/bus"):
                break
            time.sleep(0.02)
        # The a11y bus registry is normally systemd-activated, which a private
        # session bus cannot do: start the launcher and the registry directly.
        self.procs.append(subprocess.Popen(
            ["/usr/libexec/at-spi2/at-spi-bus-launcher", "--launch-immediately"],
            env=base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.4)
        self.procs.append(subprocess.Popen(
            ["/usr/libexec/at-spi2/at-spi2-registryd", "--use-gnome-session"],
            env=base, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.3)
        self.swaysock = os.path.join(self.root, "sway.sock")
        senv = dict(base, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                    SWAYSOCK=self.swaysock, WLR_RENDERER="pixman")
        self.log = open(os.path.join(self.root, "sway.log"), "w")
        self.procs.append(subprocess.Popen(["sway", "-c", cfg], env=senv,
                                           stdout=self.log, stderr=subprocess.STDOUT))
        for _ in range(100):
            if os.path.exists(self.swaysock) and os.path.exists(os.path.join(self.root, "wayland-1")):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("nested sway did not start: " + open(self.log.name).read()[-800:])
        self.env = dict(base, SWAYSOCK=self.swaysock, WAYLAND_DISPLAY="wayland-1",
                        GDK_BACKEND="wayland", QT_QPA_PLATFORM="wayland",
                        MOZ_ENABLE_WAYLAND="1", NO_AT_BRIDGE="0")
        time.sleep(0.3)

    def msg(self, *args: str, kind: str = "command"):
        out = subprocess.run(["swaymsg", "-s", self.swaysock, "-r", "-t", kind, *args],
                             capture_output=True, text=True, env=self.env)
        return json.loads(out.stdout) if out.stdout.strip() else out.stderr

    def tree(self):
        return self.msg(kind="get_tree")

    def windows(self):
        found = []

        def walk(n, ws=None):
            if n.get("type") == "workspace":
                ws = n.get("name")
            if n.get("pid") and n.get("type") in ("con", "floating_con"):
                found.append(dict(n, _ws=ws))
            for c in n.get("nodes", []) + n.get("floating_nodes", []):
                walk(c, ws)
        walk(self.tree())
        return found

    def window(self, app_id: str, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for w in self.windows():
                if w.get("app_id") == app_id:
                    return w
            time.sleep(0.1)
        raise RuntimeError(f"window {app_id} never mapped")

    def spawn(self, argv: list[str], **env) -> subprocess.Popen:
        p = subprocess.Popen(argv, env=dict(self.env, **env), stdout=subprocess.DEVNULL,
                             stderr=open(os.path.join(self.root, f"app-{len(self.procs)}.log"), "w"))
        self.procs.append(p)
        return p

    def seats(self):
        return {s["name"]: s for s in self.msg(kind="get_seats")}

    def close(self):
        for p in reversed(self.procs):
            p.terminate()
        for p in reversed(self.procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_log(path: str) -> list[dict]:
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []

"""A private nested sway for tests and risky runs.

Own XDG_RUNTIME_DIR, SWAYSOCK, WAYLAND_DISPLAY and session bus (so its own
AT-SPI bus). `xwayland disable` is mandatory: a nested sway with Xwayland
grabs the live X11 socket. Teardown removes only the exact root this
instance created, after checking it lies under the runtime prefix.
"""

from __future__ import annotations

import itertools
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import core, install

_counter = itertools.count(1)
AT_SPI_DIRS = ("/usr/libexec/at-spi2", "/usr/lib/at-spi2-core", "/usr/libexec", "/usr/lib/at-spi2")
STRIP_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "SWAYSOCK", "I3SOCK", "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS")


# A bus without service directories: nothing is D-Bus-activated inside the
# sandbox (gvfs would otherwise mount a FUSE filesystem under its root).
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={path}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
QUIET_ENV = {"GIO_USE_VFS": "local", "GVFS_DISABLE_FUSE": "1", "GSETTINGS_BACKEND": "memory"}


def mounts_under(root: Path) -> list[str]:
    prefix = str(root).rstrip("/") + "/"
    found = []
    try:
        with open("/proc/self/mounts", encoding="utf-8") as handle:
            for line in handle:
                point = line.split()[1].replace("\\040", " ")
                if point == str(root) or point.startswith(prefix):
                    found.append(point)
    except OSError:
        pass
    return found


def _references(pid: str, root: str, marker: bytes) -> bool:
    try:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            if marker in handle.read():
                return True
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            if root.encode() in handle.read():
                return True
    except OSError:
        pass
    try:
        if os.readlink(f"/proc/{pid}/cwd").startswith(root):
            return True
        for fd in os.listdir(f"/proc/{pid}/fd"):
            if os.readlink(f"/proc/{pid}/fd/{fd}").startswith(root):
                return True
    except OSError:
        pass
    return False


def processes_in(root: Path) -> list[int]:
    """Our processes tied to this sandbox root by environment, command line,
    working directory or an open file. Apps exec'd by the nested sway are not
    our descendants, and Chromium overwrites its environ block for its
    process title, so no single signal finds them all."""
    marker = f"XDG_RUNTIME_DIR={root}\0".encode()
    text = str(root)
    uid = os.getuid()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
        except OSError:
            continue
        if _references(entry, text, marker):
            found.append(int(entry))
    return found


def descendants(roots: list[int]) -> list[int]:
    """Every live descendant of `roots` (by ppid), captured before any kill so
    re-parenting to init cannot hide them."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as handle:
                ppid = int(handle.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    found: list[int] = []
    stack = list(roots)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in found:
                found.append(child)
                stack.append(child)
    return found


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def find_atspi(name: str) -> str | None:
    for base in AT_SPI_DIRS:
        path = os.path.join(base, name)
        if os.access(path, os.X_OK):
            return path
    return None


def render_config(root: Path, width: int, height: int, scale: float) -> str:
    return (
        "xwayland disable\n"
        f"output HEADLESS-1 resolution {width}x{height} position 0 0 scale {scale:g}\n"
        "seat seat0 fallback true\n"

    )


def _raise_exit(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def exit_on_sigterm() -> None:
    """Turn the default SIGTERM into SystemExit, so `with Sandbox()` still
    tears down: a terminated bench run left its root and processes behind.
    A handler someone else installed is kept; signals reach only the main
    thread."""
    if threading.current_thread() is threading.main_thread() \
            and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
        signal.signal(signal.SIGTERM, _raise_exit)


class Sandbox:
    def __init__(self, width: int = 1920, height: int = 1080, scale: float = 1.0,
                 prefix: Path | None = None) -> None:
        self.width, self.height, self.scale = width, height, scale
        self.prefix = prefix or install.runtime_root()
        self.root = self.prefix / f"sandbox-{os.getpid()}-{next(_counter)}"
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.env: dict[str, str] = {}
        self.a11y = False
        self._created = False

    def _log(self, name: str) -> Any:
        return open(self.root / f"{name}.log", "w", encoding="utf-8")

    def _tail(self, name: str) -> str:
        try:
            return (self.root / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-800:]
        except OSError:
            return ""

    def _spawn(self, name: str, argv: list[str], env: dict[str, str]) -> subprocess.Popen:
        with self._log(name) as log:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=log, start_new_session=True)
        self.procs.append((name, proc))
        return proc

    @staticmethod
    def _wait_paths(paths: list[Path], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(p.exists() for p in paths):
                return True
            time.sleep(0.05)
        return False

    def start(self) -> None:
        for binary in ("sway", "dbus-daemon"):
            if shutil.which(binary) is None:
                raise core.ToolError(f"sandbox mode needs `{binary}` on PATH")
        exit_on_sigterm()
        self.prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.mkdir(mode=0o700)
        self._created = True
        try:
            self._start_processes()
        except BaseException:
            self.stop()
            raise

    def _start_processes(self) -> None:
        base = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        base.update(QUIET_ENV)
        base["XDG_RUNTIME_DIR"] = str(self.root)
        bus = f"unix:path={self.root}/bus"
        (self.root / "bus.conf").write_text(BUS_CONFIG.format(path=self.root / "bus"), encoding="utf-8")
        self._spawn("dbus", ["dbus-daemon", "--nofork", f"--config-file={self.root / 'bus.conf'}"], base)
        if not self._wait_paths([self.root / "bus"], 2.0):
            raise core.ToolError("sandbox: dbus-daemon did not start: " + self._tail("dbus"))
        base["DBUS_SESSION_BUS_ADDRESS"] = bus
        launcher, registry = find_atspi("at-spi-bus-launcher"), find_atspi("at-spi2-registryd")
        if launcher and registry:
            self._spawn("atspi-launcher", [launcher, "--launch-immediately"], base)
            time.sleep(0.4)
            self._spawn("atspi-registry", [registry, "--use-gnome-session"], base)
            time.sleep(0.3)
            self.a11y = True
        (self.root / "config").write_text(render_config(self.root, self.width, self.height, self.scale),
                                          encoding="utf-8")
        swaysock = self.root / "sway.sock"
        sway_env = dict(base, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                        WLR_RENDERER="pixman", SWAYSOCK=str(swaysock))
        self._spawn("sway", ["sway", "-c", str(self.root / "config")], sway_env)
        if not self._wait_paths([swaysock, self.root / "wayland-1"], 5.0):
            raise core.ToolError("sandbox: nested sway did not start: " + self._tail("sway"))
        self.env = dict(base, SWAYSOCK=str(swaysock), WAYLAND_DISPLAY="wayland-1", GDK_BACKEND="wayland",
                        QT_QPA_PLATFORM="wayland", MOZ_ENABLE_WAYLAND="1", NO_AT_BRIDGE="0")
        time.sleep(0.3)

    def spawn(self, argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
        if not self.env:
            raise core.ToolError("sandbox is not running")
        name = f"app-{len(self.procs)}"
        return self._spawn(name, list(argv), dict(self.env, **(extra_env or {})))

    def _safe_root(self) -> bool:
        root = self.root
        try:
            prefix = self.prefix.resolve()
            resolved = root.resolve()
        except OSError:
            return False
        return (
            self._created
            and root.is_dir()
            and not root.is_symlink()
            and resolved.parent == prefix
            and resolved.name.startswith("sandbox-")
        )

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        # Every child starts its own session, so its process group holds
        # whatever it spawned (the a11y bus launcher's broker, app helpers).
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self) -> None:
        tree = descendants([proc.pid for _name, proc in self.procs])
        for pid in tree:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and any(alive(pid) for pid in tree):
            time.sleep(0.05)
        for _name, proc in reversed(self.procs):
            self._signal_group(proc, signal.SIGTERM)
        for _name, proc in reversed(self.procs):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)
                proc.wait(timeout=5)
        self.procs.clear()
        self.env = {}
        if self._created:
            self._kill_stragglers()
        for point in mounts_under(self.root):
            for tool in ("fusermount3", "fusermount"):
                if shutil.which(tool):
                    subprocess.run([tool, "-u", "-z", point], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
        if mounts_under(self.root):
            core.eprint(f"sandbox: leaving {self.root} in place, something is still mounted under it")
        elif self._safe_root():
            for _attempt in range(5):
                shutil.rmtree(self.root, ignore_errors=True)
                if not self.root.exists():
                    break
                self._kill_stragglers()
                time.sleep(0.2)
            if self.root.exists():
                core.eprint(f"sandbox: could not fully remove {self.root}")
        self._created = False

    def _kill_stragglers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            pids = processes_in(self.root)
            if not pids:
                return
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and processes_in(self.root):
                time.sleep(0.1)

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

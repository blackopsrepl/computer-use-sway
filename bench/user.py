"""The simulated user: types into their own window every 200 ms with their own
keyboard (a separate Wayland connection, like a physical keyboard), while a
watcher samples seat0's focus at 20 Hz. Disturbance = focus moves away from
the user's window + characters that did not arrive in it."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from computer_use_sway import desktop, swayipc, vinput, wire

PY = "/usr/bin/python3"


class UserSimulation:
    def __init__(self, sess: Any, oracle: str, log: Path, typing: bool = True) -> None:
        self.sess, self.oracle, self.log, self.typing = sess, oracle, log, typing
        self.typed: list[str] = []
        self.focus_moves = 0
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.window_id: int | None = None

    def text(self) -> str:
        try:
            rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        except FileNotFoundError:
            return ""
        texts = [r["text"] for r in rows if r["ev"] == "text"]
        return texts[-1] if texts else ""

    def start(self) -> None:
        self.sess.sandbox().spawn([PY, self.oracle, "U", str(self.log)])
        deadline = time.monotonic() + 15
        while True:
            win = next((w for w in desktop.all_windows() if w.get("app_id") == "oracle-U"), None)
            if win is not None:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("user window did not open")
            time.sleep(0.1)
        self.window_id = int(win["id"])
        time.sleep(0.5)
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        geo = [r for r in rows if r["ev"] == "geometry"][-1]["widgets"]["entry"]
        c = desktop.window_by_id(self.window_id)["content"]
        point = (c["x"] + geo["x"] + 20, c["y"] + geo["y"] + geo["h"] // 2)
        self.threads = [threading.Thread(target=self._type, args=(point,), daemon=True),
                        threading.Thread(target=self._watch, daemon=True)]
        for thread in self.threads:
            thread.start()
        time.sleep(0.5)

    def _type(self, point: tuple[int, int]) -> None:
        conn = wire.Connection()
        devices = vinput.SeatDevices(conn, "seat0", extent=desktop.layout_extent)
        devices.pointer.click(*point)  # the user puts the caret in their own field
        i = 0
        while self.typing and not self.stop_event.is_set():
            ch = "abcdefghij"[i % 10]
            devices.keyboard.type_text(ch)
            self.typed.append(ch)
            i += 1
            self.stop_event.wait(0.2)
        self.stop_event.wait()
        devices.destroy()
        conn.close()

    def _watch(self) -> None:
        ipc = swayipc.Ipc(desktop.ipc().path)
        away = False
        while not self.stop_event.is_set():
            focus = next((s["focus"] for s in ipc.seats() if s["name"] == "seat0"), None)
            if focus != self.window_id and not away:
                self.focus_moves += 1
            away = focus != self.window_id
            self.stop_event.wait(0.05)
        ipc.close()

    def stop(self) -> dict[str, int]:
        time.sleep(0.4)
        self.stop_event.set()
        for thread in self.threads:
            thread.join(5)
        time.sleep(0.4)
        typed, got = "".join(self.typed), self.text()
        lost = sum(1 for a, b in zip(typed, got.ljust(len(typed))) if a != b) + max(0, len(got) - len(typed))
        return {"focus_moves": self.focus_moves, "typing_lost": lost}

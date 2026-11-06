from __future__ import annotations

import json
import socket
import struct
import threading
import unittest
import unittest.mock

from computer_use_sway import core, dbus, keymap, swayipc, wire


class DBusMarshallingTests(unittest.TestCase):
    def roundtrip(self, sig: str, values: tuple) -> tuple:
        message = dbus.build_message(dbus.METHOD_RETURN, 7, {5: ("u", 3)}, sig, values)
        header, parsed, total = dbus.parse_message(message)
        self.assertEqual(total, len(message))
        self.assertEqual(header["serial"], 7)
        return parsed

    def test_basic_and_container_types(self) -> None:
        values = (1, True, -5, 2 ** 40, 1.5, "héllo", dbus.ObjectPath("/a/b"), [1, 2, 3],
                  {"k": dbus.Variant("i", 4)}, (7, "x"))
        parsed = self.roundtrip("ybixdsoaua{sv}(is)", values)
        self.assertEqual(parsed[:7], values[:7])
        self.assertEqual(parsed[7], [1, 2, 3])
        self.assertEqual(parsed[8]["k"].value, 4)
        self.assertEqual(parsed[9], (7, "x"))

    def test_atspi_child_list_shape(self) -> None:
        refs = [(":1.5", dbus.ObjectPath("/org/a11y/atspi/accessible/1"))]
        self.assertEqual(self.roundtrip("a(so)", (refs,))[0], refs)

    def test_signature_split(self) -> None:
        self.assertEqual(dbus.split_signature("sa{sv}(ii)ai"), ["s", "a{sv}", "(ii)", "ai"])

    def test_address_parsing(self) -> None:
        self.assertEqual(dbus.parse_address("unix:path=/run/user/1/bus,guid=x"), "/run/user/1/bus")
        self.assertEqual(dbus.parse_address("unix:abstract=foo"), b"\0foo")
        with self.assertRaises(core.ToolError):
            dbus.parse_address("tcp:host=x")


class KeymapTests(unittest.TestCase):
    def test_ascii_on_real_keys(self) -> None:
        self.assertEqual(keymap.US["a"], (30, False))
        self.assertEqual(keymap.US["A"], (30, True))
        self.assertEqual(keymap.US["?"], (53, True))

    def test_keysyms(self) -> None:
        self.assertEqual(keymap.keysym_for_char("é"), "U00E9")
        self.assertEqual(keymap.keysym_for_char("\n"), "Return")
        self.assertEqual(keymap.normalize_key("enter"), "Return")
        self.assertEqual(keymap.normalize_key("f5"), "F5")
        self.assertEqual(keymap.normalize_key("PageDown"), "Next")

    def test_slots_are_low_keycodes(self) -> None:
        text = keymap.build_keymap(["XF86Macro27", None, "U00E9"]).decode()
        self.assertIn("key <FK13> {[ XF86Macro27 ]};", text)
        self.assertIn("key <FK15> {[ U00E9 ]};", text)
        self.assertNotIn("FK14", text)
        self.assertTrue(text.endswith("\0"))
        # Chromium/Electron drop xkb keycodes >= 256
        self.assertTrue(all(code + 8 < 256 for code, _ in keymap.SLOTS))


class SlotTests(unittest.TestCase):
    def keyboard(self):
        from computer_use_sway import vinput

        kb = vinput.VirtualKeyboard.__new__(vinput.VirtualKeyboard)
        kb.slots = [None] * len(keymap.SLOTS)
        kb.pinned = set()
        kb._used = {}
        kb.uploads = 0

        def upload() -> None:
            kb.uploads += 1

        kb._upload = upload
        patcher = unittest.mock.patch.object(vinput, "KEYMAP_SETTLE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        return kb

    def test_lru_eviction_keeps_recent_text(self) -> None:
        kb = self.keyboard()
        for i in range(30):
            kb.code_for("U%04X" % (0x100 + i))
        self.assertIn("U011D", kb.slots)  # most recent survives
        self.assertNotIn("U0100", kb.slots)

    def test_codes_map_to_slot_keys(self) -> None:
        kb = self.keyboard()
        code = kb.code_for("U00E9")
        self.assertEqual(code, keymap.SLOTS[kb.slots.index("U00E9")][0])
        self.assertEqual(kb.code_for("F13"), keymap.SLOTS[kb.slots.index("F13")][0])

    def test_characters_use_unassigned_codes_and_keys_use_f_keys(self) -> None:
        # Ghostty reads F13-F24 as function keys and sends their escape
        # sequences, and Chromium drops keys it has no DOM code for [I6].
        kb = self.keyboard()
        text_codes = {code for code, _ in keymap.TEXT_SLOTS}
        self.assertIn(kb.code_for("U00F6"), text_codes)
        self.assertIn(kb.code_for("U20AC"), text_codes)
        self.assertNotIn(kb.code_for("F13"), text_codes)
        self.assertEqual(sorted(text_codes), [86, 121, 124, 134, 179, 180])
        self.assertIn("key <LSGT> {[ U00F6 ]};", keymap.build_keymap(kb.slots).decode())


class WireTests(unittest.TestCase):
    def test_string_padding(self) -> None:
        self.assertEqual(wire.wl_string("abc"), struct.pack("=I", 4) + b"abc\0")
        self.assertEqual(len(wire.wl_string("abcd")), 4 + 8)
        text, end = wire.read_string(wire.wl_string("seat0") + b"tail", 0)
        self.assertEqual((text, end), ("seat0", 12))


class SwayIpcTests(unittest.TestCase):
    def test_command_roundtrip_and_failure(self) -> None:
        server, client = socket.socketpair()

        def serve() -> None:
            for reply in ([{"success": True}], [{"success": False, "error": "nope"}]):
                head = server.recv(14)
                length, _kind = struct.unpack("=II", head[6:])
                server.recv(length)
                body = json.dumps(reply).encode()
                server.sendall(swayipc.MAGIC + struct.pack("=II", len(body), 0) + body)

        thread = threading.Thread(target=serve)
        thread.start()
        ipc = swayipc.Ipc.__new__(swayipc.Ipc)
        ipc.path, ipc._timeout, ipc._sock, ipc._lock = "x", 5.0, client, threading.Lock()
        ipc._connect = lambda: (_ for _ in ()).throw(AssertionError("no reconnect expected"))
        self.assertEqual(ipc.command("nop"), [{"success": True}])
        with self.assertRaises(core.ToolError) as caught:
            ipc.command("bogus")
        self.assertIn("nope", str(caught.exception))
        thread.join()
        server.close()
        client.close()

    def test_event_log_wait(self) -> None:
        log = swayipc.EventLog()
        t0 = 0.0
        threading.Timer(0.05, lambda: log.append("window", {"change": "new"})).start()
        hit = log.wait_for(lambda e: e[1] == "window", t0, 2.0)
        self.assertIsNotNone(hit)
        self.assertIsNone(log.wait_for(lambda e: e[1] == "output", t0, 0.05))


if __name__ == "__main__":
    unittest.main()

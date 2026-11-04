from __future__ import annotations

import unittest

from computer_use_sway import atspi, capture, core, ocr, query, refs, toolkits


class OcrTests(unittest.TestCase):
    PPM = b"P6\n2 2\n255\n" + bytes([0, 10, 0, 0, 200, 0, 0, 30, 0, 0, 250, 0])

    def test_variants_are_byte_exact(self) -> None:
        v = ocr.variants(self.PPM)
        self.assertEqual(v["raw"], self.PPM)
        self.assertEqual(v["gray"], b"P5 2 2 255\n" + bytes([10, 200, 30, 250]))
        self.assertEqual(v["gray_inv"], b"P5 2 2 255\n" + bytes([245, 55, 225, 5]))
        self.assertEqual(len(v["mid"]), len(v["gray"]))

    def test_dedupe_keeps_best(self) -> None:
        words = [ocr.Word("Save", (0, 0, 40, 10), 70, "raw"), ocr.Word("Save", (2, 1, 40, 10), 90, "gray")]
        kept = ocr.dedupe(words)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].conf, 90)

    def test_dedupe_resolves_different_readings_of_one_spot(self) -> None:
        words = [ocr.Word("Hi", (10, 10, 30, 20), 95, "raw"), ocr.Word("Hit", (10, 10, 36, 20), 80, "gray"),
                 ocr.Word("Hit", (11, 10, 35, 20), 75, "mid"), ocr.Word("Go", (100, 10, 30, 20), 90, "raw")]
        kept = ocr.dedupe(words)
        self.assertEqual([w.text for w in kept], ["Hit", "Go"])  # two variants agree on "Hit"
        self.assertEqual([line.text for line in ocr.lines(kept)], ["Hit", "Go"])

    def test_lines_and_span_box(self) -> None:
        words = [ocr.Word(t, (x, 10, w, 20), 90, "raw")
                 for t, x, w in (("File", 0, 40), ("Save", 50, 40), ("as", 95, 20), ("Export", 125, 60))]
        lines = ocr.lines(words)
        self.assertEqual([l.text for l in lines], ["File Save as Export"])
        (line, box), = ocr.find(lines, "save as")
        self.assertEqual(box, (50, 10, 65, 20))

    def test_tsv_parsing_filters_low_confidence(self) -> None:
        tsv = "h\n" + "\t".join(["5", "1", "1", "1", "1", "1", "3", "4", "5", "6", "91", "Go"]) + "\n" \
              + "\t".join(["5", "1", "1", "1", "1", "2", "3", "4", "5", "6", "20", "xx"])
        self.assertEqual([w.text for w in ocr.parse_tsv(tsv, "raw")], ["Go"])


def ppm(width: int, height: int, dark: set[tuple[int, int]]) -> bytes:
    px = bytearray(b"\xff" * width * height * 3)
    for x, y in dark:
        px[(y * width + x) * 3:(y * width + x) * 3 + 3] = b"\0\0\0"
    return b"P6 %d %d 255\n" % (width, height) + bytes(px)


class ChangeTests(unittest.TestCase):
    def test_a_blinking_caret_is_noise(self) -> None:
        caret = {(10, y) for y in range(2, 22)} | {(11, y) for y in range(2, 22)}
        self.assertFalse(capture.changed(ppm(40, 30, set()), ppm(40, 30, caret)))

    def test_a_glyph_or_a_repaint_is_a_change(self) -> None:
        glyph = {(x, y) for x in range(5, 13) for y in range(5, 15)}
        self.assertTrue(capture.changed(ppm(40, 30, set()), ppm(40, 30, glyph)))
        wide_line = {(x, 3) for x in range(0, 30)}
        self.assertTrue(capture.changed(ppm(40, 30, set()), ppm(40, 30, wide_line)))
        self.assertTrue(capture.changed(ppm(40, 30, set()), ppm(41, 30, set())))

    def test_crop(self) -> None:
        image = ppm(4, 4, {(1, 1)})
        self.assertEqual(capture.crop(image, (1, 1, 1, 1)), b"P6 1 1 255\n\0\0\0")
        self.assertEqual(capture.crop(image, (3, 3, 5, 5)), b"P6 1 1 255\n\xff\xff\xff")


class QueryTests(unittest.TestCase):
    def test_role_synonyms(self) -> None:
        self.assertTrue(query.role_matches("button", "push button"))
        self.assertTrue(query.role_matches("entry", "text"))
        self.assertFalse(query.role_matches("button", "label"))
        # either spelling finds the other (GTK3 dialog buttons report "button")
        self.assertTrue(query.role_matches("push button", "button"))
        self.assertTrue(query.role_matches("menu item", "check menu item"))

    def test_parse_validates(self) -> None:
        with self.assertRaises(core.ToolError):
            query.parse({})
        with self.assertRaises(core.ToolError):
            query.parse({"name": "x", "match": "fuzzy"})
        with self.assertRaises(core.ToolError):
            query.parse({"name": "(", "match": "regex"})
        self.assertEqual(query.parse({"role": "button", "nth": 2}).nth, 2)

    def test_text_matches_name_or_text(self) -> None:
        q = query.parse({"text": "wifi"})
        self.assertTrue(query.matches(q, "label", "Wi-Fi WIFI", None))
        self.assertTrue(query.matches(q, "entry", "", "my wifi pass"))


class ToolkitTests(unittest.TestCase):
    def test_classify(self) -> None:
        cases = [(("gtk", None, "3.24"), "gtk3"), (("GTK", None, None), "gtk4"), (("gtk", None, "4.14"), "gtk4"),
                 (("Qt", None, None), "qt"), (("Gecko", None, None), "gecko"), (("Chromium", None, None), "chromium"),
                 ((None, "kitty", None), "terminal"), ((None, "md.obsidian.Obsidian", None), "chromium"),
                 ((None, "org.gnome.Foo", None), "unknown")]
        for (name, app_id, version), want in cases:
            with self.subTest(want=want, app_id=app_id):
                self.assertEqual(toolkits.classify(name, app_id, toolkit_version=version).name, want)

    def test_learned_single_seat(self) -> None:
        learned = toolkits.Learned()
        learned.mark_single_seat("odd.app")
        self.assertIs(learned.profile(toolkits.PROFILES["unknown"], "odd.app").multi_seat, False)
        self.assertIsNone(learned.profile(toolkits.PROFILES["unknown"], "other").multi_seat)


class FakeNode:
    def __init__(self, role: str, name: str, states: set[str]) -> None:
        self.role, self.name, self.states = role, name, states


class FakeClient:
    def __init__(self, node: FakeNode | None) -> None:
        self._node = node

    def node(self, _ref):
        if self._node is None:
            raise core.ToolError("gone")
        return self._node


class RefTests(unittest.TestCase):
    def element(self) -> refs.Element:
        return refs.Element(ref="", source="a11y", con_id=1, role="push button", name="Go", a11y=(":1.2", "/o"))

    def test_eviction_and_validation(self) -> None:
        store = refs.RefStore()
        snap = store.new_snapshot()
        ref = store.add(snap, self.element())
        self.assertEqual(ref, f"s{snap}:1")
        live = FakeClient(FakeNode("push button", "Go", {"showing"}))
        self.assertEqual(store.validate(ref, live, lambda e: (1, 2, 3, 4), lambda r: "").bounds, (1, 2, 3, 4))
        for client in (FakeClient(None), FakeClient(FakeNode("push button", "Stop", {"showing"})),
                       FakeClient(FakeNode("push button", "Go", set()))):
            with self.assertRaises(core.ToolError) as caught:
                store.validate(ref, client, lambda e: None, lambda r: "")
            self.assertEqual(caught.exception.code, "stale_ref")
        for _ in range(refs.KEEP_SNAPSHOTS):
            store.new_snapshot()
        with self.assertRaises(core.ToolError):
            store.get(ref)

    def test_ocr_refs_go_stale_when_their_pixels_change(self) -> None:
        store = refs.RefStore()
        snap = store.new_snapshot()
        el = refs.Element(ref="", source="ocr", con_id=1, role="text", name="Save", region={"x": 0},
                          crop_px=(0, 0, 4, 4), pixels=b"P6 1 1 255\n\0\0\0")
        ref = store.add(snap, el)
        store.validate(ref, None, lambda e: None, lambda e: True)
        with self.assertRaises(core.ToolError):
            store.validate(ref, None, lambda e: None, lambda e: False)


class AtspiTests(unittest.TestCase):
    def test_state_bits(self) -> None:
        bits = [(1 << 25) | (1 << 12), 1 << (43 - 32)]
        self.assertEqual(atspi.states_from(bits), {"showing", "focused", "read_only"})

    def test_invalid_extents(self) -> None:
        self.assertFalse(atspi.valid_extents((-1, -1, -1, -1)))
        self.assertFalse(atspi.valid_extents((-2147483648, 0, 1, 1)))
        self.assertTrue(atspi.valid_extents((0, 0, 10, 10)))

    def test_gecko_zero_nactions_probe_and_synonyms(self) -> None:
        client = atspi.Client(session_bus=None, a11y_bus=object())
        client.prop = lambda ref, iface, name: 0
        client.call = lambda ref, iface, member, sig="", *a: ("press",) if a == (0,) else (_ for _ in ()).throw(
            core.ToolError("x"))
        self.assertEqual(client.actions(("a", "/b")), ["press"])
        self.assertEqual(client.resolve_action(("a", "/b"), "click"), 0)

    def test_a_stalled_app_is_skipped_until_the_window_passes(self) -> None:
        from computer_use_sway import dbus

        calls: list[str] = []

        class Bus:
            def call(self, dest, *args, **kw):
                calls.append(dest)
                raise dbus.DBusError("org.freedesktop.DBus.Error.Timeout", dest)

        client = atspi.Client(session_bus=None, a11y_bus=Bus())
        with self.assertRaises(dbus.DBusError):
            client.call((":1.9", "/x"), "acc", "GetChildren")
        with self.assertRaises(core.ToolError) as caught:
            client.call((":1.9", "/y"), "acc", "GetChildren")
        self.assertEqual(caught.exception.code, "a11y_stalled")
        self.assertEqual(calls, [":1.9"])
        client.stalled[":1.9"] = 0.0  # window over: tried again
        with self.assertRaises(dbus.DBusError):
            client.call((":1.9", "/x"), "acc", "GetChildren")
        self.assertEqual(calls, [":1.9", ":1.9"])


if __name__ == "__main__":
    unittest.main()

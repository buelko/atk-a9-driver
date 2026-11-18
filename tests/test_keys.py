"""Tastenkombinationen, Makros, Schnellfeuer und die uebrigen Zusatzfunktionen.

Die Byte-Folgen in GOLDEN_* stammen aus einer echten ATK A9 Ultra und wurden
von der Herstellersoftware geschrieben - die Kodierer muessen sie exakt treffen.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atk_a9 import keys as k  # noqa: E402
from atk_a9 import protocol as p  # noqa: E402
from atk_a9.device import AtkMouse, MouseOffline  # noqa: E402
from atk_a9.fake import FakeAtkDevice  # noqa: E402
from atk_a9.hidraw import HidrawNode, ReportInfo, ReportSizes  # noqa: E402

GOLDEN_SHORTCUT_KP2 = bytes.fromhex("02 81 5a 00 41 5a 00 dd")
GOLDEN_SHORTCUT_1 = bytes.fromhex("02 81 1e 00 41 1e 00 55")
GOLDEN_MACRO_1 = bytes.fromhex("02 81 1e 00 00 0f 41 1e 00 00 05 41")
GOLDEN_MACRO_2 = bytes.fromhex("02 81 1e 00 00 1f 41 1e 00 00 01 35")


def connect() -> tuple[AtkMouse, FakeAtkDevice]:
    fake = FakeAtkDevice()
    node = HidrawNode(
        path="/dev/hidraw-test", name="Test", vendor=0x373B, product=0x101A, interface=1,
        reports=ReportInfo(reports={0x08: ReportSizes(input=16, output=16)}),
    )
    mouse = AtkMouse(fake, node)
    mouse.identify()
    return mouse, fake


class ShortcutCodecTests(unittest.TestCase):
    def test_encoder_matches_vendor_bytes(self):
        self.assertEqual(k.encode_shortcut(k.parse_combo("kp2")), GOLDEN_SHORTCUT_KP2)
        self.assertEqual(k.encode_shortcut(k.parse_combo("1")), GOLDEN_SHORTCUT_1)

    def test_decode_vendor_bytes(self):
        events = k.decode_shortcut(GOLDEN_SHORTCUT_KP2 + bytes(2) + b"\xff" * 10)
        self.assertEqual([(e.type, e.down, e.value1) for e in events], [(1, True, 0x5A), (1, False, 0x5A)])
        self.assertEqual(k.describe_combo(events), "kp2")

    def test_combo_order_is_modifiers_then_key(self):
        events = k.parse_combo("Strg+Shift+a")
        self.assertEqual(
            [(e.type, e.down, e.value1) for e in events],
            [(0, True, 1), (0, True, 2), (1, True, 4), (0, False, 1), (0, False, 2), (1, False, 4)],
        )
        raw = k.encode_shortcut(events)
        self.assertEqual(len(raw), 20)
        self.assertEqual(sum(raw) & 0xFF, p.CHECKSUM_TARGET)
        self.assertEqual(k.describe_combo(k.decode_shortcut(raw)), "ctrl+shift+a")

    def test_invalid_combos(self):
        for text in ("", "ctrl", "ctrl+shift+alt+a", "ctrl+nixda", "hyper+a"):
            with self.assertRaises(ValueError, msg=text):
                k.parse_combo(text)

    def test_unprogrammed_and_corrupt_slots(self):
        self.assertEqual(k.decode_shortcut(b"\xff" * 20), [])
        self.assertEqual(k.decode_shortcut(bytes(20)), [])
        broken = bytearray(GOLDEN_SHORTCUT_1)
        broken[-1] ^= 1
        with self.assertRaises(p.ProtocolError):
            k.decode_shortcut(bytes(broken))


class MacroCodecTests(unittest.TestCase):
    def test_encoder_matches_vendor_bytes(self):
        self.assertEqual(k.encode_macro(k.parse_macro("+1 15ms -1 5ms")), k.MACRO_HEADER + GOLDEN_MACRO_1)
        self.assertEqual(k.encode_macro(k.parse_macro("+1 31ms -1 1ms")), k.MACRO_HEADER + GOLDEN_MACRO_2)
        self.assertEqual(len(k.MACRO_HEADER), 31)

    def test_decode_and_text_roundtrip(self):
        events = k.decode_macro(k.MACRO_HEADER + GOLDEN_MACRO_1)
        self.assertEqual([(e.down, e.value1, e.delay_ms) for e in events], [(True, 0x1E, 15), (False, 0x1E, 5)])
        self.assertEqual(k.format_macro(events), "+1 15ms -1 5ms")

    def test_text_forms(self):
        events = k.parse_macro("+ctrl c 50ms -ctrl click:left wheel:down 20ms f5")
        self.assertEqual(
            [(e.type, e.down, e.value1, e.delay_ms) for e in events],
            [
                (0, True, 1, 10), (1, True, 6, 10), (1, False, 6, 50), (0, False, 1, 10),
                (4, True, 1, 10), (4, False, 1, 10),
                (6, None, 255, 20), (6, None, 0, 20),
                (1, True, 0x3E, 10), (1, False, 0x3E, 10),
            ],
        )
        self.assertEqual(k.parse_macro(k.format_macro(events)), events)
        self.assertEqual(k.decode_macro(k.encode_macro(events)), events)

    def test_unknown_events_survive_editing(self):
        """Medientasten o. ae. aus der Herstellersoftware bleiben als raw:... erhalten."""
        events = [k.KeyEvent(k.KeyType.MEDIA, True, 0xE9, 0, 10), k.KeyEvent(k.KeyType.MEDIA, False, 0xE9, 0, 10)]
        text = k.format_macro(events)
        self.assertEqual(text, "raw:82:e9:00 raw:42:e9:00")
        self.assertEqual(k.parse_macro(text), events)

    def test_limits_and_errors(self):
        with self.assertRaises(ValueError):
            k.parse_macro("a " * 36)  # 72 Ereignisse
        self.assertEqual(len(k.parse_macro("a " * 35)), 70)
        self.assertLessEqual(len(k.encode_macro(k.parse_macro("a " * 35))), p.MACRO_SLOT_LEN)
        for text in ("50ms a", "nixda", "click:oben", "a 70000ms", "raw:zz:00:00"):
            with self.assertRaises(ValueError, msg=text):
                k.parse_macro(text)

    def test_macro_modes(self):
        self.assertEqual(k.parse_macro_mode("hold"), 0xFE)
        self.assertEqual(k.parse_macro_mode("toggle"), 0xFD)
        self.assertEqual(k.parse_macro_mode("anykey"), 0xFF)
        self.assertEqual(k.parse_macro_mode("3"), 3)
        for bad in ("0", "251", "immer"):
            with self.assertRaises(ValueError):
                k.parse_macro_mode(bad)


class DeviceKeyFeatureTests(unittest.TestCase):
    def test_reads_vendor_written_data(self):
        mouse, _ = connect()
        self.assertEqual(k.describe_combo(mouse.read_combo(1)), "kp2")
        self.assertEqual(mouse.read_combo(2), [])
        self.assertEqual(k.format_macro(mouse.read_macro(1)), "+1 15ms -1 5ms")
        self.assertEqual(k.format_macro(mouse.read_macro(2)), "+1 31ms -1 1ms")
        self.assertEqual(mouse.read_macro(3), [])

    def test_rewriting_vendor_data_is_a_no_op(self):
        """Gleiche Bytes wie beim Hersteller -> es wird nichts geschrieben."""
        mouse, fake = connect()
        mouse.write_macro(1, "+1 15ms -1 5ms")
        mouse.set_combo(5, "1")
        self.assertEqual([hex(a) for a, _ in fake.writes], ["0x70"])  # nur der Tasten-Record

    def test_set_combo_writes_slot_then_button(self):
        mouse, fake = connect()
        button = mouse.set_combo(4, "ctrl+shift+a")
        self.assertEqual((button.cls, button.value1, button.value2), (5, 0, 0))
        self.assertEqual(button.label, "Tastenkombination ctrl+shift+a")
        # zwei 10-Byte-Rahmen ab Slot-Anfang, dann der Tasten-Record
        self.assertEqual([(hex(a), len(d)) for a, d in fake.writes], [("0x160", 10), ("0x16a", 10), ("0x6c", 4)])
        self.assertEqual(k.describe_combo(mouse.read_combo(4)), "ctrl+shift+a")
        labels = {b.slot: b.label for b in mouse.read_buttons()}
        self.assertEqual(labels[4], "Tastenkombination ctrl+shift+a")

    def test_combo_only_for_six_buttons(self):
        mouse, fake = connect()
        with self.assertRaises(ValueError):
            mouse.set_combo(7, "a")
        self.assertEqual(fake.writes, [])

    def test_write_and_clear_macro(self):
        mouse, fake = connect()
        events = mouse.write_macro(3, "+shift a 30ms -shift click:left")
        self.assertEqual(k.format_macro(events), "+shift +a -a 30ms -shift +mouse:left -mouse:left")
        base = 0x0300 + 2 * 384
        self.assertEqual(bytes(fake.eeprom[base : base + 31]), k.MACRO_HEADER)
        self.assertEqual(fake.eeprom[base + 31], 6)
        # Nachbar-Makros unberuehrt
        self.assertEqual(k.format_macro(mouse.read_macro(2)), "+1 31ms -1 1ms")
        self.assertEqual(mouse.read_macro(4), [])
        self.assertTrue(all(len(d) == 10 and (a - base) % 10 == 0 for a, d in fake.writes))
        mouse.clear_macro(3)
        self.assertEqual(mouse.read_macro(3), [])
        self.assertEqual(bytes(fake.eeprom[base : base + 32]), k.MACRO_HEADER + b"\x00")

    def test_full_macro_stays_inside_its_slot(self):
        mouse, fake = connect()
        guard = bytes(fake.eeprom[0x0300 + 384 : 0x0300 + 384 + 44])
        mouse.write_macro(1, "a " * 35)  # 70 Ereignisse = 383 Byte
        self.assertEqual(len(mouse.read_macro(1)), 70)
        self.assertEqual(bytes(fake.eeprom[0x0300 + 384 : 0x0300 + 384 + 44]), guard)
        self.assertLessEqual(max(a + len(d) for a, d in fake.writes), 0x0300 + 384)

    def test_empty_macro_and_bad_slot(self):
        mouse, _ = connect()
        with self.assertRaises(ValueError):
            mouse.write_macro(1, "")
        for bad in (0, 13):
            with self.assertRaises(ValueError):
                mouse.read_macro(bad)

    def test_fire_key(self):
        mouse, fake = connect()
        button = mouse.set_fire_key(5, 40, 0)
        self.assertEqual(button.raw[:3], bytes([4, 40, 0]))
        self.assertEqual(button.label, "Schnellfeuer alle 40 ms, Dauerfeuer")
        self.assertEqual(mouse.set_fire_key(5, 3, 3).label, "Schnellfeuer alle 3 ms, 3x")
        for interval, count in ((2, 1), (256, 1), (50, 4), (50, -1)):
            with self.assertRaises(ValueError):
                mouse.set_fire_key(5, interval, count)

    def test_macro_button(self):
        mouse, _ = connect()
        button = mouse.set_macro_button(4, 1, "hold")
        self.assertEqual(button.raw, bytes.fromhex("06 00 fe 51"))  # wie in der echten Maus
        self.assertEqual(button.label, "Makro 1, wiederholen solange gehalten")
        self.assertEqual(mouse.set_macro_button(4, 12, 5).label, "Makro 12, 5x ausfuehren")
        with self.assertRaises(ValueError):
            mouse.set_macro_button(4, 13)


class ExtraSettingTests(unittest.TestCase):
    def test_angle_and_snap(self):
        mouse, fake = connect()
        self.assertEqual(mouse.set_angle(angle=-12), (-12, False))
        self.assertEqual(bytes(fake.eeprom[0x00BD:0x00C1]), bytes([0xF4, 0x61, 0x00, 0x55]))
        self.assertEqual(mouse.set_angle(snap=True), (-12, True))
        for bad in (-31, 31):
            with self.assertRaises(ValueError):
                mouse.set_angle(angle=bad)
        with self.assertRaises(ValueError):
            mouse.set_angle()

    def test_angle_unsupported_model(self):
        mouse, fake = connect()
        fake.eeprom[0x00BD:0x00C1] = b"\xff" * 4
        with self.assertRaises(ValueError):
            mouse.set_angle(angle=5)
        self.assertEqual(fake.writes, [])

    def test_wheel_guard(self):
        mouse, fake = connect()
        self.assertEqual(mouse.read_wheel_guard(), 0)
        self.assertEqual(mouse.set_wheel_guard(200), 200)
        # ganze 10-Byte-Systemzeile, die uebrigen Paare unveraendert
        self.assertEqual(fake.writes, [(0x0000, bytes.fromhex("01 54 04 51 01 54 14 41 00 55"))])
        self.assertEqual(mouse.read_system().polling_hz, 1000)
        self.assertEqual(mouse.set_wheel_guard(0), 0)
        for bad in (50, 105, 1010, -10):
            with self.assertRaises(ValueError):
                mouse.set_wheel_guard(bad)

    def test_wheel_guard_missing(self):
        mouse, fake = connect()
        fake.eeprom[6:8] = b"\xff\xff"
        self.assertIsNone(mouse.read_wheel_guard())
        with self.assertRaises(ValueError):
            mouse.set_wheel_guard(200)
        self.assertEqual(fake.writes, [])

    def test_long_range(self):
        mouse, fake = connect()
        self.assertTrue(mouse.set_far_distance(True))
        self.assertEqual(fake.far_distance, 1)
        self.assertFalse(mouse.set_far_distance(False))

    def test_sensor_mode_max(self):
        mouse, _ = connect()
        self.assertEqual(mouse.set_sensor_mode("max").performance_mode, "max")

    def test_status_carries_new_fields(self):
        mouse, fake = connect()
        fake.far_distance = 1
        mouse.set_wheel_guard(300)
        status = mouse.status()
        self.assertTrue(status.long_range)
        self.assertEqual(status.wheel_guard_ms, 300)
        self.assertIn("wheel_guard_ms", status.to_dict())

    def test_sleeping_mouse_during_macro_write(self):
        mouse, fake = connect()
        fake.asleep = True
        with self.assertRaises(MouseOffline):
            mouse.write_macro(1, "a")
        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.lock_depth, 0)


if __name__ == "__main__":
    unittest.main()

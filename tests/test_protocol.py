"""Pruefungen der Protokoll-Ebene.

Die Testvektoren stammen aus echten Referenzimplementierungen bzw. dokumentierten
Hardware-Mitschnitten:

  * Odin-Treiber (xb-bx/atk-a9-ultra-driver, ATK A9 Ultra): Abfrage-Frames
  * OpenMouse-Project/mouse-protocol, docs/atk-testing.md: Frames und DPI-Records
  * VoideUI/Linux-ATK (Rust): DPI-Kodierung eines VXE-R1-artigen Modells
  * MouseBattery (Qt/C++): Rahmen- und DPI-Pruefsummen
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atk_a9 import catalog, hidraw, protocol as p  # noqa: E402


class FrameTests(unittest.TestCase):
    def test_odin_battery_request(self):
        """QUERY_CHARGE des Odin-Treibers fuer die ATK A9 Ultra."""
        reference = bytes.fromhex("08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49")
        frame = p.Frame(cmd=p.Cmd.GET_BATTERY).build()
        self.assertEqual(frame, reference)
        self.assertTrue(p.checksum_ok(frame))

    def test_odin_system_read(self):
        """QUERY_POLL: 6 Bytes ab Adresse 0x0000."""
        reference = bytes.fromhex("08 08 00 00 00 06 00 00 00 00 00 00 00 00 00 00 3f")
        frame = p.Frame(cmd=p.Cmd.GET_EEPROM, address=0x0000, declared=6).build()
        self.assertEqual(frame, reference)

    def test_odin_advanced_read(self):
        """QUERY_HIBERNATION: 10 Bytes ab Adresse 0x00A9."""
        reference = bytes.fromhex("08 08 00 00 a9 0a 00 00 00 00 00 00 00 00 00 00 92")
        frame = p.Frame(cmd=p.Cmd.GET_EEPROM, address=0x00A9, declared=10).build()
        self.assertEqual(frame, reference)

    def test_odin_dpi_color_read(self):
        reference = bytes.fromhex("08 08 00 00 2c 08 00 00 00 00 00 00 00 00 00 00 11")
        frame = p.Frame(cmd=p.Cmd.GET_EEPROM, address=0x002C, declared=8).build()
        self.assertEqual(frame, reference)

    def test_odin_sensor_mode_read(self):
        reference = bytes.fromhex("08 08 00 00 b5 06 00 00 00 00 00 00 00 00 00 00 8a")
        frame = p.Frame(cmd=p.Cmd.GET_EEPROM, address=0x00B5, declared=6).build()
        self.assertEqual(frame, reference)

    def test_openmouse_dongle_version_reply(self):
        """Dokumentierter Hardware-Mitschnitt (status=1, deklariert 0 Bytes)."""
        raw = bytes.fromhex("08 1d 01 00 00 00 00 00 00 00 00 00 00 00 00 00 2f")
        frame = p.Frame.parse(raw, expect_cmd=p.Cmd.GET_RECEIVER_VERSION)
        self.assertEqual(frame.status, 1)
        self.assertEqual(frame.declared, 0)
        self.assertEqual(frame.data, b"")

    def test_roundtrip(self):
        sent = p.Frame(cmd=p.Cmd.SET_EEPROM, address=0x00A9, data=bytes(range(10)))
        parsed = p.Frame.parse(sent.build(), expect_cmd=(int(p.Cmd.SET_EEPROM),))
        self.assertEqual(parsed.address, 0x00A9)
        self.assertEqual(parsed.data, bytes(range(10)))
        self.assertEqual(parsed.declared, 10)

    def test_rejects_bad_checksum(self):
        raw = bytearray(p.Frame(cmd=p.Cmd.GET_BATTERY).build())
        raw[-1] ^= 0xFF
        with self.assertRaises(p.ProtocolError):
            p.Frame.parse(bytes(raw))

    def test_accepts_16_byte_payload(self):
        raw = p.Frame(cmd=p.Cmd.GET_BATTERY).build()[1:]
        frame = p.Frame.parse(raw)
        self.assertEqual(frame.cmd, int(p.Cmd.GET_BATTERY))

    def test_data_limit(self):
        with self.assertRaises(p.ProtocolError):
            p.Frame(cmd=p.Cmd.SET_EEPROM, data=bytes(11)).build()


class PairTests(unittest.TestCase):
    def test_pair(self):
        self.assertEqual(p.pair(0x01), b"\x01\x54")
        self.assertEqual(p.pair(0), b"\x00\x55")
        self.assertEqual(p.pair(0xFF), b"\xff\x56")

    def test_unpair(self):
        self.assertEqual(p.unpair(b"\x01\x54"), 1)
        self.assertEqual(p.unpair(b"\x00\x0a\x02\x53", 2), 2)

    def test_unpair_detects_corruption(self):
        with self.assertRaises(p.ProtocolError):
            p.unpair(b"\x01\x01")


class PollingTests(unittest.TestCase):
    def test_codes(self):
        expected = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01, 2000: 0x10, 4000: 0x20, 8000: 0x40}
        for hz, code in expected.items():
            self.assertEqual(p.polling_to_code(hz), code)
            self.assertEqual(p.polling_from_code(code), hz)

    def test_unknown(self):
        self.assertIsNone(p.polling_to_code(3000))
        self.assertIsNone(p.polling_from_code(0x33))


class LodTests(unittest.TestCase):
    def test_ultra_continuous_scale(self):
        # code = mm*10 - 6, code 1 = 0.7 mm, code 11 = 1.7 mm
        self.assertEqual(p.lod_from_code(1, "PAW3950Ultra"), 0.7)
        self.assertEqual(p.lod_from_code(11, "PAW3950Ultra"), 1.7)
        self.assertEqual(p.lod_to_code("1.0", "PAW3950Ultra"), 4)
        self.assertEqual(p.lod_to_code("code:4", "PAW3950Ultra"), 4)

    def test_r1_discrete_codes(self):
        self.assertEqual(p.lod_from_code(1, "PAW3395SE"), 1.0)
        self.assertEqual(p.lod_from_code(2, "PAW3395SE"), 2.0)
        self.assertEqual(p.lod_to_code("2", "PAW3395SE"), 2)

    def test_paw3950_low_medium_high(self):
        self.assertEqual(p.lod_from_code(3, "PAW3950"), 0.7)
        self.assertEqual(p.lod_from_code(1, "PAW3950"), 1.0)
        self.assertEqual(p.lod_from_code(2, "PAW3950"), 2.0)

    def test_raw_code_escape_hatch(self):
        self.assertEqual(p.lod_to_code("code:9", "PAW3950Ultra"), 9)
        self.assertIsNone(p.lod_to_code("code:99", "PAW3950Ultra"))


class DpiTests(unittest.TestCase):
    def test_step50_rust_vectors(self):
        self.assertEqual(p.dpi_encode(1300, family="step50"), bytes.fromhex("19190023"))
        self.assertEqual(p.dpi_encode(13000, family="step50"), bytes.fromhex("0303440b"))
        self.assertEqual(p.dpi_decode(bytes.fromhex("19190023"), "step50"), (1300, 1300))
        self.assertEqual(p.dpi_decode(bytes.fromhex("0303440b"), "step50"), (13000, 13000))

    def test_step50_openmouse_vectors(self):
        # dokumentierte Reads: 800, 1200 (PAW3950) und 1600 (PAW3395)
        self.assertEqual(p.dpi_decode(bytes.fromhex("0f0f0037"), "step50"), (800, 800))
        self.assertEqual(p.dpi_decode(bytes.fromhex("17170027"), "step50"), (1200, 1200))
        self.assertEqual(p.dpi_decode(bytes.fromhex("1f1f0017"), "step50"), (1600, 1600))
        self.assertEqual(p.dpi_encode(800, family="step50"), bytes.fromhex("0f0f0037"))
        self.assertEqual(p.dpi_encode(1200, family="step50"), bytes.fromhex("17170027"))
        self.assertEqual(p.dpi_encode(1600, family="step50"), bytes.fromhex("1f1f0017"))

    def test_ultra_vectors(self):
        # dieselben Records, aber Ultra-Kodierung: 160 / 240 DPI
        self.assertEqual(p.dpi_decode(bytes.fromhex("0f0f0037"), "ultra"), (160, 160))
        self.assertEqual(p.dpi_decode(bytes.fromhex("17170027"), "ultra"), (240, 240))
        self.assertEqual(p.dpi_encode(1300, family="ultra"), bytes.fromhex("81810053"))

    def test_ultra_ranges(self):
        codec = p.dpi_codec("ultra")
        for dpi in (10, 50, 100, 800, 1600, 3200, 6400, 10000, 10050, 12350, 30000, 30100, 42000):
            record = codec.encode_record(dpi)
            self.assertEqual(codec.decode_record(record), (dpi, dpi), f"DPI {dpi}")

    def test_step50_ranges(self):
        codec = p.dpi_codec("step50")
        for dpi in (50, 100, 800, 1300, 13000, 20000, 30000, 30100, 36000):
            record = codec.encode_record(dpi)
            self.assertEqual(codec.decode_record(record), (dpi, dpi), f"DPI {dpi}")

    def test_paw3395se_roundtrip(self):
        codec = p.dpi_codec("paw3395se")
        for dpi in (200, 400, 1600, 9950, 10100, 18000):
            record = codec.encode_record(dpi)
            self.assertEqual(codec.decode_record(record), (dpi, dpi), f"DPI {dpi}")

    def test_stepped(self):
        codec = p.dpi_codec("stepped", 200, 16000)
        codec = p.DpiCodec("stepped", 200, 16000, step=100)
        for dpi in (200, 300, 800, 16000):
            record = codec.encode_record(dpi)
            self.assertEqual(codec.decode_record(record), (dpi, dpi), f"DPI {dpi}")

    def test_xy_independent(self):
        record = p.dpi_encode(800, 1600, family="step50")
        self.assertEqual(p.dpi_decode(record, "step50"), (800, 1600))

    def test_record_checksum(self):
        for family in ("ultra", "step50", "paw3395se"):
            record = p.dpi_encode(1200, family=family)
            self.assertEqual(sum(record) & 0xFF, p.CHECKSUM_TARGET)

    def test_bad_record_checksum(self):
        with self.assertRaises(p.ProtocolError):
            p.dpi_codec("step50").decode_record(bytes.fromhex("19190024"))

    def test_quantize(self):
        codec = p.dpi_codec("step50")
        self.assertEqual(codec.quantize(1299), 1300)
        with self.assertRaises(p.ProtocolError):
            codec.quantize(50000)


class BatteryTests(unittest.TestCase):
    def test_two_bytes(self):
        battery = p.parse_battery(bytes([100, 0]), declared=2)
        self.assertEqual(battery.percent, 100)
        self.assertFalse(battery.charging)
        self.assertIsNone(battery.voltage_mv)

    def test_four_bytes(self):
        battery = p.parse_battery(bytes([87, 1, 0x0F, 0xA0]), declared=4)
        self.assertEqual(battery.percent, 87)
        self.assertTrue(battery.charging)
        self.assertEqual(battery.voltage_mv, 4000)

    def test_padding_is_not_voltage(self):
        battery = p.parse_battery(bytes([100, 0, 0x0F, 0xA0]), declared=2)
        self.assertIsNone(battery.voltage_mv)

    def test_invalid_percent(self):
        battery = p.parse_battery(bytes([255, 0]), declared=2)
        self.assertIsNone(battery.percent)


class ButtonTests(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(p.describe_button_action(0x01, 0x01, 0), "Linksklick")
        self.assertEqual(p.describe_button_action(0x01, 0x02, 0), "Rechtsklick")
        self.assertEqual(p.describe_button_action(0x02, 0x01, 0), "DPI durchschalten")
        self.assertEqual(p.describe_button_action(0x0B, 0x01, 0), "rad hoch")
        self.assertEqual(p.describe_button_action(0x00, 0, 0), "deaktiviert")
        self.assertIn("Makro", p.describe_button_action(0x06, 3, 0))

    def test_unknown_class_is_reported(self):
        self.assertIn("unbekannte Klasse", p.describe_button_action(0x77, 1, 2))


class CatalogTests(unittest.TestCase):
    def test_a9_ultra_by_cid_mid(self):
        model = catalog.find_model(0x373B, 0x101A, "2,87")
        self.assertIsNotNone(model)
        assert model
        self.assertEqual(model.name, "ATK A9 Ultra")
        self.assertEqual(model.sensor, "PAW3950Ultra")
        self.assertEqual(model.sensor_info.family, "ultra")

    def test_dongle_pid_is_not_a_model(self):
        model = catalog.find_model(0x373B, 0x101A)
        assert model
        self.assertIn("1K Dongle", model.name)

    def test_polling_limited_by_receiver(self):
        self.assertEqual(catalog.polling_options(0x101A), (125, 250, 500, 1000))
        self.assertEqual(catalog.polling_options(0x101B), (125, 250, 500, 1000, 2000, 4000, 8000))

    def test_catalog_size(self):
        self.assertGreater(len(catalog.MODELS), 150)


class UeventTests(unittest.TestCase):
    def test_button_bitmask(self):
        # /sys/class/input/event9: KEY=1f0000 0 0 0 0  -> BTN_LEFT..BTN_EXTRA
        bits = hidraw.parse_uevent_bitmask("1f0000 0 0 0 0")
        self.assertEqual({0x110, 0x111, 0x112, 0x113, 0x114} - bits, set())

    def test_key_bitmask(self):
        # System Control: KEY_POWER (116), KEY_SLEEP (142), KEY_WAKEUP (143)
        bits = hidraw.parse_uevent_bitmask("c000 10000000000000 0")
        self.assertTrue({116, 142, 143}.issubset(bits))

    def test_ev_bitmask(self):
        bits = hidraw.parse_uevent_bitmask("17")
        self.assertEqual(bits, {0, 1, 2, 4})


class DescriptorTests(unittest.TestCase):
    def test_vendor_output_report(self):
        # vereinfachter Vendor-Descriptor: Report 0x08, 16 Byte input + output
        descriptor = bytes.fromhex(
            "0602ff"      # Usage Page (0xff02)
            "0902"        # Usage (0x02)
            "a101"        # Collection (Application)
            "8508"        # Report ID (8)
            "1500"        # Logical Minimum (0)
            "26ff00"      # Logical Maximum (255)
            "7508"        # Report Size (8)
            "9510"        # Report Count (16)
            "0901"        # Usage (0x01)
            "8102"        # Input (Data,Var)
            "0901"        # Usage (0x01)
            "9102"        # Output (Data,Var)
            "c0"          # End Collection
        )
        info = hidraw.parse_report_descriptor(descriptor)
        self.assertTrue(info.has_output_report(0x08, 16))
        self.assertTrue(info.reports[0x08].input)
        self.assertTrue(info.vendor_collections())


if __name__ == "__main__":
    unittest.main()

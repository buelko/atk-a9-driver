"""Geraetetest gegen das simulierte ATK-Geraet (atk_a9.fake).

Damit lassen sich Lese-/Schreibpfad, Read-Modify-Write und Pruefsummen
verifizieren, ohne die echte Maus zu veraendern.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atk_a9 import protocol as p  # noqa: E402
from atk_a9.device import AtkMouse, WriteVerificationError  # noqa: E402
from atk_a9.fake import FakeAtkDevice  # noqa: E402
from atk_a9.hidraw import HidrawNode, ReportInfo, ReportSizes  # noqa: E402


def fake_node(product: int = 0x101A) -> HidrawNode:
    reports = ReportInfo(reports={0x08: ReportSizes(input=16, output=16, feature=7)})
    return HidrawNode(
        path="/dev/hidraw1",
        name="Compx ATK Mouse 1K Dongle",
        vendor=0x373B,
        product=product,
        interface=1,
        reports=reports,
        physical="usb-0000:80:14.0-1/input1",
    )


def connect(product: int = 0x101A) -> AtkMouse:
    transport = FakeAtkDevice()
    mouse = AtkMouse(transport, fake_node(product))
    mouse.identify()
    return mouse


class IdentificationTests(unittest.TestCase):
    def test_cid_mid_resolves_model(self):
        mouse = connect()
        self.assertEqual(mouse.cid_mid, "2,87")
        assert mouse.model
        self.assertEqual(mouse.model.name, "ATK A9 Ultra")
        self.assertEqual(mouse.system_row_length(), p.SYSTEM_LEN)

    def test_firmware_and_battery(self):
        mouse = connect()
        self.assertEqual(mouse.firmware_version(), "1.30")
        status = mouse.status()
        assert status.battery
        self.assertEqual(status.battery.percent, 100)
        self.assertFalse(status.battery.charging)

    def test_receiver_caps_polling(self):
        mouse = connect(0x101A)
        self.assertEqual(mouse.max_polling_hz(), 1000)
        self.assertEqual(mouse.polling_options(), (125, 250, 500, 1000))
        mouse8k = connect(0x101B)
        self.assertEqual(mouse8k.max_polling_hz(), 8000)

    def test_receiver_rejects_too_high_rate(self):
        mouse = connect(0x101A)
        with self.assertRaises(ValueError):
            mouse.set_polling(2000)


class StatusTests(unittest.TestCase):
    def test_status_snapshot(self):
        mouse = connect()
        status = mouse.status()
        assert status.system
        self.assertEqual(status.system.polling_hz, 1000)
        self.assertEqual(status.system.stage_count, 4)
        self.assertEqual(status.system.active_stage, 2)
        self.assertEqual(status.lod_mm, 1.0)
        self.assertEqual([s.x for s in status.dpi_stages], [800, 1600, 3200, 6400])
        assert status.advanced
        self.assertEqual(status.advanced.debounce_ms, 2)
        self.assertTrue(status.advanced.motion_sync)
        self.assertEqual(status.advanced.sleep_seconds, 120)
        assert status.sensor_row
        self.assertEqual(status.sensor_row.performance_mode, "competitive")
        self.assertEqual(len(status.buttons), 6)
        self.assertEqual(status.buttons[0].label, "Linksklick")
        self.assertEqual(status.buttons[3].label, "DPI durchschalten")
        self.assertEqual(status.dpi_stages[0].color, (255, 0, 0))

    def test_status_is_json_serialisable(self):
        import json

        status = connect().status()
        json.dumps(status.to_dict())


class WriteTests(unittest.TestCase):
    def test_set_dpi_stage_roundtrip(self):
        mouse = connect()
        stage = mouse.set_dpi_stage(1, 1250)
        self.assertEqual((stage.x, stage.y), (1250, 1250))
        self.assertEqual(mouse.read_dpi_stages()[0].x, 1250)
        # benachbarte Stufe bleibt unangetastet
        self.assertEqual(mouse.read_dpi_stages()[1].x, 1600)

    def test_set_dpi_xy_independent(self):
        mouse = connect()
        mouse.set_dpi_stage(3, 2000, 2500)
        stage = mouse.read_dpi_stages()[2]
        self.assertEqual((stage.x, stage.y), (2000, 2500))

    def test_dpi_out_of_range(self):
        mouse = connect()
        with self.assertRaises(ValueError):
            mouse.set_dpi_stage(1, 50000)

    def test_select_stage(self):
        mouse = connect()
        system = mouse.select_stage(4)
        self.assertEqual(system.active_stage, 4)
        self.assertEqual(system.polling_hz, 1000)
        self.assertEqual(system.stage_count, 4)

    def test_set_polling_keeps_other_pairs(self):
        mouse = connect()
        system = mouse.set_polling(500)
        self.assertEqual(system.polling_hz, 500)
        self.assertEqual(system.stage_count, 4)
        self.assertEqual(system.active_stage, 2)

    def test_set_lod(self):
        mouse = connect()
        code, mm = mouse.set_lod("1.7")
        self.assertEqual((code, mm), (11, 1.7))
        code, mm = mouse.set_lod("code:2")
        self.assertEqual((code, mm), (2, 0.8))

    def test_lod_rejects_invalid(self):
        mouse = connect()
        with self.assertRaises(ValueError):
            mouse.set_lod("3.5")

    def test_advanced_settings(self):
        mouse = connect()
        advanced = mouse.set_advanced(debounce_ms=4, motion_sync=False, sleep_seconds=300)
        self.assertEqual(advanced.debounce_ms, 4)
        self.assertFalse(advanced.motion_sync)
        self.assertEqual(advanced.sleep_seconds, 300)
        self.assertTrue(advanced.straight_line)
        self.assertTrue(advanced.ripple)

    def test_advanced_rejects_unknown_values(self):
        mouse = connect()
        with self.assertRaises(ValueError):
            mouse.set_advanced(debounce_ms=3)
        with self.assertRaises(ValueError):
            mouse.set_advanced(sleep_seconds=45)

    def test_sensor_mode(self):
        mouse = connect()
        self.assertEqual(mouse.set_sensor_mode("basic").performance_mode, "basic")
        self.assertEqual(mouse.set_sensor_mode("competitive").performance_mode, "competitive")

    def test_dpi_color(self):
        mouse = connect()
        mouse.set_dpi_color(2, (10, 20, 30))
        self.assertEqual(mouse.read_dpi_colors(2)[1], (10, 20, 30))

    def test_button_write(self):
        mouse = connect()
        record = mouse.set_button(4, int(p.ButtonClass.MOUSE), p.MOUSE_BUTTON_ALIASES["back"])
        self.assertEqual(record.label, "Zurueck")
        self.assertEqual(mouse.read_buttons()[3].label, "Zurueck")

    def test_button_checksum(self):
        mouse = connect()
        record = mouse.set_button(5, int(p.ButtonClass.DPI), 2)
        self.assertEqual(sum(record.raw) & 0xFF, p.CHECKSUM_TARGET)

    def test_stage_count(self):
        mouse = connect()
        system = mouse.set_stage_count(6)
        self.assertEqual(system.stage_count, 6)
        self.assertEqual(len(mouse.read_dpi_stages()), 6)


class SafetyTests(unittest.TestCase):
    def test_write_verification_detects_mismatch(self):
        mouse = connect()

        def sabotage(address: int, data: bytes, verify: bool = True, chunk: int = p.MAX_DATA) -> None:
            # Geraet "verschluckt" still ein Byte: Readback muss scheitern
            mouse.transport.eeprom[address] = data[0] ^ 0xFF

        mouse.write_eeprom = sabotage  # type: ignore[method-assign]
        with self.assertRaises(WriteVerificationError):
            mouse.set_dpi_stage(1, 1250)

    def test_device_rejects_bad_checksum_write(self):
        mouse = connect()
        with self.assertRaises(p.ProtocolError):
            mouse.raw_write(0x00A9, bytes([0x02, 0x00, 0x01, 0x54, 0x0C, 0x49, 0x01, 0x54, 0x01, 0x54]))

    def test_read_requires_declared_length(self):
        """Das simulierte Geraet verhaelt sich wie die echte Firmware: ohne
        deklarierte Laenge kommt status=1 zurueck."""
        mouse = connect()
        with self.assertRaises(p.ProtocolError):
            mouse._exchange(p.Frame(cmd=p.Cmd.GET_EEPROM, address=0x0000, declared=0), expect=(0x07, 0x08))

    def test_raw_dump(self):
        mouse = connect()
        data = mouse.dump(0x0000, 0x0013)
        self.assertEqual(len(data), 0x14)
        self.assertEqual(data[0], 0x01)  # 1000 Hz


class ExportImportTests(unittest.TestCase):
    def test_export_has_all_sections(self):
        data = connect().export_settings()
        for key in (
            "version", "polling_hz", "stage_count", "dpi", "advanced",
            "sensor_mode", "buttons", "macros", "wheel_guard_ms",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["version"], AtkMouse.SETTINGS_VERSION)
        self.assertTrue(data["dpi"])
        self.assertIn("color", data["dpi"][0])

    def test_import_dry_run_writes_nothing(self):
        mouse = connect()
        data = mouse.export_settings()
        before = len(mouse.transport.writes)
        changes = mouse.import_settings(data, dry_run=True)
        self.assertEqual(len(mouse.transport.writes), before)
        self.assertTrue(all(c.startswith("[Vorschau]") for c in changes))

    def test_import_roundtrip_no_skips(self):
        mouse = connect()
        data = mouse.export_settings()
        changes = mouse.import_settings(data, dry_run=False)
        skipped = [c for c in changes if c.startswith("uebersprungen")]
        self.assertEqual(skipped, [])

    def test_import_changes_dpi(self):
        mouse = connect()
        data = mouse.export_settings()
        data["dpi"][0]["x"] = data["dpi"][0]["y"] = 1200
        mouse.import_settings(data, dry_run=False)
        stages = mouse.read_dpi_stages(1)
        self.assertEqual(stages[0].x, 1200)

    def test_import_rejects_unknown_version(self):
        mouse = connect()
        with self.assertRaises(ValueError):
            mouse.import_settings({"version": 999}, dry_run=False)

    def test_import_skips_invalid_value(self):
        mouse = connect()
        data = mouse.export_settings()
        data["polling_hz"] = 99999  # ungueltig
        changes = mouse.import_settings(data, dry_run=False)
        self.assertTrue(any(c.startswith("uebersprungen") and "Polling" in c for c in changes))

    def test_set_dpi_color_roundtrip(self):
        mouse = connect()
        mouse.set_dpi_color(2, (0x12, 0x34, 0x56))
        colors = mouse.read_dpi_colors(2)
        self.assertEqual(colors[1], (0x12, 0x34, 0x56))


if __name__ == "__main__":
    unittest.main()

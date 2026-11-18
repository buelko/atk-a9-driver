"""Tests fuer GUI-/Tray-Logik, die ohne Fenster auskommt, und fuer das
Einsparen unnoetiger EEPROM-Schreibvorgaenge."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atk_a9 import protocol as p  # noqa: E402
from atk_a9.device import AtkMouse  # noqa: E402
from atk_a9.fake import FakeAtkDevice  # noqa: E402
from atk_a9.hidraw import HidrawNode, ReportInfo, ReportSizes  # noqa: E402


def connect(product: int = 0x101A) -> tuple[AtkMouse, FakeAtkDevice]:
    fake = FakeAtkDevice()
    node = HidrawNode(
        path="/dev/hidraw-test",
        name="Test",
        vendor=0x373B,
        product=product,
        interface=1,
        reports=ReportInfo(reports={0x08: ReportSizes(input=16, output=16)}),
    )
    mouse = AtkMouse(fake, node)
    mouse.identify()
    return mouse, fake


class NoRedundantWriteTests(unittest.TestCase):
    """Die GUI schreibt bei jeder Auswahl - unveraenderte Werte duerfen den
    EEPROM nicht abnutzen."""

    def test_unchanged_settings_are_not_written(self):
        mouse, fake = connect()
        mouse.set_polling(1000)
        mouse.set_stage_count(4)
        mouse.select_stage(2)
        mouse.set_lod(1.0)
        mouse.set_advanced(debounce_ms=2, motion_sync=True, sleep_seconds=120)
        mouse.set_sensor_mode("competitive")
        mouse.set_dpi_stage(1, 800)
        mouse.set_button(1, 0x01, 0x01)
        self.assertEqual(fake.writes, [])

    def test_changed_settings_are_written_once(self):
        mouse, fake = connect()
        self.assertEqual(mouse.set_polling(500).polling_hz, 500)
        self.assertEqual(mouse.set_dpi_stage(1, 1250).x, 1250)
        self.assertEqual(mouse.set_button(5, 0x01, 0x08).value1, 0x08)
        self.assertEqual(len(fake.writes), 3)

    def test_partial_change_in_row_is_written(self):
        mouse, fake = connect()
        row = mouse.set_advanced(debounce_ms=2, motion_sync=False)
        self.assertFalse(row.motion_sync)
        self.assertEqual(row.debounce_ms, 2)
        self.assertEqual(len(fake.writes), 1)

    def test_broken_pair_checksum_is_repaired(self):
        """Gleicher Wert, aber kaputtes Pruefbyte: muss geschrieben werden."""
        mouse, fake = connect()
        fake.eeprom[0x0001] ^= 0xFF
        mouse.set_polling(1000)
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(bytes(fake.eeprom[0:2]), p.pair(0x01))


class StatusOptionTests(unittest.TestCase):
    def test_options_follow_model_and_receiver(self):
        mouse, _ = connect(0x101A)  # 1K-Dongle, A9 Ultra
        status = mouse.status()
        self.assertEqual(status.polling_options, (125, 250, 500, 1000))
        self.assertEqual(status.lod_options[0], 0.7)
        self.assertEqual(status.lod_options[-1], 1.7)
        self.assertEqual(len(status.lod_options), 11)
        self.assertIn(status.lod_mm, status.lod_options)
        mouse8k, _ = connect(0x101B)
        self.assertIn(8000, mouse8k.status().polling_options)
        self.assertIn("polling_options", status.to_dict())


class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from atk_a9 import gui
        except ImportError as exc:  # tkinter fehlt
            self.skipTest(str(exc))
        self.gui = gui
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "gui.sock")

    def test_second_start_pokes_the_first(self):
        first = self.gui.acquire_instance(self.path)
        assert first is not None
        self.addCleanup(first.close)
        self.assertIsNone(self.gui.acquire_instance(self.path))
        conn, _ = first.accept()  # die GUI holt daraufhin ihr Fenster nach vorn
        conn.close()

    def test_stale_socket_of_crashed_instance_is_replaced(self):
        first = self.gui.acquire_instance(self.path)
        assert first is not None
        first.close()  # "Absturz": Datei bleibt liegen, niemand lauscht
        self.assertTrue(os.path.exists(self.path))
        second = self.gui.acquire_instance(self.path)
        self.assertIsNotNone(second)
        second.close()


class LowBatteryTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from atk_a9.tray import low_battery_step
        except (ImportError, ValueError) as exc:  # GTK/cairo fehlt
            self.skipTest(str(exc))
        self.step = low_battery_step

    def test_warns_once_until_recovered(self):
        warned = False
        seen = []
        for percent, charging in [(40, False), (15, False), (14, False), (None, False), (12, False),
                                  (17, False), (14, False), (14, True), (10, False), (25, False), (15, False)]:
            warn, warned = self.step(percent, charging, warned)
            seen.append(warn)
        self.assertEqual(
            seen,
            # 40    15    14     None   12     17     14     laden  10    25     15
            [False, True, False, False, False, False, False, False, True, False, True],
        )


if __name__ == "__main__":
    unittest.main()

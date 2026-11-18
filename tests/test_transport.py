"""Tests fuer den hidraw-Transport und den GUI-Worker (ohne Hardware).

Das "Geraet" ist das andere Ende eines SOCK_SEQPACKET-Paares: wie bei hidraw
bleibt jede Nachricht ein eigener Report.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atk_a9 import protocol as p  # noqa: E402
from atk_a9.device import AtkMouse, MouseOffline, WriteVerificationError  # noqa: E402
from atk_a9.fake import FakeAtkDevice  # noqa: E402
from atk_a9.hidraw import (  # noqa: E402
    DeviceBusy,
    DeviceNotFound,
    HidrawNode,
    HidrawTransport,
    ReportInfo,
    ReportSizes,
)


def node(path: str = "/dev/hidraw-test") -> HidrawNode:
    return HidrawNode(
        path=path,
        name="Test",
        vendor=0x373B,
        product=0x101A,
        interface=1,
        reports=ReportInfo(reports={0x08: ReportSizes(input=16, output=16)}),
    )


class SocketDevice:
    """Beantwortet eine Anfrage mit einer vorgegebenen Folge von Reports."""

    def __init__(self, replies) -> None:
        self.host, self.peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.host.setblocking(False)
        self.transport = HidrawTransport(node(), timeout=1.0)
        self.transport.fd = os.dup(self.host.fileno())
        self.requests: list[bytes] = []
        self.thread = threading.Thread(target=self._serve, args=(replies,), daemon=True)
        self.thread.start()

    def _serve(self, replies) -> None:
        self.requests.append(self.peer.recv(64))
        for reply in replies:
            self.peer.send(reply)

    def close(self) -> None:
        self.thread.join(2)
        self.transport.close()
        self.host.close()
        self.peer.close()


def eeprom_reply(address: int, data: bytes) -> bytes:
    return p.Frame(cmd=p.Cmd.GET_EEPROM, address=address, data=data, declared=len(data)).build()


class ExchangeTests(unittest.TestCase):
    def _mouse(self, replies) -> tuple[AtkMouse, SocketDevice]:
        device = SocketDevice(replies)
        self.addCleanup(device.close)
        return AtkMouse(device.transport, node()), device

    def test_reply_for_other_address_is_skipped(self):
        """Antwort auf die Anfrage eines anderen Prozesses darf nicht als
        eigene Antwort durchgehen (sonst schreibt Read-Modify-Write Muell)."""
        foreign = eeprom_reply(0x0060, bytes([0x01, 0x01, 0x00, 0x53]))
        own = eeprom_reply(0x00A9, bytes([0x02, 0x53, 0x01, 0x54]))
        mouse, _ = self._mouse([foreign, own])
        self.assertEqual(mouse.read_eeprom(0x00A9, 4), bytes([0x02, 0x53, 0x01, 0x54]))

    def test_only_foreign_reply_times_out(self):
        mouse, device = self._mouse([eeprom_reply(0x0060, bytes(4))])
        device.transport.timeout = 0.2
        with self.assertRaises(TimeoutError):
            mouse.read_eeprom(0x00A9, 4)

    def test_empty_and_foreign_reports_are_ignored(self):
        own = eeprom_reply(0x0000, bytes([0x01, 0x54]))
        mouse, _ = self._mouse([b"", b"\x01\x00\x05\x00", own])
        self.assertEqual(mouse.read_eeprom(0x0000, 2), bytes([0x01, 0x54]))


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        self.path = handle.name
        self.addCleanup(os.unlink, self.path)

    def _transport(self) -> HidrawTransport:
        transport = HidrawTransport(node(self.path))
        transport.open()
        self.addCleanup(transport.close)
        return transport

    def test_second_handle_is_locked_out(self):
        first, second = self._transport(), self._transport()
        with first.locked():
            with self.assertRaises(DeviceBusy):
                with second.locked(timeout=0.1):
                    pass
        with second.locked(timeout=0.1):
            pass

    def test_lock_is_reentrant(self):
        first, second = self._transport(), self._transport()
        with first.locked():
            with first.locked():
                pass
            # innere Freigabe darf die aeussere Sperre nicht aufheben
            with self.assertRaises(DeviceBusy):
                with second.locked(timeout=0.1):
                    pass

    def test_update_holds_lock_across_read_and_write(self):
        fake = FakeAtkDevice()
        depths: list[int] = []
        original = fake.exchange

        def spy(frame, **kwargs):
            depths.append(fake.lock_depth)
            return original(frame, **kwargs)

        fake.exchange = spy  # type: ignore[method-assign]
        mouse = AtkMouse(fake, node())
        mouse.identify()
        depths.clear()
        mouse.set_polling(500)
        # lesen + schreiben + verifizieren laufen unter der Sperre
        self.assertGreaterEqual(len(depths), 3)
        self.assertTrue(all(depth >= 1 for depth in depths[:3]), depths)


class ButtonTests(unittest.TestCase):
    def test_set_button_after_unprogrammed_slot(self):
        """Ein leerer Slot davor liess read_buttons() abbrechen -> IndexError."""
        fake = FakeAtkDevice()
        fake.eeprom[0x0060 + 4 : 0x0060 + 8] = bytes(4)  # Slot 2 unprogrammiert
        mouse = AtkMouse(fake, node())
        got = mouse.set_button(5, 0x01, 0x08)
        self.assertEqual((got.slot, got.cls, got.value1), (5, 0x01, 0x08))
        self.assertTrue(got.checksum_ok)
        self.assertEqual(bytes(fake.eeprom[0x0070:0x0074]), got.raw)

    def test_set_button_detects_failed_write(self):
        fake = FakeAtkDevice()
        mouse = AtkMouse(fake, node())
        original = fake._eeprom

        def swallow(request):
            response = original(request)
            if request.cmd == p.Cmd.SET_EEPROM:
                fake.eeprom[request.address] ^= 0xFF
            return response

        fake._eeprom = swallow  # type: ignore[method-assign]
        with self.assertRaises(WriteVerificationError):
            mouse.set_button(1, 0x01, 0x02)


class SleepTests(unittest.TestCase):
    def _mouse(self) -> tuple[AtkMouse, FakeAtkDevice]:
        fake = FakeAtkDevice()
        mouse = AtkMouse(fake, node())
        mouse.identify()
        return mouse, fake

    def test_sleeping_mouse_is_reported_as_offline(self):
        mouse, fake = self._mouse()
        fake.asleep = True
        with self.assertRaises(MouseOffline):
            mouse.read_system()
        with self.assertRaises(MouseOffline):
            mouse.set_polling(500)
        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.lock_depth, 0)  # Sperre wieder frei
        fake.asleep = False
        self.assertEqual(mouse.read_system().polling_hz, 1000)

    def test_plain_timeout_stays_timeout_when_mouse_is_online(self):
        mouse, fake = self._mouse()
        original = fake.exchange

        def flaky(frame, **kwargs):
            if frame.cmd == p.Cmd.GET_EEPROM:
                raise TimeoutError("keine Antwort")
            return original(frame, **kwargs)

        fake.exchange = flaky  # type: ignore[method-assign]
        with self.assertRaises(TimeoutError) as ctx:
            mouse.read_system()
        self.assertNotIsInstance(ctx.exception, MouseOffline)

    def test_busy_is_not_mistaken_for_sleep(self):
        mouse, fake = self._mouse()

        def busy(frame, **kwargs):
            raise DeviceBusy("gesperrt")

        fake.exchange = busy  # type: ignore[method-assign]
        with self.assertRaises(DeviceBusy):
            mouse.read_system()

    def test_identify_survives_sleeping_mouse(self):
        fake = FakeAtkDevice()
        fake.asleep = True
        mouse = AtkMouse(fake, node())
        self.assertIsNone(mouse.identify())
        self.assertIsNone(mouse.firmware_version())


class DeviceDetailTests(unittest.TestCase):
    def _mouse(self, **kwargs) -> tuple[AtkMouse, FakeAtkDevice]:
        fake = FakeAtkDevice(**kwargs)
        mouse = AtkMouse(fake, node())
        mouse.identify()
        return mouse, fake

    def test_notes_do_not_pile_up(self):
        fake = FakeAtkDevice(cid_mid=(0, 0))  # unbekanntes Modell -> Hinweis
        mouse = AtkMouse(fake, node())
        for _ in range(5):
            mouse.dpi_family()
        self.assertEqual(len(mouse.notes), 1)

    def test_select_stage_respects_stage_count(self):
        mouse, fake = self._mouse()  # 4 Stufen
        self.assertEqual(mouse.select_stage(4).active_stage, 4)
        writes = len(fake.writes)
        for bad in (0, 5, 9):
            with self.assertRaises(ValueError):
                mouse.select_stage(bad)
        self.assertEqual(len(fake.writes), writes)
        self.assertEqual(fake.lock_depth, 0)

    def test_firmware_version_with_non_bcd_bytes(self):
        mouse, _ = self._mouse(firmware=(0x03, 0x00))
        self.assertEqual(mouse.firmware_version(), "3.0")
        mouse, _ = self._mouse(firmware=(0x1A, 0x0F))
        self.assertEqual(mouse.firmware_version(), "1a.0f")

    def test_single_value_replies_are_not_pairs(self):
        """An der A9 Ultra beobachtet: 0x0E liefert ein rohes Byte, 0x17 zehn
        Nullbytes. Frueher scheiterte beides an der Paar-Pruefsumme."""
        mouse, fake = self._mouse()
        self.assertEqual(mouse.active_profile(), 1)
        fake.profile = 2
        self.assertEqual(mouse.active_profile(), 3)
        self.assertEqual(mouse.get_far_distance(), 0)
        self.assertIsNone(mouse.get_dongle_light())  # Status 1 -> Hinweis statt Absturz
        self.assertTrue(any("Dongle-LED" in note for note in mouse.notes))

    def test_set_profile_reports_rejection(self):
        mouse, _ = self._mouse()
        with self.assertRaises(p.ProtocolError):
            mouse.set_active_profile(1)

    def test_connect_closes_handle_on_failure(self):
        transport = mock.Mock()
        with mock.patch("atk_a9.device.find_config_node", return_value=node()), \
             mock.patch("atk_a9.device.HidrawTransport", return_value=transport), \
             mock.patch("atk_a9.device.list_input_nodes", side_effect=OSError(5, "EIO")):
            with self.assertRaises(OSError):
                AtkMouse.connect()
        transport.close.assert_called_once()


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from atk_a9.gui import Worker
        except ImportError as exc:  # tkinter fehlt
            self.skipTest(str(exc))
        self.worker = Worker()
        self.results: list = []
        self.errors: list[str] = []

    def _run(self, work) -> None:
        self.worker.run_one(work, self.results.append, self.errors.append)

    def _raise(self, exc: BaseException):
        def work(_mouse):
            raise exc

        return work

    def test_survives_unexpected_errors(self):
        mouse = mock.Mock()
        with mock.patch("atk_a9.gui.AtkMouse.connect", return_value=mouse):
            self._run(self._raise(WriteVerificationError("soll/ist")))
            self.assertIs(self.worker.mouse, mouse)  # Verbindung bleibt
            self._run(self._raise(IndexError("list index out of range")))
            self.assertIsNone(self.worker.mouse)
            self._run(lambda _m: 42)
        self.assertEqual(len(self.errors), 2)
        self.assertIn("IndexError", self.errors[1])
        self.assertEqual(self.results, [42])

    def test_reconnects_after_device_error(self):
        first, second = mock.Mock(), mock.Mock()
        with mock.patch("atk_a9.gui.AtkMouse.connect", side_effect=[first, second]) as connect:
            self._run(self._raise(OSError(19, "No such device")))
            first.close.assert_called_once()
            self.assertIsNone(self.worker.mouse)
            self._run(lambda m: m)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(self.results, [second])

    def test_timeout_and_busy_keep_connection(self):
        mouse = mock.Mock()
        with mock.patch("atk_a9.gui.AtkMouse.connect", return_value=mouse) as connect:
            self._run(self._raise(TimeoutError("keine Antwort")))
            self._run(self._raise(DeviceBusy("gesperrt")))
            self._run(self._raise(MouseOffline("schlaeft")))
            self._run(lambda _m: "ok")
        self.assertEqual(connect.call_count, 1)
        mouse.close.assert_not_called()
        self.assertEqual(self.errors[1:], ["gesperrt", "schlaeft"])

    def test_connect_failure_is_reported(self):
        with mock.patch("atk_a9.gui.AtkMouse.connect", side_effect=DeviceNotFound("kein Dongle")):
            self._run(lambda _m: 1)
        self.assertEqual(self.errors, ["kein Dongle"])
        self.assertEqual(self.results, [])


if __name__ == "__main__":
    unittest.main()

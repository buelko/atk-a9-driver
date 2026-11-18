"""Ein simuliertes ATK-Geraet (16-Byte-Vendor-Kanal) fuer Tests ohne Hardware.

Imitiert die fuer echte ATK/VXE-Maeuse verifizierten Protokoll-Eigenheiten:

  * GetEEPROM (0x08) verlangt eine deklarierte Laenge; ohne sie antwortet die
    Firmware mit status=1 und data_len=0.
  * Antworten auf Lesevorgaenge koennen die Kommando-ID des Schreibvorgangs
    (0x07) tragen.
  * Wertepaare (wert, 0x55 - wert) und 4-Byte-Records muessen in der Summe
    0x55 ergeben, sonst antwortet das Geraet mit status=1.
  * Frame-Pruefsumme: Summe aller 17 Bytes & 0xFF == 0x55.
"""

from __future__ import annotations

import contextlib

from . import protocol as p


class FakeAtkDevice:
    """HidrawTransport-kompatibler Ersatz (duck typing)."""

    def __init__(self, cid_mid: tuple[int, int] = (2, 87), firmware: tuple[int, int] = (0x01, 0x30)) -> None:
        self.eeprom = bytearray(0x2000)
        self.timeout = 1.0
        self.verbose = False
        self.opened = 0
        self.lock_depth = 0
        # True: Maus schlaeft - nur der Dongle antwortet noch (0x03 Online-Status)
        self.asleep = False
        self.cid_mid = cid_mid
        self.firmware = firmware
        self.battery = [100, 0]
        self.writes: list[tuple[int, bytes]] = []
        self._seed()

    # -- Transport-Schnittstelle -------------------------------------------

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.opened = max(0, self.opened - 1)

    @contextlib.contextmanager
    def locked(self, timeout: float = 0.0):
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def exchange(self, frame: p.Frame, expect_cmd=None, ok_status=(0x00,), expect_address=None) -> p.Frame:
        request = p.Frame.parse(frame.build())
        expected: tuple[int, ...] = (
            (frame.cmd,)
            if expect_cmd is None
            else (expect_cmd if isinstance(expect_cmd, (tuple, set, frozenset)) else (expect_cmd,))
        )
        if self.asleep and request.cmd != p.Cmd.GET_ONLINE:
            raise TimeoutError("Keine Antwort (simulierter Schlafmodus)")
        response = self._handle(request)
        if expect_address is not None and response.address != expect_address:
            raise p.ProtocolError(
                f"Antwort-Adresse {response.address:#06x} != erwartet {expect_address:#06x}"
            )
        if response.cmd not in expected:
            raise p.ProtocolError(f"Antwort {response.cmd:04x} nicht erwartet {expected}")
        if response.status not in ok_status:
            raise p.ProtocolError(
                f"Geraet meldet Status {response.status:#04x} fuer Kommando {response.cmd:#04x}"
            )
        return p.Frame.parse(response.build())

    # -- Initialisierung ---------------------------------------------------

    def _seed(self) -> None:
        self.eeprom = bytearray(0x2000)
        # Systemzeile: 1000 Hz, 4 Stufen, Stufe 2 aktiv, Rest leer
        self._put_pairs(0x0000, [0x01, 0x04, 0x01, 0x00, 0x00])
        # Lift-off 1.0 mm (Ultra-Skala: code 4)
        self._put_pairs(0x000A, [0x04])
        # DPI-Stufen (ultra-Kodierung): 800 / 1600 / 3200 / 6400
        for index, dpi in enumerate((800, 1600, 3200, 6400)):
            self.eeprom[0x000C + index * 4 : 0x000C + index * 4 + 4] = p.dpi_encode(
                dpi, family="ultra"
            )
        # DPI-Farben
        for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))):
            self.eeprom[0x002C + index * 4 : 0x002C + index * 4 + 4] = bytes(color) + bytes(
                [(p.CHECKSUM_TARGET - sum(color)) & 0xFF]
            )
        # Erweitert: debounce 2 ms, Motion Sync an, Schlaf 120 s, linear an, ripple an
        self._put_pairs(0x00A9, [0x02, 0x01, 12, 0x01, 0x01])
        # Sensor: Schlaf aus, Zeit 0, Wettbewerbs-Modus
        self._put_pairs(0x00B5, [0x00, 0x00, 0x01])
        # Sensor-Winkel: 0 Grad, Angle-Snap aus
        self._put_pairs(0x00BD, [0x00, 0x00])
        # Tasten: links, rechts, mittel, DPI durchschalten, DPI senken, Rad hoch
        actions = [
            (0x01, 0x01, 0x00),
            (0x01, 0x02, 0x00),
            (0x01, 0x04, 0x00),
            (0x02, 0x01, 0x00),
            (0x02, 0x03, 0x00),
            (0x0B, 0x01, 0x00),
        ]
        for index, (cls, v1, v2) in enumerate(actions):
            record = bytes([cls, v1, v2])
            self.eeprom[0x0060 + index * 4 : 0x0060 + index * 4 + 4] = record + bytes(
                [(p.CHECKSUM_TARGET - sum(record)) & 0xFF]
            )

    def _put_pairs(self, address: int, values: list[int]) -> None:
        for index, value in enumerate(values):
            self.eeprom[address + index * 2 : address + index * 2 + 2] = p.pair(value)

    # -- Kommando-Bearbeitung ----------------------------------------------

    def _handle(self, request: p.Frame) -> p.Frame:
        cmd = request.cmd
        if cmd == p.Cmd.GET_CID_MID:
            return p.Frame(cmd=cmd, data=bytes(self.cid_mid), declared=2)
        if cmd == p.Cmd.GET_MOUSE_VERSION:
            return p.Frame(cmd=cmd, data=bytes(self.firmware), declared=2)
        if cmd == p.Cmd.GET_BATTERY:
            return p.Frame(cmd=cmd, data=bytes(self.battery), declared=len(self.battery))
        if cmd == p.Cmd.GET_ONLINE:
            return p.Frame(cmd=cmd, data=bytes([0 if self.asleep else 1, 0xAB, 0xCD, 0xEF]), declared=1)
        if cmd in (p.Cmd.GET_EEPROM, p.Cmd.SET_EEPROM):
            return self._eeprom(request)
        return p.Frame(cmd=cmd, status=0x01)

    def _eeprom(self, request: p.Frame) -> p.Frame:
        address = request.address
        if request.cmd == p.Cmd.GET_EEPROM:
            length = request.declared or 0
            if length == 0:
                # echte Firmware antwortet mit status=1 und leerem Datenbereich
                return p.Frame(cmd=request.cmd, address=address, status=0x01, declared=0)
            data = bytes(self.eeprom[address : address + length])
            return p.Frame(cmd=int(p.Cmd.SET_EEPROM), address=address, data=data, declared=length)
        data = request.data
        if not self._acceptable_write(address, data):
            return p.Frame(cmd=request.cmd, address=address, status=0x01, declared=0)
        self.eeprom[address : address + len(data)] = data
        self.writes.append((address, data))
        return p.Frame(cmd=request.cmd, address=address, data=data, declared=len(data))

    def _acceptable_write(self, address: int, data: bytes) -> bool:
        """Das Geraet lehnt Pruefsummen-Verletzungen ab (beobachtetes Verhalten)."""
        if address <= 0x0009:
            return all((data[i] + data[i + 1]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data) - 1, 2))
        if address == 0x000A:
            return all((data[i] + data[i + 1]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data) - 1, 2))
        if 0x000C <= address <= 0x002B:
            return len(data) % 4 == 0 and all(sum(data[i : i + 4]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data), 4))
        if 0x002C <= address <= 0x004B:
            return len(data) % 4 == 0 and all(sum(data[i : i + 4]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data), 4))
        if 0x0060 <= address <= 0x009F:
            return len(data) % 4 == 0 and all(sum(data[i : i + 4]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data), 4))
        if 0x00A9 <= address <= 0x00B4:
            return all((data[i] + data[i + 1]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data) - 1, 2))
        if 0x00B5 <= address <= 0x00BC:
            return all((data[i] + data[i + 1]) & 0xFF == p.CHECKSUM_TARGET for i in range(0, len(data) - 1, 2))
        return True

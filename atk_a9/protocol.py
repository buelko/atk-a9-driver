"""ATK / Compx 16-Byte HID Protokoll (Report-ID 0x08).

Frame-Aufbau (17 Bytes inkl. Report-ID, 16 Bytes Payload):

    Index  Bedeutung
    -----  ---------------------------------------------------------
    0      Report-ID (0x08)
    1      Kommando (Cmd)
    2      Status   (Anfrage: 0x00, Antwort: 0x00 = ok, 0x01 = Fehler)
    3      Adresse high
    4      Adresse low
    5      Datenlaenge (deklariert)
    6..15  Daten (max. 10 Bytes)
    16     Pruefsumme: Summe aller 17 Bytes & 0xFF == 0x55

Einzelwerte in den EEPROM-Zeilen werden als Wert/Pruefsummen-Paar abgelegt:
``(wert, 0x55 - wert)``. 4-Byte-Records (DPI, Tasten) haben eine eigene
Pruefsumme: alle 4 Bytes summieren auf 0x55.

Die Spezifikation wurde aus offenen Referenzimplementierungen und deren
Hardware-Verifikation uebernommen (siehe docs/PROTOCOL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Sequence

REPORT_ID = 0x08
PAYLOAD_LEN = 16
FRAME_LEN = PAYLOAD_LEN + 1
CHECKSUM_TARGET = 0x55
MAX_DATA = 10


class ProtocolError(ValueError):
    """Frame ist ungueltig, Pruefsumme passt nicht oder Geraet meldet Fehler."""


class Cmd(IntEnum):
    DOWNLOAD_DATA = 0x01
    DOWNLOAD_STATUS = 0x02
    GET_ONLINE = 0x03
    GET_BATTERY = 0x04
    SET_PAIR = 0x05
    GET_PAIR_RESULT = 0x06
    SET_EEPROM = 0x07
    GET_EEPROM = 0x08
    RESTORE_FACTORY = 0x09
    REPORT_MOUSE_STATUS = 0x0A
    ENTER_BOOT_MODE = 0x0D
    GET_CURRENT_CONFIG = 0x0E
    SET_CURRENT_CONFIG = 0x0F
    GET_CID_MID = 0x10
    GET_MOUSE_VERSION = 0x12
    DONGLE_EXIT_PAIR = 0x13
    SET_4K_RGB = 0x14
    GET_4K_RGB = 0x15
    SET_FAR_DISTANCE = 0x16
    GET_FAR_DISTANCE = 0x17
    SET_DONGLE_LIGHT = 0x18
    GET_DONGLE_LIGHT = 0x19
    GET_RECEIVER_VERSION = 0x1D

    # Aliase (identische Werte, bequemere Namen in der CLI)
    START_PAIRING = 0x05
    GET_PAIRING = 0x06
    FACTORY_RESET = 0x09
    CANCEL_PAIRING = 0x13


ATK_VID = 0x373B  # ATK / Compx
ATK_ALT_VID = 0x3554  # VXE / Nordic


class Eeprom(IntEnum):
    """EEPROM-Adressen (NK/COMPX-Map, identisch zu libatk-rs)."""

    SYSTEM = 0x0000  # [polling, crc, stufen, crc, aktive stufe, crc, ..., ...]
    LOD = 0x000A  # [wert, crc]
    DPI_BASE = 0x000C  # 8 x 4 Byte Records (Stufe 1..8)
    DPI_COLOR_BASE = 0x002C  # 8 x [r, g, b, crc]
    RGB = 0x004C  # [effekt, crc, helligkeit, crc, tempo, crc, aktiv, crc]
    LAMP2 = 0x0054  # [r, g, b, crc, effekt, crc, helligkeit, crc, tempo, crc, stromsparen, crc]
    KEYS = 0x0060  # 16 x [klasse, wert1, wert2, crc]
    ADVANCED = 0x00A9  # [debounce, crc, motion_sync, crc, sleep, crc, linear, crc, ripple, crc]
    MOVE_CLOSE_LIGHTS = 0x00B3
    SENSOR = 0x00B5  # [sensor_sleep_aktiv, crc, sensor_sleep_zeit, crc, sensor_modus, crc]
    RF_TX_TIME = 0x00BB
    SENSOR_ANGLE = 0x00BD  # [winkel, crc, schalter, crc]
    KEY_SHORTCUTS = 0x0100  # 16 x 32 Byte
    MACROS = 0x0300  # 16 x 384 Byte


SYSTEM_LEN = 6
SYSTEM_ROW_LEN = 10
LOD_LEN = 2
DPI_RECORD_LEN = 4
DPI_STAGE_COUNT_MAX = 8
DPI_COLOR_LEN = 4
ADVANCED_LEN = 10
SENSOR_LEN = 6
KEY_RECORD_LEN = 4
KEY_COUNT = 16
SHORTCUT_SLOT_LEN = 32
SHORTCUT_COUNT = 16
MACRO_SLOT_LEN = 384
MACRO_COUNT = 16


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


def frame_checksum(frame: bytes) -> int:
    """Pruefsummenbyte, damit Summe aller 17 Bytes == 0x55 wird."""
    return (CHECKSUM_TARGET - sum(frame[: FRAME_LEN - 1])) & 0xFF


def checksum_ok(frame: bytes) -> bool:
    return len(frame) == FRAME_LEN and (sum(frame) & 0xFF) == CHECKSUM_TARGET


@dataclass
class Frame:
    cmd: int
    address: int = 0
    data: bytes = b""
    status: int = 0
    declared: int | None = None  # deklarierte Datenlaenge (ohne Datenbytes anzufragen)
    raw_data: bytes = b""

    def build(self) -> bytes:
        if len(self.data) > MAX_DATA:
            raise ProtocolError(f"Daten zu lang: {len(self.data)} > {MAX_DATA} Byte")
        declared = len(self.data) if self.declared is None else self.declared
        if declared > MAX_DATA:
            raise ProtocolError(f"deklarierte Laenge {declared} > {MAX_DATA}")
        payload = bytearray(PAYLOAD_LEN)
        payload[0] = int(self.cmd) & 0xFF
        payload[1] = int(self.status) & 0xFF
        payload[2] = (self.address >> 8) & 0xFF
        payload[3] = self.address & 0xFF
        payload[4] = declared & 0xFF
        payload[5 : 5 + len(self.data)] = self.data
        frame = bytearray([REPORT_ID]) + payload
        frame[FRAME_LEN - 1] = frame_checksum(frame)
        return bytes(frame)

    @staticmethod
    def normalize(raw: bytes) -> bytes:
        """Bringt 16-Byte-(ohne Report-ID) und 17-Byte-Frames auf eine Form."""
        raw = bytes(raw)
        if len(raw) == FRAME_LEN:
            return raw
        if len(raw) == PAYLOAD_LEN:
            return bytes([REPORT_ID]) + raw
        raise ProtocolError(f"unerwartete Frame-Laenge {len(raw)} (erwartet 16/17)")

    @classmethod
    def parse(cls, raw: bytes, *, expect_cmd: int | None = None) -> "Frame":
        frame = cls.normalize(raw)
        if frame[0] != REPORT_ID:
            raise ProtocolError(f"Report-ID {frame[0]:#04x} != 0x08")
        if not checksum_ok(frame):
            raise ProtocolError(
                "Pruefsumme falsch: " + " ".join(f"{b:02x}" for b in frame)
            )
        cmd = frame[1]
        if expect_cmd is not None and cmd not in (expect_cmd if isinstance(expect_cmd, (tuple, set, frozenset)) else (expect_cmd,)):
            raise ProtocolError(f"Kommando {cmd:#04x} != erwartet {expect_cmd}")
        length = frame[5]
        if length > MAX_DATA:
            raise ProtocolError(f"deklarierte Datenlaenge {length} > {MAX_DATA}")
        return cls(
            cmd=cmd,
            address=(frame[3] << 8) | frame[4],
            data=bytes(frame[6 : 6 + length]),
            status=frame[2],
            declared=length,
            raw_data=bytes(frame[6:16]),
        )

    def hex(self) -> str:
        return " ".join(f"{b:02x}" for b in self.build())


def pair(value: int) -> bytes:
    """Wert + Komplementaer-Pruefsumme (0x55 - wert)."""
    value &= 0xFF
    return bytes([value, (CHECKSUM_TARGET - value) & 0xFF])


def unpair(data: bytes | int, offset: int = 0) -> int:
    """Liest ein Wert/Pruefsummen-Paar; Fehler, wenn die Summe nicht 0x55 ist.

    Akzeptiert entweder (zeile, offset) oder direkt (wert, pruefsumme).
    """
    if isinstance(data, int):
        data = bytes((data, offset))
        offset = 0
    if len(data) < offset + 2:
        raise ProtocolError("Zeile zu kurz fuer Paar bei Offset " + str(offset))
    value, crc = data[offset], data[offset + 1]
    if (value + crc) & 0xFF != CHECKSUM_TARGET:
        raise ProtocolError(
            f"Paar bei Offset {offset} defekt: {value:02x} {crc:02x} (Summe != 0x55)"
        )
    return value


def is_unprogrammed(data: bytes, offset: int = 0, width: int = 4) -> bool:
    """Nicht programmierte Zeilen werden als 0xff ... 0xff geliefert."""
    chunk = data[offset : offset + width]
    return len(chunk) == width and all(b == 0xFF for b in chunk)


# ---------------------------------------------------------------------------
# Enkodierung: Polling, Schlaf, Debounce, Lift-off
# ---------------------------------------------------------------------------

POLLING_CODES: dict[int, int] = {
    125: 0x08,
    250: 0x04,
    500: 0x02,
    1000: 0x01,
    2000: 0x10,
    4000: 0x20,
    8000: 0x40,
}
POLLING_BY_CODE = {code: hz for hz, code in POLLING_CODES.items()}
DEBOUNCE_MS = (0, 1, 2, 4, 8, 15, 20)
SLEEP_SECONDS = (10, 30, 50, 60, 120, 180, 300, 600, 900, 1200, 1500, 1800)


def polling_code(hz: int) -> int:
    try:
        return POLLING_CODES[hz]
    except KeyError as exc:
        raise ProtocolError(
            f"Polling {hz} Hz nicht unterstuetzt ({', '.join(map(str, POLLING_CODES))})"
        ) from exc


def polling_hz(code: int) -> int | None:
    return POLLING_BY_CODE.get(code)


def polling_to_code(hz: int) -> "int | None":
    return POLLING_CODES.get(hz)


def polling_from_code(code: int) -> "int | None":
    return POLLING_BY_CODE.get(code)


def sleep_code(seconds: int) -> int:
    if seconds % 10:
        seconds = (seconds // 10) * 10
    code = seconds // 10
    if not 1 <= code <= 0xFF:
        raise ProtocolError(f"Schlafzeit {seconds}s ausserhalb des Bereichs")
    return code


def sleep_seconds(code: int) -> int:
    return code * 10


@dataclass(frozen=True)
class LodTable:
    """Lift-off-Tabelle pro Sensorfamilie (Code -> Millimeter)."""

    codes: dict[int, float]

    def mm(self, code: int) -> float | None:
        return self.codes.get(code)

    def code(self, mm: float) -> "int | None":
        for code, value in self.codes.items():
            if abs(value - mm) < 1e-6:
                return code
        return None


# "ultra" = kontinuierliche Skala 0.7..1.7 mm (PAW3950Ultra / PAW3395Ultra):
#   code = mm*10 - 6,  mm = (code + 6)/10,  gueltige Codes 1..11
ULTRA_LOD = LodTable({code: round((code + 6) / 10, 2) for code in range(1, 12)})
# PAW3950 (z. B. X1 Pro Max): Low/Medium/High
STEP_LOD = LodTable({1: 1.0, 2: 2.0})
PAW3950_LOD = LodTable({3: 0.7, 1: 1.0, 2: 2.0})

LOD_TABLES: dict[str, LodTable] = {
    "PAW3950Ultra": ULTRA_LOD,
    "PAW3395Ultra": ULTRA_LOD,
    "PAW3950": PAW3950_LOD,
    "PAW3950DM": PAW3950_LOD,
    "PAW3395": STEP_LOD,
    "PAW3395SE": STEP_LOD,
    "CORE26K": STEP_LOD,
    "PAW3311": STEP_LOD,
    "PAW3315": STEP_LOD,
    "PAW3320": STEP_LOD,
}


def lod_table(sensor: str | None) -> LodTable:
    return LOD_TABLES.get(sensor or "", ULTRA_LOD)


def lod_from_code(code: int, sensor: str | None = None) -> float | None:
    return lod_table(sensor).mm(code)


def lod_to_code(value: "str | int | float", sensor: str | None = None) -> int | None:
    """Akzeptiert Millimeter ("1.2", "2") oder rohen Code ("code:2")."""
    table = lod_table(sensor)
    text = str(value).strip().lower()
    if text.startswith("code:"):
        code = int(text[5:])
        return code if code in table.codes else None
    mm = float(text)
    return table.code(mm)


def lod_options(sensor: str | None = None) -> tuple[float, ...]:
    return tuple(sorted(lod_table(sensor).codes.values()))


# ---------------------------------------------------------------------------
# DPI-Codecs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DpiCodec:
    """Uebersetzt DPI-Werte in den 4-Byte-Record [x_byte, y_byte, mode, crc].

    Modus-Nibble pro Achse: Bits 2-3 = Wertverlaengerung, Bit 1 = 50-DPI-
    Bereich oberhalb 10 000, Bit 0 = Verdopplung (oberhalb 30 000).
    """

    family: str  # ultra | step50 | paw3395se | stepped
    min_dpi: int
    max_dpi: int
    step: int = 100  # nur fuer "stepped" (PAW3311/PAW3315/PAW3320)
    codes: tuple[int, ...] | None = None  # feste Codeliste (PAW3395SE)

    # -- Kodierung ------------------------------------------------------
    def encode_axis(self, dpi: int) -> tuple[int, int]:
        dpi = self.quantize(dpi)
        if self.family == "stepped":
            count = round((dpi - self.min_dpi) / self.step)
            return count & 0xFF, ((count >> 8) & 0x03) << 2
        if self.family == "step50":
            doubled = dpi > 30000
            count = round(dpi / (100 if doubled else 50)) - 1
            return count & 0xFF, (((count >> 8) & 0x03) << 2) | (1 if doubled else 0)
        if self.family == "paw3395se":
            doubled = dpi > 10000
            base = dpi // 2 if doubled else dpi
            index = base // 50 - 1
            if self.codes is None or not 0 <= index < len(self.codes):
                raise ProtocolError(f"DPI {dpi} nicht kodierbar ({self.family})")
            return self.codes[index], (2 if doubled else 0)
        # ultra (PAW3950Ultra): 10-DPI-Schritte bis 10 000, dann 50, dann verdoppelt
        value = dpi
        doubled = 0
        if value > 30000:
            doubled = 1
            value = round(value / 2)
        if value <= 10000:
            code = value // 10 - 1
            nibble = ((code >> 8) & 0x03) << 2
        else:
            code = (value - 10050) // 50
            nibble = (((code >> 8) & 0x03) << 2) | 0x02
        return code & 0xFF, nibble | doubled

    def decode_axis(self, byte: int, nibble: int) -> int:
        if self.family == "stepped":
            count = byte | (((nibble >> 2) & 0x03) << 8)
            return self.min_dpi + count * self.step
        if self.family == "step50":
            count = byte | (((nibble >> 2) & 0x03) << 8)
            dpi = (count + 1) * 50
            return dpi * 2 if nibble & 0x01 else dpi
        if self.family == "paw3395se":
            if self.codes is None or byte not in self.codes:
                return 0
            base = (self.codes.index(byte) + 1) * 50
            return base * 2 if nibble & 0x02 else base
        code = byte | (((nibble >> 2) & 0x03) << 8)
        base = 10050 + code * 50 if nibble & 0x02 else (code + 1) * 10
        return base * 2 if nibble & 0x01 else base

    # -- Wertebereich ---------------------------------------------------
    def allowed_values(self) -> tuple[int, ...]:
        if self.family == "stepped":
            return tuple(range(self.min_dpi, self.max_dpi + 1, self.step))
        if self.family == "paw3395se":
            return tuple(range(self.min_dpi, 10001, 50)) + tuple(
                range(10100, self.max_dpi + 1, 100)
            )
        if self.family == "step50":
            return tuple(range(self.min_dpi, min(self.max_dpi, 30000) + 1, 50)) + tuple(
                range(30100, self.max_dpi + 1, 100)
            )
        return (
            tuple(range(self.min_dpi, 10001, 10))
            + tuple(range(10050, min(self.max_dpi, 30000) + 1, 50))
            + tuple(range(30100, self.max_dpi + 1, 100))
        )

    def quantize(self, dpi: int) -> int:
        allowed = self.allowed_values()
        if not allowed[0] <= dpi <= allowed[-1]:
            raise ProtocolError(
                f"DPI {dpi} ausserhalb {allowed[0]}-{allowed[-1]} ({self.family})"
            )
        return min(allowed, key=lambda value: (abs(value - dpi), value))

    def is_valid(self, dpi: int) -> bool:
        return dpi in set(self.allowed_values())

    def encode_record(self, x: int, y: int | None = None) -> bytes:
        y = x if y is None else y
        x_byte, x_nib = self.encode_axis(x)
        y_byte, y_nib = self.encode_axis(y)
        mode = ((y_nib & 0x0F) << 4) | (x_nib & 0x0F)
        crc = (CHECKSUM_TARGET - x_byte - y_byte - mode) & 0xFF
        return bytes([x_byte, y_byte, mode, crc])

    def decode_record(self, record: bytes) -> tuple[int, int]:
        if len(record) != DPI_RECORD_LEN:
            raise ProtocolError("DPI-Record hat keine 4 Byte")
        x_byte, y_byte, mode, crc = record
        if (x_byte + y_byte + mode + crc) & 0xFF != CHECKSUM_TARGET:
            raise ProtocolError(
                "DPI-Record Pruefsumme falsch: " + " ".join(f"{b:02x}" for b in record)
            )
        if mode == 0xFF and x_byte == 0xFF:
            return 0, 0
        return (
            self.decode_axis(x_byte, mode & 0x0F),
            self.decode_axis(y_byte, (mode >> 4) & 0x0F),
        )


# PAW3395SE: Codes 1..235 ohne eine feste Menge ausgelassener Codes
_PAWS395SE_SKIP = frozenset(
    (7, 13, 20, 26, 33, 40, 46, 53, 60, 66, 73, 80, 86, 93, 100, 106, 113,
     120, 126, 133, 140, 146, 153, 160, 166, 173, 180, 186, 193, 200, 206,
     213, 220, 226, 233)
)
PAW3395SE_CODES = tuple(c for c in range(1, 236) if c not in _PAWS395SE_SKIP)

def make_dpi_codec(
    family: str, min_dpi: int, max_dpi: int, step: int = 100
) -> DpiCodec:
    if family == "paw3395se":
        return DpiCodec(family, min_dpi, max_dpi, codes=PAW3395SE_CODES)
    return DpiCodec(family, min_dpi, max_dpi, step=step)


DPI_CODECS: dict[str, DpiCodec] = {
    "ultra": make_dpi_codec("ultra", 10, 42000),
    "step50": make_dpi_codec("step50", 50, 36000),
    "paw3395se": make_dpi_codec("paw3395se", 200, 18000),
    "stepped": make_dpi_codec("stepped", 200, 15000, step=100),
}
DPI_FAMILIES: tuple[str, ...] = tuple(DPI_CODECS)


def dpi_codec(family: str, min_dpi: int | None = None, max_dpi: int | None = None) -> DpiCodec:
    codec = DPI_CODECS.get(family)
    if codec is None:
        raise ProtocolError(f"unbekannte DPI-Kodierung {family!r} ({', '.join(DPI_FAMILIES)})")
    if min_dpi or max_dpi:
        codec = DpiCodec(
            codec.family,
            min_dpi or codec.min_dpi,
            max_dpi or codec.max_dpi,
            step=codec.step,
            codes=codec.codes,
        )
    return codec


def dpi_encode(x: int, y: "int | None" = None, family: str = "ultra") -> bytes:
    return dpi_codec(family).encode_record(x, y)


def dpi_decode(record: bytes, family: str = "ultra") -> "tuple[int, int]":
    try:
        return dpi_codec(family).decode_record(record)
    except ProtocolError:
        return 0, 0


# ---------------------------------------------------------------------------
# Tasten-Matrix
# ---------------------------------------------------------------------------

class ButtonClass(IntEnum):
    DISABLED = 0x00
    MOUSE = 0x01
    DPI = 0x02
    HORIZONTAL_SCROLL = 0x03
    FIREPOWER = 0x04
    SHORTCUT = 0x05
    MACRO = 0x06
    REPORT_RATE = 0x07
    LIGHTING = 0x08
    PROFILE = 0x09
    DPI_LOCK = 0x0A
    WHEEL = 0x0B


BUTTON_CLASS_LABELS: dict[int, str] = {
    ButtonClass.DISABLED: "deaktiviert",
    ButtonClass.MOUSE: "Maustaste",
    ButtonClass.DPI: "DPI-Steuerung",
    ButtonClass.HORIZONTAL_SCROLL: "horizontal scrollen",
    ButtonClass.FIREPOWER: "Fire-Key",
    ButtonClass.SHORTCUT: "Tastenkuerzel (Shortcut-Slot)",
    ButtonClass.MACRO: "Makro (Makro-Slot)",
    ButtonClass.REPORT_RATE: "Polling-Rate umschalten",
    ButtonClass.LIGHTING: "Beleuchtung",
    ButtonClass.PROFILE: "Profil umschalten",
    ButtonClass.DPI_LOCK: "DPI-Sperre",
    ButtonClass.WHEEL: "Mausrad",
}

# class MOUSE: value1 ist eine Bitmaske der Maustasten
MOUSE_BUTTON_BITS: dict[int, str] = {
    0x00: "deaktiviert",
    0x01: "Linksklick",
    0x02: "Rechtsklick",
    0x04: "Mittelklick",
    0x08: "Zurueck",
    0x10: "Vor",
}
MOUSE_BUTTON_ALIASES: dict[str, int] = {
    "left": 0x01,
    "links": 0x01,
    "lmb": 0x01,
    "right": 0x02,
    "rechts": 0x02,
    "rmb": 0x02,
    "middle": 0x04,
    "mittel": 0x04,
    "mmb": 0x04,
    "wheel-click": 0x04,
    "back": 0x08,
    "zurueck": 0x08,
    "side-back": 0x08,
    "forward": 0x10,
    "vor": 0x10,
    "side-forward": 0x10,
}

DPI_ACTION_LABELS: dict[int, str] = {1: "DPI durchschalten", 2: "DPI erhoehen", 3: "DPI senken"}
SCROLL_ACTION_LABELS: dict[int, str] = {1: "links", 2: "rechts"}
WHEEL_ACTION_LABELS: dict[int, str] = {1: "rad hoch", 2: "rad runter"}

BUTTON_SLOTS: tuple[tuple[int, str], ...] = tuple(
    (0x0060 + index * 4, name)
    for index, name in enumerate(
        ("Linkstaste", "Rechtstaste", "Mittelklick", "Taste 4 (zurueck)", "Taste 5 (vor)", "Unterseite", "Taste 7", "Taste 8",
         "Taste 9", "Taste 10", "Taste 11", "Taste 12", "Taste 13", "Taste 14", "Taste 15", "Taste 16")
    )
)


def describe_button_action(cls: int, value1: int, value2: int) -> str:
    if cls == ButtonClass.DISABLED:
        return "deaktiviert"
    if cls == ButtonClass.MOUSE:
        names = [name for bit, name in MOUSE_BUTTON_BITS.items() if bit and value1 & bit]
        return "+".join(names) if names else "deaktiviert"
    if cls == ButtonClass.DPI:
        return DPI_ACTION_LABELS.get(value1, f"DPI-Steuerung 0x{value1:02x}")
    if cls == ButtonClass.HORIZONTAL_SCROLL:
        return "horizontal " + SCROLL_ACTION_LABELS.get(value1, f"0x{value1:02x}")
    if cls == ButtonClass.WHEEL:
        return WHEEL_ACTION_LABELS.get(value1, f"Mausrad 0x{value1:02x}")
    if cls in (ButtonClass.SHORTCUT, ButtonClass.MACRO):
        label = BUTTON_CLASS_LABELS[cls]
        return f"{label} #{value1} (Parameter 0x{value2:02x})"
    base = BUTTON_CLASS_LABELS.get(cls, f"unbekannte Klasse 0x{cls:02x}")
    return f"{base} (wert1=0x{value1:02x}, wert2=0x{value2:02x})"


@dataclass
class ButtonRecord:
    index: int
    address: int
    raw: bytes
    checksum_ok: bool

    @property
    def cls(self) -> int:
        return self.raw[0]

    @property
    def values(self) -> tuple[int, int]:
        return self.raw[1], self.raw[2]

    @property
    def label(self) -> str:
        return describe_button_action(self.cls, self.raw[1], self.raw[2])

    def describe(self) -> str:
        flag = "" if self.checksum_ok else "  [Pruefsumme defekt]"
        return (
            f"Taste {self.index + 1:<2} @{self.address:#06x} "
            f"[{self.raw[0]:02x} {self.raw[1]:02x} {self.raw[2]:02x} {self.raw[3]:02x}] "
            f"{self.label}{flag}"
        )


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


@dataclass
class Battery:
    percent: int | None = None
    charging: bool | None = None
    voltage_mv: int | None = None
    raw: bytes = b""

    @property
    def status_text(self) -> str:
        if self.percent is None:
            return "keine Antwort (Maus offline/geschlafen?)"
        if self.charging is None:
            return f"{self.percent} %"
        return f"{self.percent} % ({'laedt' if self.charging else 'im Betrieb'})"


def parse_battery(data: bytes, declared: "int | None" = None) -> Battery:
    """Dekodierung abhaengig von der deklarierten Datenlaenge.

    Ein deklarierte Laenge von 2 bedeutet: Prozent + Ladestatus, der Rest des
    Frames ist Auffuellung und wird nicht als Spannung gedeutet.
    """
    if declared is not None:
        data = data[:declared]
    battery = Battery(raw=data)
    battery = Battery(raw=data)
    if len(data) >= 1:
        percent = data[0]
        battery.percent = percent if percent <= 100 else None
    if len(data) >= 2:
        battery.charging = bool(data[1])
    if len(data) >= 4:
        battery.voltage_mv = int.from_bytes(data[2:4], "big")
    return battery

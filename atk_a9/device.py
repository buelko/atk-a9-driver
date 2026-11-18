"""Geraetetreiber fuer ATK-/VXE-Maeuse ueber den 16-Byte-Vendor-Kanal (Report 0x08).

Alle EEPROM-Zugriffe folgen dem Muster "lesen - modifizieren - zurueckschreiben",
damit benachbarte Einstellungen (z. B. die Pruefsummen-Paare derselben Zeile)
nicht verrueckt werden.

Quellen der Feld-Bedeutungen (Verifikation durch Auslesen echter Geraete):
  - OpenMouse-Project/mouse-protocol  docs/atk-testing.md + hid.ts
  - libatk-rs (Rust, EEPROMAddress-Enum)
  - VoideUI/Linux-ATK (Rust-CLI)
  - xb-bx/atk-a9-ultra-driver (Odin, ATK A9 Ultra)
  - szabatoro/atkctl (Python, ATK Dragonfly A9 Pro Max 2.0)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

from . import protocol as p
from .catalog import Model, find_model, generic_model, polling_options, RECEIVER_MAX_POLLING
from .hidraw import (
    DeviceBusy,
    DeviceNotFound,
    HidrawNode,
    HidrawTransport,
    InputNode,
    find_config_node,
    list_hidraw_nodes,
    list_input_nodes,
)


class WriteVerificationError(RuntimeError):
    """Geschriebener Wert wurde zurueckgelesen, weicht aber ab."""


class MouseOffline(TimeoutError):
    """Der Dongle antwortet, meldet die Maus aber als nicht verbunden
    (Schlafmodus, ausgeschaltet oder ausser Reichweite)."""


def _bcd(value: int) -> str:
    """Versionsbyte ist BCD-kodiert (0x30 -> '30'); sonst hex anzeigen."""
    text = f"{value:02x}"
    return str(int(text)) if text.isdigit() else text


# ---------------------------------------------------------------------------
# Datensaetze
# ---------------------------------------------------------------------------


@dataclass
class DpiStage:
    index: int  # 1-basiert
    address: int
    x: int | None
    y: int | None
    raw: bytes
    color: tuple[int, int, int] | None = None

    def describe(self) -> str:
        color = ""
        if self.color:
            r, g, b = self.color
            color = f"  #{r:02X}{g:02X}{b:02X}"
        if self.x is None:
            return f"[{self.raw.hex(' ')}] ungueltig{color}"
        same = "" if self.x == self.y else f" / y={self.y}"
        return f"DPI {self.x}{same}{color}"


@dataclass
class SystemRow:
    polling_hz: int | None
    stage_count: int | None
    active_stage: int | None
    raw: bytes
    extra_pairs: tuple[int, ...] = ()

    @property
    def polling_code(self) -> int | None:
        return p.polling_to_code(self.polling_hz) if self.polling_hz else None


@dataclass
class AdvancedRow:
    debounce_ms: int | None
    motion_sync: bool | None
    sleep_seconds: int | None
    straight_line: bool | None
    ripple: bool | None
    raw: bytes


@dataclass
class SensorRow:
    sleep_enabled: bool | None
    sleep_time: int | None
    performance_mode: str | None  # "basic" | "competitive"
    raw: bytes


@dataclass
class Battery:
    percent: int | None
    charging: bool | None
    millivolts: int | None

    def describe(self) -> str:
        parts: list[str] = []
        if self.percent is not None:
            parts.append(f"{self.percent}%")
        if self.charging is not None:
            parts.append("ladend" if self.charging else "nicht ladend")
        if self.millivolts:
            parts.append(f"{self.millivolts} mV")
        return " / ".join(parts) if parts else "keine Antwort"


@dataclass
class ButtonAssignment:
    index: int  # 0-basiert
    address: int
    cls: int
    value1: int
    value2: int
    checksum_ok: bool
    label: str
    raw: bytes

    @property
    def slot(self) -> int:
        return self.index + 1

    def describe(self) -> str:
        flag = "" if self.checksum_ok else "  [Pruefsumme defekt]"
        return (
            f"@{self.address:#06x} "
            f"[{self.raw[0]:02x} {self.raw[1]:02x} {self.raw[2]:02x} {self.raw[3]:02x}] "
            f"{self.label}{flag}"
        )


@dataclass
class MouseStatus:
    node: str
    usb: str
    hid_name: str
    cid_mid: str | None
    model_name: str
    sensor: str | None
    dpi_family: str | None
    dpi_min: int
    dpi_max: int
    firmware: str | None
    battery: Battery | None
    system: SystemRow | None
    lod_mm: float | None
    dpi_stages: list[DpiStage]
    advanced: AdvancedRow | None
    sensor_row: SensorRow | None
    angle: tuple[float, bool] | None
    buttons: list[ButtonAssignment]
    max_polling_hz: int | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def clean(value: Any) -> Any:
            if isinstance(value, bytes):
                return value.hex(" ")
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(v) for v in value]
            return value

        return clean(asdict(self))


# ---------------------------------------------------------------------------
# Treiber
# ---------------------------------------------------------------------------


class AtkMouse:
    def __init__(
        self,
        transport: HidrawTransport,
        node: HidrawNode,
        inputs: Sequence[InputNode] = (),
        model: Model | None = None,
        verbose: bool = False,
    ) -> None:
        self.transport = transport
        self.node = node
        self.inputs = list(inputs)
        self.model = model
        self.verbose = verbose
        self.cid_mid: str | None = None
        self.notes: list[str] = []
        # Die Systemzeile ist je nach Modellfamilie 6 oder 10 Byte lang.
        # Es wird immer genau so viel zurueckgeschrieben, wie gelesen wurde
        # (Odin-/OpenMouse-Verhalten auf echter Hardware).
        self.system_len = p.SYSTEM_LEN
        self.force_system_len: int | None = None

    # -- Verbindung ---------------------------------------------------------

    @classmethod
    def connect(
        cls,
        node_path: str | None = None,
        vendor: int | None = None,
        product: int | None = None,
        timeout: float = 1.5,
        verbose: bool = False,
        identify: bool = True,
    ) -> "AtkMouse":
        vendors = (vendor or p.ATK_VID, p.ATK_ALT_VID)
        node: HidrawNode | None = None
        if node_path:
            candidates = list_hidraw_nodes(())
            node = next(
                (c for c in candidates if c.path == node_path or c.node == node_path),
                None,
            )
            if node is None:
                raise DeviceNotFound(f"HIDRAW-Knoten {node_path} nicht gefunden")
        else:
            node = find_config_node(vendors, product_id=product)
        transport = HidrawTransport(node, timeout=timeout, verbose=verbose)
        transport.open()
        try:
            inputs = [i for i in list_input_nodes((node.vendor,)) if node.physical and node.physical in i.physical]
            mouse = cls(transport, node, inputs, verbose=verbose)
            if identify:
                try:
                    mouse.identify()
                except (TimeoutError, p.ProtocolError) as exc:
                    mouse._note(f"CID/MID nicht lesbar: {exc}")
            mouse.system_len = mouse.system_row_length()
        except BaseException:
            transport.close()  # kein offener Handle bei fehlgeschlagenem Verbinden
            raise
        return mouse

    def close(self) -> None:
        self.transport.close()

    def _note(self, text: str) -> None:
        """Hinweis merken - jeden nur einmal (die GUI haelt das Objekt lange)."""
        if text not in self.notes:
            self.notes.append(text)

    def __enter__(self) -> "AtkMouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Rohkanal -----------------------------------------------------------

    def _exchange(
        self,
        frame: p.Frame,
        expect: int | tuple[int, ...] | None = None,
        ok_status: tuple[int, ...] = (0x00,),
        expect_address: int | None = None,
    ) -> p.Frame:
        try:
            response = self.transport.exchange(
                frame, expect_cmd=expect, ok_status=ok_status, expect_address=expect_address
            )
        except DeviceBusy:
            raise
        except TimeoutError as exc:
            # Der Dongle beantwortet 0x03 selbst, auch wenn die Maus schlaeft.
            # So laesst sich "Maus nicht verbunden" von anderen Ausfaellen trennen.
            if frame.cmd != p.Cmd.GET_ONLINE and self._mouse_offline():
                raise MouseOffline(
                    "Maus ist nicht verbunden (Schlafmodus oder ausgeschaltet). "
                    "Maus bewegen oder klicken und erneut versuchen."
                ) from exc
            raise
        if self.verbose:
            print(f"  -> {frame.build().hex(' ')}")
            print(f"  <- {response.hex()}")
        return response

    def command(
        self,
        cmd: int,
        address: int = 0,
        data: bytes = b"",
        declared: int | None = None,
        expect: int | tuple[int, ...] | None = None,
        ok_status: tuple[int, ...] = (0x00,),
    ) -> p.Frame:
        return self._exchange(
            p.Frame(cmd=cmd, address=address, data=data, declared=declared),
            expect=expect if expect is not None else cmd,
            ok_status=ok_status,
        )

    def _mouse_offline(self) -> bool:
        """True nur, wenn der Dongle ausdruecklich 'nicht verbunden' meldet."""
        try:
            response = self.transport.exchange(
                p.Frame(cmd=p.Cmd.GET_ONLINE), expect_cmd=int(p.Cmd.GET_ONLINE)
            )
        except (TimeoutError, p.ProtocolError, OSError):
            return False
        return bool(response.raw_data) and response.raw_data[0] == 0

    # -- Identifikation -----------------------------------------------------

    def identify(self) -> str | None:
        """Kommando 0x10: [CID, MID] - Modell, Sensor und DPI-Kodierung folgen daraus."""
        try:
            response = self.command(p.Cmd.GET_CID_MID, expect=p.Cmd.GET_CID_MID)
        except (TimeoutError, p.ProtocolError):
            return None
        data = response.raw_data
        if len(data) >= 2 and data[0] and data[1]:
            self.cid_mid = f"{data[0]},{data[1]}"
            self.model = find_model(self.node.vendor, self.node.product, self.cid_mid, self.node.name)
        return self.cid_mid

    def firmware_version(self) -> str | None:
        try:
            response = self.command(p.Cmd.GET_MOUSE_VERSION, expect=p.Cmd.GET_MOUSE_VERSION)
        except (TimeoutError, p.ProtocolError):
            return None
        data = response.raw_data
        if len(data) < 2:
            return None
        return f"{_bcd(data[0])}.{_bcd(data[1])}"

    def receiver_version(self) -> str | None:
        try:
            response = self.command(p.Cmd.GET_RECEIVER_VERSION, expect=p.Cmd.GET_RECEIVER_VERSION)
        except (TimeoutError, p.ProtocolError) as exc:
            self._note(f"Empfaenger-Version nicht lesbar: {exc}")
            return None
        value = (response.raw_data[0] << 8) | response.raw_data[1] if len(response.raw_data) >= 2 else 0
        return f"0x{value:04x}" if value else None

    def online_status(self) -> dict[str, Any] | None:
        """Kommando 0x03: Online-Zustand + 3 Byte RF-Kennung des Dongles."""
        try:
            response = self.command(p.Cmd.GET_ONLINE, expect=p.Cmd.GET_ONLINE)
        except (TimeoutError, p.ProtocolError):
            return None
        data = response.raw_data
        rf = "".join(f"{b:02X}" for b in (data[3:0:-1] if len(data) >= 4 else data[:3]))
        return {"online": bool(data and data[0] == 1), "status": data[0] if data else None, "rf_id": rf}

    def pairing_status(self) -> dict[str, Any] | None:
        """Kommando 0x06: Pairing-Status + Restsekunden (Status 2 heisst nicht 'erfolgreich')."""
        try:
            response = self.command(
                p.Cmd.GET_PAIRING, expect=p.Cmd.GET_PAIRING, ok_status=(0x00, 0x01, 0x02)
            )
        except (TimeoutError, p.ProtocolError):
            return None
        data = response.raw_data
        seconds = (data[1] << 8) | data[2] if len(data) >= 3 else None
        return {"status": response.status, "seconds_remaining": seconds}

    # -- EEPROM -------------------------------------------------------------

    def read_eeprom(self, address: int, length: int) -> bytes:
        """GetEEPROM (0x08). Die angeforderte Laenge muss deklariert werden,
        sonst antwortet die Firmware mit data_len = 0."""
        out = bytearray()
        offset = 0
        while offset < length:
            chunk = min(p.MAX_DATA, length - offset)
            response = self._exchange(
                p.Frame(
                    cmd=p.Cmd.GET_EEPROM,
                    address=address + offset,
                    declared=chunk,
                ),
                expect=(int(p.Cmd.GET_EEPROM), int(p.Cmd.SET_EEPROM)),
                # Die Firmware wiederholt die Adresse in der Antwort; fremde
                # oder verspaetete Antworten werden so aussortiert.
                expect_address=address + offset,
            )
            got = response.raw_data[:chunk]
            if len(got) < chunk:
                raise p.ProtocolError(
                    f"EEPROM {address + offset:#06x}: {chunk} Byte angefordert, "
                    f"{len(got)} erhalten"
                )
            out += got
            offset += chunk
        return bytes(out)

    def write_eeprom(
        self, address: int, data: bytes, verify: bool = True, chunk: int = p.MAX_DATA
    ) -> None:
        """SetEEPROM (0x07). Schreibvorgaenge bleiben innerhalb der Record-Grenzen."""
        with self.transport.locked():
            for offset in range(0, len(data), chunk):
                part = data[offset : offset + chunk]
                self._exchange(
                    p.Frame(cmd=p.Cmd.SET_EEPROM, address=address + offset, data=part),
                    expect=(int(p.Cmd.SET_EEPROM), int(p.Cmd.GET_EEPROM)),
                )
            if verify:
                back = self.read_eeprom(address, len(data))
                if back != data:
                    raise WriteVerificationError(
                        f"Schreiben nach {address:#06x} nicht verifiziert:\n"
                        f"  soll: {data.hex(' ')}\n  ist:  {back.hex(' ')}"
                    )

    def _read_row(self, address: int, length: int) -> bytearray:
        return bytearray(self.read_eeprom(address, length))

    def _write_pairs(self, address: int, row: bytearray, updates: dict[int, int]) -> None:
        """Read-Modify-Write: nur genannte Offsets als [wert, 0x55-wert] setzen."""
        for offset, value in updates.items():
            row[offset], row[offset + 1] = p.pair(value)
        self.write_eeprom(address, bytes(row))

    def _update_pairs(self, address: int, length: int, updates: dict[int, int]) -> None:
        """Lesen-Aendern-Schreiben unter einer Sperre, damit kein anderer
        Prozess (Tray, GUI, CLI) dazwischen dieselbe Zeile veraendert."""
        with self.transport.locked():
            row = self._read_row(address, length)
            self._write_pairs(address, row, updates)

    # -- Systemzeile (0x0000) ----------------------------------------------

    def is_r1_family(self) -> bool:
        """VXE-R1-artige Modelle verwenden eine 10-Byte-Systemzeile."""
        name = (self.model.name if self.model else "").upper()
        return "R1" in name or self.node.vendor == p.ATK_ALT_VID

    def system_row_length(self) -> int:
        if self.force_system_len:
            return self.force_system_len
        return p.SYSTEM_ROW_LEN if self.is_r1_family() else p.SYSTEM_LEN

    def read_system(self) -> SystemRow:
        length = self.system_row_length()
        row = self._read_row(p.Eeprom.SYSTEM, length)
        polling = p.polling_from_code(row[0])
        declared_stages = row[2]
        stage_count = declared_stages if 1 <= declared_stages <= p.DPI_STAGE_COUNT_MAX else None
        active = row[4]
        active_stage = active + 1 if 0 <= active < (stage_count or 8) else None
        extra = tuple(row[i] for i in range(6, len(row), 2))
        return SystemRow(
            polling_hz=polling,
            stage_count=stage_count,
            active_stage=active_stage,
            raw=bytes(row),
            extra_pairs=extra,
        )

    def set_polling(self, hz: int) -> SystemRow:
        max_hz = self.max_polling_hz()
        if max_hz and hz > max_hz:
            raise ValueError(
                f"{hz} Hz ist mit diesem Empfaenger ({self.node.product_string}, "
                f"max. {max_hz} Hz) nicht moeglich"
            )
        code = p.polling_to_code(hz)
        if code is None:
            raise ValueError(f"Polling-Rate {hz} Hz wird nicht unterstuetzt")
        self._update_pairs(p.Eeprom.SYSTEM, self.system_row_length(), {0: code})
        return self.read_system()

    def set_stage_count(self, count: int) -> SystemRow:
        if not 1 <= count <= p.DPI_STAGE_COUNT_MAX:
            raise ValueError(f"DPI-Stufen {count} ausserhalb 1..{p.DPI_STAGE_COUNT_MAX}")
        self._update_pairs(p.Eeprom.SYSTEM, self.system_row_length(), {2: count})
        return self.read_system()

    def select_stage(self, stage: int) -> SystemRow:
        """Aktive DPI-Stufe (0-basiert im EEPROM, 1-basiert in der CLI)."""
        with self.transport.locked():
            count = self.read_system().stage_count or p.DPI_STAGE_COUNT_MAX
            if not 1 <= stage <= count:
                raise ValueError(f"DPI-Stufe {stage} ausserhalb 1..{count}")
            self._update_pairs(p.Eeprom.SYSTEM, self.system_row_length(), {4: stage - 1})
            return self.read_system()

    # -- Lift-off (0x000A) -------------------------------------------------

    def lod_table(self) -> p.LodTable:
        """Die Lift-off-Skala ist modellabhaengig (verifiziert: Ultra kontinuierlich,
        PAW3950 Low/Medium/High, R1-artig 1/2 mm)."""
        if self.model is not None:
            return self.model.lod_table
        return p.lod_table(self.sensor_name())

    def read_lod(self) -> tuple[int, float | None]:
        row = self._read_row(p.Eeprom.LOD, p.LOD_LEN)
        return row[0], self.lod_table().mm(row[0])

    def set_lod(self, value: str | float | int) -> tuple[int, float | None]:
        table = self.lod_table()
        text = str(value).strip().lower()
        if text.startswith("code:"):
            code = int(text[5:])
            if code not in table.codes:
                raise ValueError(f"Lift-off-Code {code} unbekannt ({sorted(table.codes)})")
        else:
            code = table.code(float(text))
            if code is None:
                raise ValueError(
                    f"Lift-off {value!r} nicht gueltig. Erlaubt: "
                    + ", ".join(f"{v} mm" for v in sorted(table.codes.values()))
                    + " (oder 'code:N')"
                )
        self._update_pairs(p.Eeprom.LOD, p.LOD_LEN, {0: code})
        return self.read_lod()

    # -- DPI (0x000C) -------------------------------------------------------

    def dpi_limits(self) -> tuple[int, int, str]:
        family = self.dpi_family()
        model = self.model
        if model and model.dpi_min and model.dpi_max:
            return model.dpi_min, model.dpi_max, family
        return 50, 30000, family

    def read_dpi_stages(self, count: int | None = None) -> list[DpiStage]:
        family = self.dpi_family()
        if count is None:
            system = self.read_system()
            count = system.stage_count or 6
        count = max(1, min(count, p.DPI_STAGE_COUNT_MAX))
        stages: list[DpiStage] = []
        colors = self.read_dpi_colors(count)
        for index in range(count):
            address = p.Eeprom.DPI_BASE + index * p.DPI_RECORD_LEN
            raw = self.read_eeprom(address, p.DPI_RECORD_LEN)
            x, y = p.dpi_decode(raw, family)
            stages.append(
                DpiStage(
                    index=index + 1,
                    address=address,
                    x=x,
                    y=y,
                    raw=raw,
                    color=colors[index] if index < len(colors) else None,
                )
            )
        return stages

    def read_dpi_colors(self, count: int = 8) -> list[tuple[int, int, int] | None]:
        colors: list[tuple[int, int, int] | None] = []
        for index in range(count):
            address = p.Eeprom.DPI_COLOR_BASE + index * p.DPI_COLOR_LEN
            try:
                raw = self.read_eeprom(address, p.DPI_COLOR_LEN)
            except (p.ProtocolError, TimeoutError):
                colors.append(None)
                continue
            if sum(raw) & 0xFF != p.CHECKSUM_TARGET:
                colors.append(None)
                continue
            colors.append((raw[0], raw[1], raw[2]))
        return colors

    def set_dpi_stage(self, stage: int, x: int, y: int | None = None) -> DpiStage:
        index = stage - 1
        if index < 0 or index >= p.DPI_STAGE_COUNT_MAX:
            raise ValueError(f"Stufe {stage} ausserhalb 1..{p.DPI_STAGE_COUNT_MAX}")
        family = self.dpi_family()
        lo, hi, _ = self.dpi_limits()
        y = x if y is None else y
        for value in (x, y):
            if not lo <= value <= hi:
                raise ValueError(f"DPI {value} ausserhalb {lo}..{hi} ({family})")
        record = p.dpi_encode(x, y, family)
        address = p.Eeprom.DPI_BASE + index * p.DPI_RECORD_LEN
        self.write_eeprom(address, record)
        raw = self.read_eeprom(address, p.DPI_RECORD_LEN)
        got_x, got_y = p.dpi_decode(raw, family)
        if (got_x, got_y) != (x, y):
            raise WriteVerificationError(
                f"DPI-Stufe {stage}: geschrieben {x}/{y}, zurueckgelesen {got_x}/{got_y} "
                f"(raw {raw.hex(' ')}, Kodierung {family})"
            )
        colors = self.read_dpi_colors(index + 1)
        return DpiStage(stage, address, got_x, got_y, raw, colors[index] if index < len(colors) else None)

    def set_dpi_color(self, stage: int, rgb: tuple[int, int, int]) -> None:
        index = stage - 1
        if index < 0 or index >= p.DPI_STAGE_COUNT_MAX:
            raise ValueError(f"Stufe {stage} ausserhalb 1..{p.DPI_STAGE_COUNT_MAX}")
        record = bytes(rgb) + bytes([p.CHECKSUM_TARGET - sum(rgb) & 0xFF])
        self.write_eeprom(p.Eeprom.DPI_COLOR_BASE + index * p.DPI_COLOR_LEN, record)

    # -- Erweiterte Einstellungen (0x00A9) ----------------------------------

    def read_advanced(self) -> AdvancedRow:
        row = self._read_row(p.Eeprom.ADVANCED, p.ADVANCED_LEN)
        debounce = p.unpair(row[0], row[1])
        # Schlafzeit wird in 10-Sekunden-Schritten gespeichert (OpenMouse: SLEEP_STEP_SECONDS)
        sleep = p.unpair(row[4], row[5]) * 10
        return AdvancedRow(
            debounce_ms=debounce if debounce in p.DEBOUNCE_MS else None,
            motion_sync=bool(p.unpair(row[2], row[3])) if p.unpair(row[2], row[3]) in (0, 1) else None,
            sleep_seconds=sleep if sleep in p.SLEEP_SECONDS else None,
            straight_line=bool(p.unpair(row[6], row[7])) if p.unpair(row[6], row[7]) in (0, 1) else None,
            ripple=bool(p.unpair(row[8], row[9])) if p.unpair(row[8], row[9]) in (0, 1) else None,
            raw=bytes(row),
        )

    def set_advanced(
        self,
        debounce_ms: int | None = None,
        motion_sync: bool | None = None,
        sleep_seconds: int | None = None,
        straight_line: bool | None = None,
        ripple: bool | None = None,
    ) -> AdvancedRow:
        updates: dict[int, int] = {}
        if debounce_ms is not None:
            if debounce_ms not in p.DEBOUNCE_MS:
                raise ValueError(f"Debounce {debounce_ms} nicht in {p.DEBOUNCE_MS}")
            updates[0] = debounce_ms
        if motion_sync is not None:
            updates[2] = int(motion_sync)
        if sleep_seconds is not None:
            if sleep_seconds not in p.SLEEP_SECONDS:
                raise ValueError(f"Schlafzeit {sleep_seconds} nicht in {p.SLEEP_SECONDS}")
            updates[4] = sleep_seconds // 10
        if straight_line is not None:
            updates[6] = int(straight_line)
        if ripple is not None:
            updates[8] = int(ripple)
        if not updates:
            raise ValueError("keine Aenderung angegeben")
        self._update_pairs(p.Eeprom.ADVANCED, p.ADVANCED_LEN, updates)
        return self.read_advanced()

    # -- Sensor (0x00B5) ----------------------------------------------------

    def read_sensor_row(self) -> SensorRow:
        row = self._read_row(p.Eeprom.SENSOR, p.SENSOR_LEN)
        mode = p.unpair(row[4], row[5])
        return SensorRow(
            sleep_enabled=bool(p.unpair(row[0], row[1])) if p.unpair(row[0], row[1]) in (0, 1) else None,
            sleep_time=p.unpair(row[2], row[3]),
            performance_mode={0: "basic", 1: "competitive"}.get(mode),
            raw=bytes(row),
        )

    def set_sensor_mode(self, mode: str) -> SensorRow:
        """'basic' = Basic Mode, 'competitive' = ATK Shard Competitive Firmware."""
        mapping = {"basic": 0, "base": 0, "competitive": 1, "shard": 1, "leistung": 1}
        if mode not in mapping:
            raise ValueError(f"Sensor-Modus {mode!r} nicht in {sorted(mapping)}")
        self._update_pairs(p.Eeprom.SENSOR, p.SENSOR_LEN, {4: mapping[mode]})
        return self.read_sensor_row()

    def read_angle(self) -> tuple[float, bool] | None:
        try:
            row = self._read_row(p.Eeprom.SENSOR_ANGLE, 4)
        except (p.ProtocolError, TimeoutError):
            return None
        angle = p.unpair(row[0], row[1])
        if angle > 127:
            angle -= 256
        return angle, bool(p.unpair(row[2], row[3]))

    # -- Tasten (0x0060) ----------------------------------------------------

    def read_buttons(self, count: int = 8) -> list[ButtonAssignment]:
        out: list[ButtonAssignment] = []
        for index in range(count):
            address = p.Eeprom.KEYS + index * p.KEY_RECORD_LEN
            try:
                raw = self.read_eeprom(address, p.KEY_RECORD_LEN)
            except (p.ProtocolError, TimeoutError):
                break
            if raw == b"\x00" * 4:
                # unprogrammierter Slot: Ende der Tastenmatrix
                break
            out.append(self._button_from_raw(index, address, raw))
        return out

    @staticmethod
    def _button_from_raw(index: int, address: int, raw: bytes) -> ButtonAssignment:
        if raw == b"\xff" * 4:
            return ButtonAssignment(index, address, 0xFF, 0xFF, 0xFF, False, "leer (0xff)", raw)
        return ButtonAssignment(
            index=index,
            address=address,
            cls=raw[0],
            value1=raw[1],
            value2=raw[2],
            checksum_ok=sum(raw) & 0xFF == p.CHECKSUM_TARGET,
            label=p.describe_button_action(raw[0], raw[1], raw[2]),
            raw=raw,
        )

    def set_button(
        self,
        slot: int,
        cls: int,
        value1: int,
        value2: int = 0,
    ) -> ButtonAssignment:
        """Tasten-Slot beschreiben: [klasse, wert1, wert2, Pruefsumme].

        Klassen (ATK-HUB-Enum, ueber OpenMouse verifiziert):
          0 deaktiviert, 1 Maustaste (Bitmaske), 2 DPI-Steuerung,
          3 horizontal scrollen, 4 Fire-Key, 5 Tastenkuerzel (Slot),
          6 Makro (Slot), 7 Polling-Rate, 8 Beleuchtung, 9 Profil,
          10 DPI-Sperre, 11 Mausrad
        """
        index = slot - 1
        if index < 0 or index >= p.KEY_COUNT:
            raise ValueError(f"Slot {slot} ausserhalb 1..{p.KEY_COUNT}")
        record = bytes([cls & 0xFF, value1 & 0xFF, value2 & 0xFF])
        record += bytes([(p.CHECKSUM_TARGET - sum(record)) & 0xFF])
        address = p.Eeprom.KEYS + index * p.KEY_RECORD_LEN
        # write_eeprom liest den Record zurueck und wirft WriteVerificationError
        # bei Abweichung. Kein read_buttons() hier: das bricht bei einem leeren
        # Slot davor ab und lieferte dann keinen Eintrag fuer diesen Slot.
        self.write_eeprom(address, record)
        return self._button_from_raw(index, address, record)

    def read_shortcut(self, slot: int) -> bytes:
        """32-Byte-Shortcut-Slot (0x0100 + slot*32) - roh, Format herstellerabhaengig."""
        index = slot - 1
        if index < 0 or index >= p.SHORTCUT_COUNT:
            raise ValueError(f"Shortcut-Slot {slot} ausserhalb 1..{p.SHORTCUT_COUNT}")
        return self.read_eeprom(p.Eeprom.KEY_SHORTCUTS + index * p.SHORTCUT_SLOT_LEN, p.SHORTCUT_SLOT_LEN)

    # -- Empfaenger-Kommandos ----------------------------------------------

    def get_far_distance(self) -> int | None:
        try:
            response = self.command(p.Cmd.GET_FAR_DISTANCE, expect=p.Cmd.GET_FAR_DISTANCE)
        except (TimeoutError, p.ProtocolError) as exc:
            self._note(f"Reichweiten-Modus nicht lesbar: {exc}")
            return None
        return p.unpair(response.raw_data[0], response.raw_data[1]) if len(response.raw_data) >= 2 else None

    def get_dongle_light(self) -> int | None:
        try:
            response = self.command(p.Cmd.GET_DONGLE_LIGHT, expect=p.Cmd.GET_DONGLE_LIGHT)
        except (TimeoutError, p.ProtocolError) as exc:
            self._note(f"Dongle-LED nicht lesbar: {exc}")
            return None
        return p.unpair(response.raw_data[0], response.raw_data[1]) if len(response.raw_data) >= 2 else None

    def active_profile(self) -> int | None:
        try:
            response = self.command(p.Cmd.GET_CURRENT_CONFIG, expect=p.Cmd.GET_CURRENT_CONFIG)
        except (TimeoutError, p.ProtocolError) as exc:
            self._note(f"Aktives Profil nicht lesbar: {exc}")
            return None
        value = p.unpair(response.raw_data[0], response.raw_data[1]) if len(response.raw_data) >= 2 else None
        return value + 1 if value is not None and value < 8 else value

    def set_active_profile(self, profile: int) -> None:
        index = profile - 1
        if index < 0 or index > 3:
            raise ValueError("ATK HUB verwaltet 4 Profile (1..4)")
        self.command(p.Cmd.SET_CURRENT_CONFIG, data=bytes([index]))

    # -- Hilfsfunktionen ----------------------------------------------------

    def sensor_name(self) -> str | None:
        return self.model.sensor if self.model else None

    def dpi_family(self) -> str:
        if self.model and self.model.dpi_family in p.DPI_FAMILIES:
            return self.model.dpi_family
        self._note(
            f"DPI-Kodierung 'step50' vermutet (Sensor {self.sensor_name() or 'unbekannt'}); "
            "Modell nicht eindeutig identifiziert"
        )
        return "step50"

    def max_polling_hz(self) -> int | None:
        limit = RECEIVER_MAX_POLLING.get(self.node.product)
        if limit:
            return limit
        return 8000

    def polling_options(self) -> tuple[int, ...]:
        return polling_options(self.node.product)

    # -- Sammelstatus -------------------------------------------------------

    def status(self, include_buttons: bool = True) -> MouseStatus:
        system = self.read_system()
        stages = self.read_dpi_stages()
        advanced = self.read_advanced()
        sensor_row = self.read_sensor_row()
        _, lod_mm = self.read_lod()
        battery: Battery | None = None
        try:
            response = self.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
            parsed = p.parse_battery(response.data, response.declared)
            battery = Battery(
                percent=parsed.percent,
                charging=parsed.charging,
                millivolts=parsed.voltage_mv,
            )
        except (TimeoutError, p.ProtocolError) as exc:
            self._note(f"Batterie nicht lesbar: {exc}")
        angle: tuple[int, bool] | None = None
        if self.model is None or self.model.supports("noSensorAngle"):
            try:
                angle = self.read_angle()
            except (TimeoutError, p.ProtocolError) as exc:
                self._note(f"Winkel nicht lesbar: {exc}")
        buttons = self.read_buttons() if include_buttons else []
        lo, hi, family = self.dpi_limits()
        return MouseStatus(
            node=self.node.path,
            usb=self.node.device_string,
            hid_name=self.node.name,
            cid_mid=self.cid_mid,
            model_name=self.model.name if self.model else "unbekannt",
            sensor=self.sensor_name(),
            dpi_family=family,
            dpi_min=lo,
            dpi_max=hi,
            firmware=self.firmware_version(),
            battery=battery,
            system=system,
            lod_mm=lod_mm,
            dpi_stages=stages,
            advanced=advanced,
            sensor_row=sensor_row,
            angle=angle,
            buttons=buttons,
            max_polling_hz=self.max_polling_hz(),
            notes=list(self.notes),
        )

    # -- Wartung ------------------------------------------------------------

    def factory_reset(self) -> None:
        """Kommando 0x09: Werkseinstellung. Danach neu paaren (Kommando 0x05)."""
        self.command(p.Cmd.FACTORY_RESET)

    def start_pairing(self) -> p.Frame:
        """Kommando 0x05: Pairing-Modus starten - dann Linksklick+Rad+Rechtsklick halten."""
        return self.command(p.Cmd.START_PAIRING, ok_status=(0x00, 0x01, 0x02))

    def cancel_pairing(self) -> p.Frame:
        return self.command(p.Cmd.CANCEL_PAIRING, ok_status=(0x00, 0x01, 0x02))

    # -- Rohzugriff ( fuer Entwickler) --------------------------------------

    def raw_read(self, address: int, length: int) -> bytes:
        return self.read_eeprom(address, length)

    def raw_write(self, address: int, data: bytes, verify: bool = True) -> None:
        self.write_eeprom(address, data, verify=verify)

    def dump(self, start: int = 0x0000, end: int = 0x00BF) -> bytes:
        return self.read_eeprom(start, end - start + 1)

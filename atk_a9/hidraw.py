"""HID-Transport ueber /dev/hidraw (nur Python-Standardbibliothek).

Enthaelt:

* minimalen Report-Descriptor-Parser (findet das Konfigurations-Interface,
  d. h. das Interface mit Output-Report 0x08 / 16 Byte)
* Geraet-Suche ueber /sys/class/hidraw und /sys/class/input
* Frame-Austausch (Schreiben + gefiltertes Lesen der Antwort)
* Feature-Report-Zugriff per ioctl (HIDIOCGFEATURE / HIDIOCSFEATURE)
"""

from __future__ import annotations

import array
import errno
import fcntl
import glob
import os
import re
import select
import struct
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .protocol import FRAME_LEN, REPORT_ID, Cmd, Frame, ProtocolError

# ---------------------------------------------------------------------------
# ioctls
# ---------------------------------------------------------------------------

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2
_IOC_NRMASK = (1 << _IOC_NRBITS) - 1
_IOC_SIZEMASK = (1 << _IOC_SIZEBITS) - 1


def _ioc(direction: int, typ: int, nr: int, size: int) -> int:
    return (
        (direction << 30)
        | (size << 16)
        | (typ << 8)
        | nr
    )


IOC_READ = 1
IOC_WRITE = 2

HIDIOCGFEATURE = lambda size: _ioc(IOC_READ | IOC_WRITE, ord("H"), 0x2B, size)  # noqa: E731
HIDIOCSFEATURE = lambda size: _ioc(IOC_WRITE, ord("H"), 0x2C, size)  # noqa: E731
HIDIOCGREPORT = lambda size: _ioc(IOC_READ | IOC_WRITE, ord("H"), 0x27, size)  # noqa: E731
HIDIOCSREPORT = lambda size: _ioc(IOC_WRITE, ord("H"), 0x28, size)  # noqa: E731

EVIOCGBIT_BASE = 0x20  # EVIOCGBIT(ev, len) = _IOC(_IOC_READ, 'E', 0x20 + ev, len)
EVIOCGRAB = _ioc(IOC_WRITE, ord("E"), 0x64, 4)
EVIOCGNAME = lambda size: _ioc(IOC_READ, ord("E"), 0x06, size)  # noqa: E731


# ---------------------------------------------------------------------------
# Report-Descriptor-Parser
# ---------------------------------------------------------------------------

ITEM_TYPE_MASK = 0xFC
ITEM_TAG_MASK = 0x03

# HID-Short-Item-Tags (prefix & 0xFC)
TAG_INPUT = 0x80          # Main
TAG_OUTPUT = 0x90         # Main
TAG_FEATURE = 0xB0        # Main
TAG_COLLECTION = 0xA0     # Main
TAG_END_COLLECTION = 0xC0 # Main
TAG_USAGE_PAGE = 0x04     # Global
TAG_REPORT_SIZE = 0x74    # Global
TAG_REPORT_ID = 0x84      # Global
TAG_REPORT_COUNT = 0x94   # Global
TAG_USAGE = 0x08          # Local
TAG_USAGE_MIN = 0x18      # Local
TAG_USAGE_MAX = 0x28      # Local
COLLECTION_APPLICATION = 0x01
COLLECTION_PHYSICAL = 0x00
COLLECTION_REPORT = 0x02
COLLECTION_NAMED = 0x03


@dataclass
class ReportSizes:
    input: int = 0
    output: int = 0
    feature: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "feature": self.feature,
        }


@dataclass
class Collection:
    usage_page: int
    usage: int
    report_ids: tuple[int, ...] = ()


@dataclass
class ReportInfo:
    """Zusammenfassung eines Report-Descriptors."""

    reports: dict[int, ReportSizes] = field(default_factory=dict)
    collections: list[Collection] = field(default_factory=list)

    def has_output_report(self, report_id: int, size: int | None = None) -> bool:
        sizes = self.reports.get(report_id)
        if sizes is None or sizes.output == 0:
            return False
        return size is None or sizes.output == size

    def has_feature_report(self, report_id: int) -> bool:
        sizes = self.reports.get(report_id)
        return sizes is not None and sizes.feature > 0

    def feature_size(self, report_id: int) -> int:
        sizes = self.reports.get(report_id)
        return sizes.feature if sizes else 0

    def vendor_collections(self) -> list[Collection]:
        return [c for c in self.collections if c.usage_page >= 0xFF00]

    def describe(self) -> str:
        lines = []
        for rid in sorted(self.reports):
            sizes = self.reports[rid]
            parts = [f"{name}={size} Byte" for name, size in sizes.as_dict().items() if size]
            lines.append(f"  report 0x{rid:02x}: " + ", ".join(parts))
        for c in self.collections:
            lines.append(f"  collection usage_page=0x{c.usage_page:04x} usage=0x{c.usage:04x}")
        return "\n".join(lines)


def parse_report_descriptor(data: bytes) -> ReportInfo:
    """Sehr einfacher HID-Report-Descriptor-Parser (Short Items, keine Long Items)."""
    info = ReportInfo()
    i = 0
    usage_page = 0
    usage_stack: list[tuple[int, int]] = []
    collection_usages: list[tuple[int, int]] = []
    collection_reports: list[set[int]] = []
    report_id = 0
    report_count = 0
    report_size = 0

    def add_report(kind: str, rid: int, bits: int) -> None:
        sizes = info.reports.setdefault(rid, ReportSizes())
        value = (bits + 7) // 8
        current = getattr(sizes, kind)
        setattr(sizes, kind, max(current, value))
        for collected in collection_reports:
            collected.add(rid)

    while i < len(data):
        prefix = data[i]
        if prefix == 0xFE:  # Long item: FE <size> <payload...>
            if i + 1 >= len(data):
                break
            long_size = data[i + 1]
            i += 2 + long_size
            continue
        raw_size = prefix & ITEM_TAG_MASK
        size = 4 if raw_size == 3 else raw_size
        tag = prefix & ITEM_TYPE_MASK
        if i + 1 + size > len(data):
            break
        payload = data[i + 1 : i + 1 + size]
        value = int.from_bytes(payload, "little") if payload else 0
        i += 1 + size

        if tag == TAG_USAGE_PAGE:
            usage_page = value
        elif tag == TAG_REPORT_ID:
            report_id = value
        elif tag == TAG_REPORT_COUNT:
            report_count = value
        elif tag == TAG_REPORT_SIZE:
            report_size = value
        elif tag == TAG_USAGE:
            usage_stack.append((usage_page, value))
        elif tag in (TAG_USAGE_MIN, TAG_USAGE_MAX):
            pass
        elif tag == TAG_COLLECTION:
            if value == COLLECTION_APPLICATION:
                page, usage = usage_stack[-1] if usage_stack else (usage_page, 0)
                collection_usages.append((page, usage))
                collection_reports.append(set())
            else:
                collection_usages.append(None)  # type: ignore[arg-type]
                collection_reports.append(set())
        elif tag == TAG_END_COLLECTION:
            if collection_usages:
                page_usage = collection_usages.pop()
                reports = collection_reports.pop()
                if page_usage is not None:
                    info.collections.append(Collection(page_usage[0], page_usage[1], tuple(sorted(reports))))
        elif tag in (TAG_INPUT, TAG_OUTPUT, TAG_FEATURE):
            kind = {TAG_INPUT: "input", TAG_OUTPUT: "output", TAG_FEATURE: "feature"}[tag]
            add_report(kind, report_id, report_count * report_size)
            usage_stack.clear()
    return info


# ---------------------------------------------------------------------------
# Geraet-Suche
# ---------------------------------------------------------------------------

HID_ID_RE = re.compile(r"HID_ID=0003:(?P<vendor>[0-9A-Fa-f]{8}):(?P<product>[0-9A-Fa-f]{8})")


@dataclass
class HidrawNode:
    path: str
    name: str
    vendor: int
    product: int
    interface: int | None
    reports: ReportInfo
    physical: str = ""

    @property
    def node(self) -> str:
        return os.path.basename(self.path)

    @property
    def vidpid(self) -> str:
        return f"{self.vendor:04x}:{self.product:04x}"

    @property
    def device_string(self) -> str:
        return f"{self.vendor:04x}:{self.product:04x}"

    @property
    def product_string(self) -> str:
        from .catalog import RECEIVERS

        return RECEIVERS.get(self.product, self.name or f"{self.product:04x}")

    @property
    def is_config_interface(self) -> bool:
        return self.reports.has_output_report(REPORT_ID, 16)

    def describe(self) -> str:
        iface = f"if{self.interface:02d}" if self.interface is not None else "if??"
        return f"{self.path} ({iface}, {self.vidpid}) {self.name}"


@dataclass
class InputNode:
    path: str  # /dev/input/eventN
    name: str
    interface: int | None
    is_mouse_interface: bool
    vendor: int | None = None
    product: int | None = None
    physical: str = ""
    ev_bits: frozenset[int] = frozenset()
    key_bits: frozenset[int] = frozenset()
    rel_bits: frozenset[int] = frozenset()
    abs_bits: frozenset[int] = frozenset()

    @property
    def node(self) -> str:
        return os.path.basename(self.path)

    @property
    def reports_movement(self) -> bool:
        return 2 in self.ev_bits  # EV_REL

    @property
    def mouse_buttons(self) -> list[int]:
        return sorted(code for code in self.key_bits if 0x110 <= code <= 0x1FF)

    def describe(self) -> str:
        iface = f"if{self.interface:02d}" if self.interface is not None else "if??"
        bits = "EV=" + ",".join(sorted(EV_NAMES.get(bit, str(bit)) for bit in self.ev_bits))
        return f"{self.path} ({iface}) {self.name} [{bits}]"


EV_NAMES: dict[int, str] = {
    0: "SYN",
    1: "KEY",
    2: "REL",
    3: "ABS",
    4: "MSC",
    5: "SW",
    6: "LED",
    7: "SND",
    8: "REP",
    9: "FF",
}

BTN_NAMES: dict[int, str] = {
    0x110: "BTN_LEFT",
    0x111: "BTN_RIGHT",
    0x112: "BTN_MIDDLE",
    0x113: "BTN_SIDE",
    0x114: "BTN_EXTRA",
    0x115: "BTN_FORWARD",
    0x116: "BTN_BACK",
    0x117: "BTN_TASK",
}

REL_NAMES: dict[int, str] = {
    0: "REL_X",
    1: "REL_Y",
    6: "REL_WHEEL",
    8: "REL_HWHEEL",
    11: "REL_WHEEL_HI_RES",
    12: "REL_HWHEEL_HI_RES",
}


def parse_uevent_bitmask(text: str) -> set[int]:
    """Kernel-Format: Woerter von hoch nach niedrig, ohne fuehrende Nullen.

    Das erste gedruckte Wort ist das hochwertigste, das letzte das niedrigste
    (Wort 0 = Bits 0..63). 1f0000 0 0 0 0 -> Bits 272..276 = BTN_LEFT..BTN_EXTRA.
    """
    words = [w for w in text.split() if w]
    if not words:
        return set()
    value = int("".join(word.rjust(16, "0") for word in words), 16)
    return {bit for bit in range(value.bit_length()) if (value >> bit) & 1}

def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


def _hid_interface(sysdir: str) -> int | None:
    match = re.search(r":1\.(\d+)/", sysdir)
    return int(match.group(1)) if match else None


def list_hidraw_nodes(vendors: Iterable[int] = (0x373B, 0x3554)) -> list[HidrawNode]:
    nodes: list[HidrawNode] = []
    for sysdir in sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0)):
        uevent = _read_bytes(os.path.join(sysdir, "device", "uevent")).decode("utf-8", "replace")
        match = HID_ID_RE.search(uevent)
        if not match:
            continue
        vendor = int(match.group("vendor"), 16)
        product = int(match.group("product"), 16)
        if vendors and vendor not in vendors:
            continue
        name_match = re.search(r"HID_NAME=(.*)", uevent)
        name = name_match.group(1).strip() if name_match else ""
        phys_match = re.search(r"HID_PHYS=(.*)", uevent)
        physical = phys_match.group(1).strip() if phys_match else ""
        descriptor = _read_bytes(os.path.join(sysdir, "device", "report_descriptor"))
        reports = parse_report_descriptor(descriptor)
        nodes.append(
            HidrawNode(
                path="/dev/" + os.path.basename(sysdir),
                name=name,
                vendor=vendor,
                product=product,
                interface=_hid_interface(os.path.realpath(sysdir)),
                reports=reports,
                physical=physical,
            )
        )
    return nodes


def find_config_node(
    vendors: Iterable[int] = (0x373B, 0x3554),
    product_id: int | None = None,
) -> HidrawNode:
    """Das Interface mit Output-Report 0x08 (16 Byte) ist die Konfigurationsschnittstelle."""
    candidates = [node for node in list_hidraw_nodes(vendors) if node.is_config_interface]
    if product_id:
        preferred = [node for node in candidates if node.product == product_id]
        if preferred:
            candidates = preferred
    if not candidates:
        known = ", ".join(node.describe() for node in list_hidraw_nodes(vendors)) or "kein ATK/Compx/VXE-Geraet gefunden"
        raise DeviceNotFound(
            "Kein Konfigurations-Interface (Output-Report 0x08, 16 Byte) gefunden. Gefunden: "
            + known
        )
    if len(candidates) > 1:
        candidates.sort(key=lambda node: (node.interface is None, node.interface or 0))
    return candidates[0]


def list_input_nodes(vendors: Iterable[int] = (0x373B, 0x3554)) -> list[InputNode]:
    nodes: list[InputNode] = []
    for sysdir in sorted(glob.glob("/sys/class/input/event*"), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0)):
        real = os.path.realpath(sysdir)
        uevent = _read_bytes(os.path.join(real, "uevent")).decode("utf-8", "replace")
        fields = dict(
            line.split("=", 1) for line in uevent.splitlines() if "=" in line
        )
        match = re.match(r"\d+/([0-9a-fA-F]{4})/([0-9a-fA-F]{4})", fields.get("PRODUCT", ""))
        if not match:
            continue
        vendor = int(match.group(1), 16)
        if vendors and vendor not in vendors:
            continue
        nodes.append(
            InputNode(
                path="/dev/input/" + os.path.basename(sysdir),
                name=fields.get("NAME", "").strip('"'),
                interface=_hid_interface(real),
                is_mouse_interface=_hid_interface(real) == 2,
                vendor=vendor,
                product=int(match.group(2), 16),
                physical=fields.get("PHYS", ""),
                ev_bits=frozenset(parse_uevent_bitmask(fields.get("EV", ""))),
                key_bits=frozenset(parse_uevent_bitmask(fields.get("KEY", ""))),
                rel_bits=frozenset(parse_uevent_bitmask(fields.get("REL", ""))),
                abs_bits=frozenset(parse_uevent_bitmask(fields.get("ABS", ""))),
            )
        )
    return nodes


class DeviceNotFound(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class HidrawTransport:
    """Offener /dev/hidrawN-Handle mit Frame-Austausch."""

    def __init__(self, node: HidrawNode, timeout: float = 1.5, verbose: bool = False):
        self.node = node
        self.timeout = timeout
        self.verbose = verbose
        self.fd: int | None = None

    # -- Lebenszyklus ---------------------------------------------------
    def open(self) -> "HidrawTransport":
        if self.fd is not None:
            return self
        try:
            self.fd = os.open(self.node.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError as exc:
            raise PermissionError(
                f"{self.node.path}: keine Rechte. udev-Regel installieren "
                "(./install.sh) oder mit sudo starten."
            ) from exc
        except FileNotFoundError as exc:
            raise DeviceNotFound(f"{self.node.path} existiert nicht (Dongle abgestoeszt?)") from exc
        return self

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "HidrawTransport":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Rohzugriffe ----------------------------------------------------
    def write_frame(self, frame: Frame) -> None:
        self.open()
        assert self.fd is not None
        raw = frame.build()
        if self.verbose:
            print(f">> {raw.hex(' ')}")
        try:
            os.write(self.fd, raw)
        except OSError as exc:
            if exc.errno == errno.ENOSYS:
                raise ProtocolError(
                    "Geraet akzeptiert keine Output-Reports ueber hidraw "
                    "(kein OUT-Endpoint). Bitte --backend usb verwenden."
                ) from exc
            raise

    def read_frame(self, deadline: float) -> Frame | None:
        """Naechsten 0x08-Frame lesen; andere Report-IDs werden verworfen."""
        assert self.fd is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], min(remaining, 0.25))
            if not ready:
                continue
            try:
                raw = os.read(self.fd, FRAME_LEN + 8)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno in (errno.ENXIO, errno.EPIPE, errno.EIO):
                    raise DeviceNotFound("Geraet getrennt") from exc
                raise
            if self.verbose:
                print(f"<< {raw.hex(' ')}")
            if raw[0] != REPORT_ID:
                continue  # laufende Eingabereports (Mausbewegung) ignorieren
            if len(raw) not in (FRAME_LEN, FRAME_LEN - 1):
                continue
            try:
                return Frame.parse(raw)
            except ProtocolError:
                continue

    def exchange(
        self,
        frame: Frame,
        expect_cmd: int | tuple[int, ...] | None = None,
        ok_status: tuple[int, ...] = (0x00,),
    ) -> Frame:
        """Frame senden und zugehoerige Antwort zurueckgeben."""
        self.open()
        assert self.fd is not None
        expected: tuple[int, ...] = (
            (frame.cmd,)
            if expect_cmd is None
            else (expect_cmd if isinstance(expect_cmd, (tuple, set, frozenset)) else (expect_cmd,))
        )
        self.drain()
        self.write_frame(frame)
        deadline = time.monotonic() + self.timeout
        while True:
            response = self.read_frame(deadline)
            if response is None:
                raise TimeoutError(
                    f"Keine Antwort auf Kommando {'/'.join(f'{c:04x}' for c in expected)} "
                    f"({self.timeout:.1f}s). Maus evtl. im Schlafmodus - erst bewegen/Klick."
                )
            if response.cmd in expected:
                if response.status not in ok_status:
                    raise ProtocolError(
                        f"Geraet meldet Status {response.status:#04x} fuer Kommando {response.cmd:#04x}"
                    )
                return response
            # andere 0x08-Frames (z. B. Reports des Dongles) ueberspringen

    def drain(self, seconds: float = 0.05) -> int:
        """Laufende Reports verwerfen, damit die Antwort nicht vermischt wird."""
        assert self.fd is not None
        count = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.01)
            if not ready:
                break
            try:
                os.read(self.fd, 64)
                count += 1
            except (BlockingIOError, OSError):
                break
        return count

    # -- Feature-Reports ------------------------------------------------
    def get_feature(self, report_id: int, size: int) -> bytes:
        self.open()
        assert self.fd is not None
        buffer = array.array("B", bytes([report_id]) + bytes(size - 1))
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(size), buffer, True)
        return buffer.tobytes()

    def set_feature(self, report_id: int, payload: bytes) -> bytes:
        self.open()
        assert self.fd is not None
        data = bytes([report_id]) + payload
        buffer = array.array("B", data)
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buffer)), buffer, True)
        return buffer.tobytes()

    # -- Diagnose -------------------------------------------------------
    def probe(self) -> dict:
        out: dict = {"node": self.node.describe(), "reports": {}}
        for report_id, sizes in sorted(self.node.reports.reports.items()):
            out["reports"][f"0x{report_id:02x}"] = sizes.as_dict()
        out["collections"] = [
            f"0x{c.usage_page:04x}/0x{c.usage:04x} reports={','.join(f'0x{r:02x}' for r in c.report_ids)}"
            for c in self.node.reports.collections
        ]
        features = [rid for rid, sizes in self.node.reports.reports.items() if sizes.feature]
        out["feature_reports"] = {}
        for report_id in features:
            size = self.node.reports.feature_size(report_id)
            try:
                out["feature_reports"][f"0x{report_id:02x}"] = self.get_feature(report_id, size).hex(" ")
            except OSError as exc:
                out["feature_reports"][f"0x{report_id:02x}"] = f"Lesefehler: {exc.strerror}"
        return out

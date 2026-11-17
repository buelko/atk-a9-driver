"""atk-a9 - Kommandozeilen-Werkzeug fuer ATK/VXE-Maeuse (z. B. ATK A9 Ultra).

Beispiele:
  atk-a9 scan                      Geraete auflisten
  atk-a9 status                    kompletter Status (auch --json)
  atk-a9 battery                   Akkustand
  atk-a9 dpi                       DPI-Stufen anzeigen
  atk-a9 dpi set 2 1600            Stufe 2 auf 1600 DPI setzen
  atk-a9 dpi set 2 1600 3200      Stufe 2: X=1600, Y=3200
  atk-a9 dpi select 3              aktive Stufe waehlen
  atk-a9 polling                   Polling-Rate anzeigen
  atk-a9 polling set 500           Polling-Rate setzen
  atk-a9 lod                       Lift-off anzeigen
  atk-a9 lod set 1.0               Lift-off setzen (mm oder code:N)
  atk-a9 buttons                   Tastenbelegung anzeigen
  atk-a9 buttons set 5 mouse back  Slot 5 -> "Zurueck"
  atk-a9 advanced                  Debounce/MotionSync/Schlaf usw.
  atk-a9 advanced set --debounce 4 --sleep 300
  atk-a9 probe                     Diagnose (Interfaces, Reports, Input-Nodes)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import protocol as p
from .device import AtkMouse
from .hidraw import DeviceNotFound, list_hidraw_nodes, list_input_nodes


def _connect(args: argparse.Namespace) -> AtkMouse:
    return AtkMouse.connect(
        node_path=getattr(args, "device", None),
        timeout=getattr(args, "timeout", 1.5),
        verbose=getattr(args, "verbose", False),
    )


def _fail(message: str) -> int:
    print(f"Fehler: {message}", file=sys.stderr)
    return 1


# -- Unterkommandos ---------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    nodes = list_hidraw_nodes()
    if not nodes:
        return _fail("kein ATK/VXE/Compx-Geraet gefunden (373b/3554)")
    for node in nodes:
        marker = " [Konfig-Interface]" if node.is_config_interface else ""
        print(node.describe() + marker)
    for inode in list_input_nodes():
        print("  " + inode.describe())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        status = mouse.status(include_buttons=not args.no_buttons)
    if args.json:
        print(json.dumps(status.to_dict(), indent=2, ensure_ascii=False))
        return 0
    print(f"Geraet       : {status.hid_name} ({status.usb}) auf {status.node}")
    print(f"Modell       : {status.model_name} (CID/MID {status.cid_mid or '?'})")
    print(f"Sensor       : {status.sensor or '?'}  DPI {status.dpi_min}-{status.dpi_max} ({status.dpi_family})")
    print(f"Firmware     : {status.firmware or '?'}")
    if status.battery:
        print(f"Batterie     : {status.battery.describe()}")
    if status.system:
        print(
            f"Polling      : {status.system.polling_hz or '?'} Hz"
            f" (max. {status.max_polling_hz or '?'} Hz am Empfaenger)"
        )
        print(f"DPI-Stufen   : {status.system.stage_count}, aktiv: Stufe {status.system.active_stage}")
    if status.lod_mm is not None:
        print(f"Lift-off     : {status.lod_mm} mm")
    for stage in status.dpi_stages:
        active = " *" if status.system and stage.index == status.system.active_stage else ""
        print(f"  Stufe {stage.index}: {stage.describe()}{active}")
    if status.advanced:
        a = status.advanced
        print(
            "Erweitert    : "
            f"Debounce {a.debounce_ms} ms, MotionSync {_onoff(a.motion_sync)}, "
            f"Schlaf {a.sleep_seconds} s, Linear {_onoff(a.straight_line)}, Ripple {_onoff(a.ripple)}"
        )
    if status.sensor_row:
        print(f"Sensormodus  : {status.sensor_row.performance_mode}")
    if status.angle:
        print(f"Winkel       : {status.angle[0]} Grad, Angle-Snap {_onoff(status.angle[1])}")
    for button in status.buttons:
        print(f"  Taste {button.slot}: {button.describe()}")
    for note in status.notes:
        print(f"Hinweis      : {note}")
    return 0


def _onoff(value: bool | None) -> str:
    return "?" if value is None else ("an" if value else "aus")


def cmd_battery(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        response = mouse.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
        battery = p.parse_battery(response.data, response.declared)
    if args.json:
        print(json.dumps({"percent": battery.percent, "charging": battery.charging, "millivolts": battery.voltage_mv}))
    else:
        parts = [f"{battery.percent}%" if battery.percent is not None else "unbekannt"]
        if battery.charging:
            parts.append("laedt")
        if battery.voltage_mv:
            parts.append(f"{battery.voltage_mv / 1000:.2f} V")
        print("Batterie: " + ", ".join(parts))
    return 0


def cmd_dpi(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.dpi_action == "set":
            stage = mouse.set_dpi_stage(args.stage, args.x, args.y)
            print(f"Stufe {stage.index}: {stage.describe()}")
        elif args.dpi_action == "select":
            system = mouse.select_stage(args.stage)
            print(f"aktive Stufe: {system.active_stage}")
        elif args.dpi_action == "stages":
            system = mouse.set_stage_count(args.count)
            print(f"DPI-Stufen: {system.stage_count}")
        else:
            system = mouse.read_system()
            lo, hi, family = mouse.dpi_limits()
            print(f"Sensorbereich: {lo}-{hi} DPI ({family})")
            for stage in mouse.read_dpi_stages():
                active = " *" if stage.index == system.active_stage else ""
                print(f"  Stufe {stage.index}: {stage.describe()}{active}")
    return 0


def cmd_polling(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.polling_action == "set":
            system = mouse.set_polling(args.hz)
            print(f"Polling-Rate: {system.polling_hz} Hz")
        else:
            system = mouse.read_system()
            options = ", ".join(str(hz) for hz in mouse.polling_options())
            print(f"Polling-Rate: {system.polling_hz or '?'} Hz (moeglich: {options})")
    return 0


def cmd_lod(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.lod_action == "set":
            code, mm = mouse.set_lod(args.value)
        else:
            code, mm = mouse.read_lod()
        table = mouse.lod_table()
        options = ", ".join(f"{v}" for v in sorted(set(table.codes.values())))
        print(f"Lift-off: {mm if mm is not None else '?'} mm (Code {code}; moeglich: {options} mm)")
    return 0


def cmd_buttons(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.buttons_action == "set":
            cls = _parse_button_class(args.cls)
            value1, value2 = _parse_button_value(cls, args.value)
            record = mouse.set_button(args.slot, cls, value1, value2)
            print(f"Taste {record.slot}: {record.describe()}")
        else:
            for button in mouse.read_buttons():
                print(f"Taste {button.slot}: {button.describe()}")
    return 0


def _parse_button_class(text: str) -> int:
    aliases = {
        "disabled": 0x00, "aus": 0x00,
        "mouse": 0x01, "maus": 0x01,
        "dpi": 0x02,
        "hscroll": 0x03,
        "fire": 0x04,
        "shortcut": 0x05,
        "macro": 0x06, "makro": 0x06,
        "polling": 0x07,
        "light": 0x08, "licht": 0x08,
        "profile": 0x09, "profil": 0x09,
        "dpilock": 0x0A,
        "wheel": 0x0B, "rad": 0x0B,
    }
    key = text.strip().lower()
    if key in aliases:
        return aliases[key]
    return int(text, 0)


def _parse_button_value(cls: int, text: str) -> tuple[int, int]:
    key = text.strip().lower()
    if cls == 0x01 and key in p.MOUSE_BUTTON_ALIASES:
        return p.MOUSE_BUTTON_ALIASES[key], 0
    if cls == 0x02:
        dpi_aliases = {"cycle": 1, "up": 2, "hoch": 2, "down": 3, "runter": 3}
        if key in dpi_aliases:
            return dpi_aliases[key], 0
    if "," in key:
        v1, v2 = key.split(",", 1)
        return int(v1, 0), int(v2, 0)
    return int(text, 0), 0


def cmd_advanced(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.advanced_action == "set":
            advanced = mouse.set_advanced(
                debounce_ms=args.debounce,
                motion_sync=_tristate(args.motion_sync),
                sleep_seconds=args.sleep,
                straight_line=_tristate(args.linear),
                ripple=_tristate(args.ripple),
            )
        else:
            advanced = mouse.read_advanced()
        print(f"Debounce   : {advanced.debounce_ms} ms (moeglich: {', '.join(map(str, p.DEBOUNCE_MS))})")
        print(f"MotionSync : {_onoff(advanced.motion_sync)}")
        print(f"Schlafzeit : {advanced.sleep_seconds} s (moeglich: {', '.join(map(str, p.SLEEP_SECONDS))})")
        print(f"Linear     : {_onoff(advanced.straight_line)}")
        print(f"Ripple     : {_onoff(advanced.ripple)}")
    return 0


def _tristate(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("1", "an", "on", "true", "ja")


def cmd_sensor(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        if args.sensor_action == "set":
            row = mouse.set_sensor_mode(args.mode)
        else:
            row = mouse.read_sensor_row()
        print(f"Sensormodus: {row.performance_mode} (basic|competitive)")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    nodes = list_hidraw_nodes()
    if not nodes:
        return _fail("kein ATK/VXE/Compx-Geraet gefunden")
    for node in nodes:
        print(node.describe())
        print("  " + node.reports.describe().replace("\n", "\n  "))
    try:
        with _connect(args) as mouse:
            print(f"Konfig-Knoten : {mouse.node.path}")
            print(f"CID/MID       : {mouse.cid_mid or '?'}")
            print(f"Modell        : {mouse.model.name if mouse.model else '?'}")
            print(f"Firmware Maus : {mouse.firmware_version() or '?'}")
            print(f"Firmware Dongle: {mouse.receiver_version() or '?'}")
    except (DeviceNotFound, TimeoutError, p.ProtocolError, PermissionError) as exc:
        print(f"Kommunikation fehlgeschlagen: {exc}")
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    with _connect(args) as mouse:
        data = mouse.dump(args.start, args.end)
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        print(f"{args.start + offset:04x}: {chunk.hex(' ')}")
    return 0


# -- Parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atk-a9",
        description="Treiber-Werkzeug fuer ATK/VXE-Maeuse (z. B. ATK A9 Ultra ueber den 1K-Dongle)",
    )
    parser.add_argument("--device", "-d", help="hidraw-Knoten (z. B. /dev/hidraw1)")
    parser.add_argument("--timeout", type=float, default=1.5, help="Antwort-Timeout in Sekunden")
    parser.add_argument("--verbose", "-v", action="store_true", help="Frames anzeigen")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Geraete auflisten").set_defaults(func=cmd_scan)

    s = sub.add_parser("status", help="kompletter Status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-buttons", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("battery", help="Akkustand")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_battery)

    s = sub.add_parser("dpi", help="DPI-Stufen")
    dsub = s.add_subparsers(dest="dpi_action")
    dset = dsub.add_parser("set", help="Stufe setzen: dpi set <stufe> <x> [y]")
    dset.add_argument("stage", type=int)
    dset.add_argument("x", type=int)
    dset.add_argument("y", type=int, nargs="?")
    dsel = dsub.add_parser("select", help="aktive Stufe waehlen")
    dsel.add_argument("stage", type=int)
    dcnt = dsub.add_parser("stages", help="Anzahl der Stufen setzen")
    dcnt.add_argument("count", type=int)
    s.set_defaults(func=cmd_dpi)

    s = sub.add_parser("polling", help="Polling-Rate")
    psub = s.add_subparsers(dest="polling_action")
    pset = psub.add_parser("set")
    pset.add_argument("hz", type=int)
    s.set_defaults(func=cmd_polling)

    s = sub.add_parser("lod", help="Lift-off-Distanz")
    lsub = s.add_subparsers(dest="lod_action")
    lset = lsub.add_parser("set")
    lset.add_argument("value", help="mm (z. B. 1.0) oder code:N")
    s.set_defaults(func=cmd_lod)

    s = sub.add_parser("buttons", help="Tastenbelegung")
    bsub = s.add_subparsers(dest="buttons_action")
    bset = bsub.add_parser("set", help="buttons set <slot> <klasse> <wert>")
    bset.add_argument("slot", type=int)
    bset.add_argument("cls", help="mouse|dpi|hscroll|fire|shortcut|macro|polling|wheel|aus|0xNN")
    bset.add_argument("value", help="z. B. left/right/middle/back/forward, cycle/up/down oder Zahl[,Zahl]")
    s.set_defaults(func=cmd_buttons)

    s = sub.add_parser("advanced", help="Debounce, MotionSync, Schlafzeit usw.")
    asub = s.add_subparsers(dest="advanced_action")
    aset = asub.add_parser("set")
    aset.add_argument("--debounce", type=int, help=f"ms, eins aus {p.DEBOUNCE_MS}")
    aset.add_argument("--motion-sync", dest="motion_sync", help="an|aus")
    aset.add_argument("--sleep", type=int, help=f"Sekunden, eins aus {p.SLEEP_SECONDS}")
    aset.add_argument("--linear", help="an|aus (Geradenkorrektur)")
    aset.add_argument("--ripple", help="an|aus (Ripple-Korrektur)")
    s.set_defaults(func=cmd_advanced)

    s = sub.add_parser("sensor", help="Sensormodus (basic/competitive)")
    ssub = s.add_subparsers(dest="sensor_action")
    sset = ssub.add_parser("set")
    sset.add_argument("mode", choices=("basic", "competitive"))
    s.set_defaults(func=cmd_sensor)

    sub.add_parser("probe", help="Diagnose").set_defaults(func=cmd_probe)

    s = sub.add_parser("dump", help="EEPROM-Bereich hexdumpen")
    s.add_argument("start", type=lambda x: int(x, 0))
    s.add_argument("end", type=lambda x: int(x, 0))
    s.set_defaults(func=cmd_dump)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DeviceNotFound as exc:
        return _fail(str(exc))
    except PermissionError as exc:
        return _fail(
            f"{exc}\nKeine Berechtigung fuer den hidraw-Knoten. "
            "udev-Regel installieren: sudo ./install.sh (danach Dongle neu einstecken)."
        )
    except TimeoutError as exc:
        return _fail(
            f"{exc}\nKeine Antwort - Maus einschalten/bewegen und ATK HUB (falls offen) schliessen."
        )
    except (p.ProtocolError, ValueError) as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

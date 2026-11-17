"""atk-a9-tray - Batteriesymbol fuer die ATK A9 Ultra im XFCE-Systray.

Zeichnet ein kleines Batterie-Icon (Fuellstand + Farbe), aktualisiert sich
periodisch und oeffnet per Klick die Einstellungs-GUI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, GdkPixbuf  # noqa: E402

import cairo  # noqa: E402

from . import protocol as p  # noqa: E402
from .device import AtkMouse  # noqa: E402
from .hidraw import DeviceNotFound  # noqa: E402

INTERVAL_SECONDS = 120
RETRY_SECONDS = 30
ICON_SIZE = 22
GUI_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "atk-a9-gui")


def draw_battery_pixbuf(percent: int | None, charging: bool) -> GdkPixbuf.Pixbuf:
    size = ICON_SIZE
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Batteriekoerper (liegend)
    x, y, w, h = 1.5, 6.5, 16, 9
    ctx.set_line_width(1.5)
    ctx.set_source_rgba(0.9, 0.9, 0.9, 1)
    ctx.rectangle(x, y, w, h)
    ctx.stroke()
    # Pol
    ctx.rectangle(x + w + 0.5, y + 2.5, 2.5, 4)
    ctx.fill()

    if percent is None:
        # unbekannt: graues Fragezeichen
        ctx.set_source_rgba(0.7, 0.7, 0.7, 1)
        ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(10)
        ctx.move_to(x + 5, y + h - 1.5)
        ctx.show_text("?")
    else:
        level = max(0, min(100, percent))
        if charging:
            r, g, b = (0.3, 0.6, 1.0)
        elif level <= 15:
            r, g, b = (0.9, 0.2, 0.2)
        elif level <= 35:
            r, g, b = (0.95, 0.7, 0.1)
        else:
            r, g, b = (0.3, 0.8, 0.3)
        ctx.set_source_rgba(r, g, b, 1)
        inner = (w - 3) * level / 100.0
        ctx.rectangle(x + 1.5, y + 1.5, inner, h - 3)
        ctx.fill()
        if charging:
            # Blitz
            ctx.set_source_rgba(1, 1, 1, 0.95)
            ctx.move_to(x + 9, y + 1)
            ctx.line_to(x + 5, y + 5.5)
            ctx.line_to(x + 8, y + 5.5)
            ctx.line_to(x + 6.5, y + 8.5)
            ctx.line_to(x + 11, y + 3.5)
            ctx.line_to(x + 8, y + 3.5)
            ctx.close_path()
            ctx.fill()

    # cairo ARGB32 (premultipliziert, little-endian BGRA) -> RGBA fuer GdkPixbuf
    surface.flush()
    raw = bytes(surface.get_data())
    stride = surface.get_stride()
    rgba = bytearray(size * size * 4)
    for row in range(size):
        for col in range(size):
            b, g, r, a = raw[row * stride + col * 4 : row * stride + col * 4 + 4]
            if a:
                r = min(255, r * 255 // a)
                g = min(255, g * 255 // a)
                b = min(255, b * 255 // a)
            i = (row * size + col) * 4
            rgba[i : i + 4] = bytes((r, g, b, a))
    return GdkPixbuf.Pixbuf.new_from_data(
        bytes(rgba), GdkPixbuf.Colorspace.RGB, True, 8, size, size, size * 4
    )


class TrayApp:
    def __init__(self) -> None:
        self.icon = Gtk.StatusIcon()
        self.icon.set_title("ATK A9 Ultra")
        self.icon.connect("activate", self.on_activate)
        self.icon.connect("popup-menu", self.on_popup)
        self.set_state(None, False, "verbinde ...")
        self._busy = False
        GLib.idle_add(self.schedule_refresh)

    # -- Anzeige ------------------------------------------------------------

    def set_state(self, percent: int | None, charging: bool, note: str | None = None) -> None:
        self.icon.set_from_pixbuf(draw_battery_pixbuf(percent, charging))
        if percent is None:
            tooltip = f"ATK A9 Ultra: {note or 'nicht erreichbar'}"
        else:
            tooltip = f"ATK A9 Ultra: {percent} %" + (" (laedt)" if charging else "")
            if note:
                tooltip += f"\n{note}"
        self.icon.set_tooltip_text(tooltip)

    # -- Abfrage ------------------------------------------------------------

    def schedule_refresh(self) -> bool:
        self.refresh()
        return False

    def refresh(self, _widget: Gtk.Widget | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._read_battery, daemon=True).start()

    def _read_battery(self) -> None:
        percent: int | None = None
        charging = False
        note: str | None = None
        retry = False
        try:
            with AtkMouse.connect(identify=False) as mouse:
                response = mouse.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
                battery = p.parse_battery(response.data, response.declared)
                percent, charging = battery.percent, bool(battery.charging)
        except TimeoutError:
            note = "Maus schlaeft (bewegen zum Aufwecken)"
            retry = True
        except (DeviceNotFound, PermissionError, p.ProtocolError, OSError) as exc:
            note = str(exc)
            retry = True
        GLib.idle_add(self._apply, percent, charging, note, retry)

    def _apply(self, percent: int | None, charging: bool, note: str | None, retry: bool) -> bool:
        self._busy = False
        self.set_state(percent, charging, note)
        GLib.timeout_add_seconds(RETRY_SECONDS if retry else INTERVAL_SECONDS, self.schedule_refresh)
        return False

    # -- Menue --------------------------------------------------------------

    def on_activate(self, _icon: Gtk.StatusIcon) -> None:
        subprocess.Popen([GUI_BIN])

    def on_popup(self, icon: Gtk.StatusIcon, button: int, time: int) -> None:
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Jetzt aktualisieren")
        item.connect("activate", self.refresh)
        menu.append(item)
        item = Gtk.MenuItem(label="Einstellungen oeffnen")
        item.connect("activate", lambda _w: subprocess.Popen([GUI_BIN]))
        menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label="Beenden")
        item.connect("activate", lambda _w: Gtk.main_quit())
        menu.append(item)
        menu.show_all()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)


def main() -> int:
    TrayApp()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

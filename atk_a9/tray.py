# SPDX-License-Identifier: AGPL-3.0-or-later
"""atk-a9-tray - Batteriesymbol fuer die ATK A9 Ultra in der Leiste.

Zeichnet ein kleines Batterie-Icon (Fuellstand + Farbe), aktualisiert sich
periodisch und oeffnet per Klick die Einstellungs-GUI.

Backend-Wahl zur Laufzeit:
  * AppIndicator (Ayatana oder Ubuntu AppIndicator3) - funktioniert unter
    Wayland (GNOME mit AppIndicator-Erweiterung, KDE, XFCE) und X11.
  * Fallback Gtk.StatusIcon - klassischer X11-Systray (XFCE, alte Umgebungen).
Unter Wayland wird AppIndicator bevorzugt, weil Gtk.StatusIcon dort kein
Symbol zeigt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, GdkPixbuf  # noqa: E402

import cairo  # noqa: E402


def _load_appindicator():
    """AppIndicator-Modul laden (Ayatana bevorzugt), sonst None."""
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            from gi.repository import GLib as _  # noqa: F401
            module = __import__("gi.repository", fromlist=[namespace])
            return getattr(module, namespace)
        except (ValueError, ImportError):
            continue
    return None


AppIndicator = _load_appindicator()


def _running_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"

from . import protocol as p  # noqa: E402
from .device import AtkMouse, MouseOffline  # noqa: E402
from .hidraw import DeviceBusy, DeviceNotFound  # noqa: E402

INTERVAL_SECONDS = 120
RETRY_SECONDS = 30
ICON_SIZE = 22
LOW_BATTERY = 15       # Warnung bei <= 15 %
LOW_BATTERY_RESET = 20  # erst ab >= 20 % (oder beim Laden) wieder scharf
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Die GUI als Modul starten: funktioniert aus dem Quellbaum (bin/-Skripte)
# genauso wie nach einer Paket-Installation.
GUI_CMD = [sys.executable, "-m", "atk_a9.gui"]


def gui_env() -> dict[str, str]:
    env = dict(os.environ)
    paths = [PACKAGE_PARENT] + [x for x in env.get("PYTHONPATH", "").split(os.pathsep) if x]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def low_battery_step(percent: int | None, charging: bool, warned: bool) -> tuple[bool, bool]:
    """Entscheidet ueber die Akku-Warnung: (jetzt_warnen, neuer_warned_Zustand).

    Gewarnt wird einmal beim Unterschreiten der Schwelle, nicht bei jeder Abfrage.
    """
    if percent is None:
        return False, warned
    if charging or percent >= LOW_BATTERY_RESET:
        return False, False
    if percent <= LOW_BATTERY and not warned:
        return True, True
    return False, warned


def notify(title: str, body: str) -> None:
    """Desktop-Benachrichtigung; fehlt die Infrastruktur, passiert nichts."""
    try:
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify

        if not Notify.is_initted():
            Notify.init("ATK A9 Ultra")
        Notify.Notification.new(title, body, "battery-caution").show()
        return
    except Exception:  # noqa: BLE001 - libnotify fehlt oder kein Dienst
        pass
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-i", "battery-caution", title, body], timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def draw_battery_pixbuf(percent: int | None, charging: bool, stale: bool = False) -> GdkPixbuf.Pixbuf:
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
        if stale:
            # letzter bekannter Stand (Maus schlaeft): ausgegraut
            r, g, b = (0.6, 0.6, 0.6)
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


def save_icon_png(percent: int | None, charging: bool, stale: bool = False) -> str:
    """Icon als PNG in eine temporaere Datei schreiben (fuer AppIndicator, das
    nur Icon-Namen/Pfade kennt). Gibt den Dateipfad zurueck."""
    pixbuf = draw_battery_pixbuf(percent, charging, stale)
    fd, path = tempfile.mkstemp(prefix="atk-a9-bat-", suffix=".png")
    os.close(fd)
    pixbuf.savev(path, "png", [], [])
    return path


class _StatusIconBackend:
    """Klassischer X11-Systray ueber Gtk.StatusIcon."""

    def __init__(self, app: "TrayApp") -> None:
        self.icon = Gtk.StatusIcon()
        self.icon.set_title("ATK A9 Ultra")
        self.icon.connect("activate", lambda _i: app.open_gui())
        self.icon.connect("popup-menu", self._popup)
        self._app = app

    def set_icon(self, pixbuf, tooltip: str) -> None:
        self.icon.set_from_pixbuf(pixbuf)
        self.icon.set_tooltip_text(tooltip)

    def _popup(self, icon, button: int, time: int) -> None:
        menu = self._app.build_menu()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)


class _AppIndicatorBackend:
    """AppIndicator-Backend - funktioniert unter Wayland und X11."""

    def __init__(self, app: "TrayApp") -> None:
        self._app = app
        self._icon_file: str | None = None
        self.indicator = AppIndicator.Indicator.new(
            "atk-a9-ultra",
            "battery",  # Fallback-Icon-Name aus dem Theme, bis das erste PNG da ist
            AppIndicator.IndicatorCategory.HARDWARE,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        # AppIndicator braucht zwingend ein Menue (Linksklick oeffnet es; der
        # erste Eintrag dient als Standardaktion).
        self.indicator.set_menu(self._app.build_menu(default_open=True))

    def set_icon(self, pixbuf, tooltip: str) -> None:
        old = self._icon_file
        self._icon_file = save_icon_png(
            self._app._last_percent, self._app._last_charging, self._app._last_stale
        )
        self.indicator.set_icon_full(self._icon_file, tooltip)
        if hasattr(self.indicator, "set_title"):
            self.indicator.set_title(tooltip)
        if old and os.path.exists(old):
            try:
                os.unlink(old)
            except OSError:
                pass


class TrayApp:
    def __init__(self, backend: str = "auto") -> None:
        self._last_percent: int | None = None
        self._last_charging = False
        self._last_stale = False
        self.backend = self._make_backend(backend)
        self._busy = False
        self._last: tuple[int, bool] | None = None  # letzter gelesener Stand
        self._warned = False
        self._children: list[subprocess.Popen] = []
        self.set_state(None, False, "verbinde ...")
        GLib.idle_add(self.schedule_refresh)

    # -- Anzeige ------------------------------------------------------------

    def _make_backend(self, choice: str):
        """Backend waehlen: 'appindicator', 'statusicon' oder 'auto'."""
        want_indicator = choice == "appindicator" or (
            choice == "auto" and (AppIndicator is not None and (_running_wayland() or True))
        )
        # Unter Wayland ist AppIndicator Pflicht; sonst ist es trotzdem die
        # robustere Wahl, wenn verfuegbar. Fallback nur, wenn es fehlt.
        if want_indicator and AppIndicator is not None:
            return _AppIndicatorBackend(self)
        if choice == "appindicator" and AppIndicator is None:
            print(
                "AppIndicator nicht verfuegbar - installiere "
                "gir1.2-ayatanaappindicator3-0.1 (oder gir1.2-appindicator3-0.1). "
                "Fallback auf Gtk.StatusIcon.",
                file=sys.stderr,
            )
        if _running_wayland() and AppIndicator is None:
            print(
                "Wayland erkannt, aber AppIndicator fehlt - das Symbol bleibt "
                "unter Wayland womoeglich unsichtbar. Installiere "
                "gir1.2-ayatanaappindicator3-0.1.",
                file=sys.stderr,
            )
        return _StatusIconBackend(self)

    def set_state(
        self, percent: int | None, charging: bool, note: str | None = None, stale: bool = False
    ) -> None:
        self._last_percent, self._last_charging, self._last_stale = percent, charging, stale
        if percent is None:
            tooltip = f"ATK A9 Ultra: {note or 'nicht erreichbar'}"
        else:
            tooltip = f"ATK A9 Ultra: {percent} %" + (" (laedt)" if charging else "")
            if stale:
                tooltip += " (letzter Stand)"
            if note:
                tooltip += f"\n{note}"
        self.backend.set_icon(draw_battery_pixbuf(percent, charging, stale), tooltip)

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
        asleep = False
        try:
            with AtkMouse.connect(identify=False) as mouse:
                # Der Dongle weiss sofort, ob die Maus verbunden ist. Das spart
                # bei schlafender Maus das Warten auf den Timeout (und haelt die
                # Geraetesperre nicht unnoetig lange).
                online = mouse.online_status()
                if online is not None and online["status"] == 0:
                    asleep = True
                else:
                    response = mouse.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
                    battery = p.parse_battery(response.data, response.declared)
                    percent, charging = battery.percent, bool(battery.charging)
        except MouseOffline:
            asleep = True
        except DeviceBusy as exc:
            note = str(exc)
            retry = True
        except TimeoutError:
            note = "keine Antwort vom Geraet"
            retry = True
        except (DeviceNotFound, PermissionError, p.ProtocolError, OSError) as exc:
            note = str(exc)
            retry = True
        except Exception as exc:  # noqa: BLE001 - sonst bliebe _busy fuer immer gesetzt
            note = f"{type(exc).__name__}: {exc}"
            retry = True
        if asleep:
            note = "Maus schlaeft (bewegen zum Aufwecken)"
            retry = True
        GLib.idle_add(self._apply, percent, charging, note, retry, asleep)

    def _apply(
        self, percent: int | None, charging: bool, note: str | None, retry: bool, asleep: bool = False
    ) -> bool:
        self._busy = False
        self._children = [child for child in self._children if child.poll() is None]
        if percent is not None:
            self._last = (percent, charging)
            self.set_state(percent, charging, note)
            warn, self._warned = low_battery_step(percent, charging, self._warned)
            if warn:
                notify("ATK A9 Ultra: Akku fast leer", f"Noch {percent} % - bitte laden.")
        elif asleep and self._last is not None:
            # Schlafende Maus: letzten Stand ausgegraut weiter anzeigen statt "?"
            self.set_state(self._last[0], False, note, stale=True)
        else:
            self.set_state(None, False, note)
        GLib.timeout_add_seconds(RETRY_SECONDS if retry else INTERVAL_SECONDS, self.schedule_refresh)
        return False

    def open_gui(self, _widget: Gtk.Widget | None = None) -> None:
        """GUI starten. Laeuft sie schon, holt der neue Prozess nur ihr Fenster
        nach vorn und beendet sich (siehe gui.acquire_instance)."""
        # beendete Kindprozesse abholen, sonst bleiben Zombies zurueck
        self._children = [child for child in self._children if child.poll() is None]
        try:
            self._children.append(subprocess.Popen(GUI_CMD, env=gui_env()))
        except OSError as exc:
            notify("ATK A9 Ultra", f"Einstellungen lassen sich nicht starten: {exc}")

    # -- Menue --------------------------------------------------------------

    def build_menu(self, default_open: bool = False) -> Gtk.Menu:
        """Kontextmenue (von beiden Backends genutzt)."""
        menu = Gtk.Menu()
        if default_open:
            # AppIndicator oeffnet beim Linksklick das Menue; der erste Eintrag
            # ist dort die naheliegende Standardaktion.
            item = Gtk.MenuItem(label="Einstellungen oeffnen")
            item.connect("activate", self.open_gui)
            menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())
            item = Gtk.MenuItem(label="Jetzt aktualisieren")
            item.connect("activate", self.refresh)
            menu.append(item)
        else:
            item = Gtk.MenuItem(label="Jetzt aktualisieren")
            item.connect("activate", self.refresh)
            menu.append(item)
            item = Gtk.MenuItem(label="Einstellungen oeffnen")
            item.connect("activate", self.open_gui)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label="Beenden")
        item.connect("activate", lambda _w: Gtk.main_quit())
        menu.append(item)
        menu.show_all()
        return menu


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="atk-a9-tray", description="Akku-Symbol fuer die ATK A9 Ultra")
    parser.add_argument(
        "--backend", choices=("auto", "appindicator", "statusicon"), default="auto",
        help="Tray-Backend (Standard auto: AppIndicator wenn vorhanden, sonst StatusIcon)",
    )
    args = parser.parse_args(argv)
    TrayApp(backend=args.backend)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())

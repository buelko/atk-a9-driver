# SPDX-License-Identifier: GPL-3.0-or-later
"""atk-a9-gui - grafische Oberflaeche fuer die ATK A9 Ultra (Tkinter).

Alle Geraetezugriffe laufen in einem Hintergrund-Thread, damit die
Oberflaeche nicht blockiert (die Maus kann schlafen -> Timeouts).
"""

from __future__ import annotations

import atexit
import os
import queue
import socket
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import keys as k
from . import protocol as p
from .device import AtkMouse, MouseStatus
from .device import MouseOffline, WriteVerificationError
from .hidraw import DeviceBusy, DeviceNotFound

POLL_BATTERY_MS = 60_000
PUMP_MS = 40

# Einfache Belegungen: Bezeichnung -> (klasse, wert1, wert2)
SIMPLE_ACTIONS: list[tuple[str, tuple[int, int, int]]] = [
    ("Linksklick", (0x01, 0x01, 0x00)),
    ("Rechtsklick", (0x01, 0x02, 0x00)),
    ("Mittelklick", (0x01, 0x04, 0x00)),
    ("Zurueck", (0x01, 0x08, 0x00)),
    ("Vorwaerts", (0x01, 0x10, 0x00)),
    ("DPI durchschalten", (0x02, 0x01, 0x00)),
    ("DPI erhoehen", (0x02, 0x02, 0x00)),
    ("DPI senken", (0x02, 0x03, 0x00)),
    ("Rad hoch", (0x0B, 0x01, 0x00)),
    ("Rad runter", (0x0B, 0x02, 0x00)),
    ("Scrollen links", (0x03, 0x01, 0x00)),
    ("Scrollen rechts", (0x03, 0x02, 0x00)),
    ("Polling-Rate umschalten", (0x07, 0x00, 0x00)),
    ("deaktiviert", (0x00, 0x00, 0x00)),
]
ACTION_KINDS = ("Einfache Funktion", "Schnellfeuer", "Tastenkombination", "Makro")
FIRE_COUNTS = {"Dauerfeuer (solange gehalten)": 0, "1 Klick": 1, "2 Klicks": 2, "3 Klicks": 3}
MACRO_MODE_CHOICES = {
    "solange gehalten": "hold",
    "bis zum naechsten Druck dieser Taste": "toggle",
    "bis zu einem beliebigen Tastendruck": "anykey",
    "feste Anzahl": "count",
}
WHEEL_GUARD_CHOICES = ["aus", "100", "150", "200", "300", "400", "500", "600", "800", "1000"]
MACRO_HELP = (
    "a = Taste tippen   +ctrl / -ctrl = druecken / loslassen   click:left = Mausklick\n"
    "+mouse:left / -mouse:left   wheel:up / wheel:down   50ms = Pause nach dem vorigen Ereignis\n"
    "Tasten: a-z, 0-9, f1-f24, enter, esc, space, tab, up, down, left, right, kp0-kp9 ...\n"
    "Beispiel:  +ctrl c 50ms -ctrl    (hoechstens 70 Ereignisse, Standardpause 10 ms)"
)


class ButtonDialog(tk.Toplevel):
    """Belegung einer Taste waehlen. Ergebnis: Funktion, die die Belegung am
    Geraet setzt (laeuft im Worker), oder None bei Abbruch."""

    def __init__(self, parent: tk.Tk, slot: int, current: str, macros: dict[int, str]) -> None:
        super().__init__(parent)
        self.title(f"Taste {slot} belegen")
        self.transient(parent)
        self.resizable(False, False)
        self.slot = slot
        self.result: Callable[[AtkMouse], Any] | None = None
        self.summary = ""

        pad = {"padx": 10, "pady": 4}
        ttk.Label(self, text=f"Aktuell: {current}").grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(self, text="Art").grid(row=1, column=0, sticky="w", **pad)
        kinds = [kind for kind in ACTION_KINDS if kind != "Tastenkombination" or slot <= k.SHORTCUT_SLOTS]
        self.kind_var = tk.StringVar(value=kinds[0])
        box = ttk.Combobox(self, textvariable=self.kind_var, state="readonly", values=kinds, width=30)
        box.grid(row=1, column=1, sticky="w", **pad)
        box.bind("<<ComboboxSelected>>", lambda _e: self._show_kind())

        self.frames: dict[str, ttk.Frame] = {}
        holder = ttk.Frame(self)
        holder.grid(row=2, column=0, columnspan=2, sticky="we", **pad)

        frame = self.frames["Einfache Funktion"] = ttk.Frame(holder)
        self.simple_var = tk.StringVar(value=SIMPLE_ACTIONS[0][0])
        ttk.Combobox(frame, textvariable=self.simple_var, state="readonly", width=30,
                     values=[name for name, _ in SIMPLE_ACTIONS]).pack(anchor="w")

        frame = self.frames["Schnellfeuer"] = ttk.Frame(holder)
        ttk.Label(frame, text="Abstand (ms)").grid(row=0, column=0, sticky="w", pady=2)
        self.fire_interval = tk.StringVar(value="50")
        ttk.Spinbox(frame, from_=k.FIRE_INTERVAL_MIN, to=k.FIRE_INTERVAL_MAX, textvariable=self.fire_interval,
                    width=6).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="Ausloesung").grid(row=1, column=0, sticky="w", pady=2)
        self.fire_count = tk.StringVar(value=next(iter(FIRE_COUNTS)))
        ttk.Combobox(frame, textvariable=self.fire_count, state="readonly", width=28,
                     values=list(FIRE_COUNTS)).grid(row=1, column=1, sticky="w", padx=8)

        frame = self.frames["Tastenkombination"] = ttk.Frame(holder)
        ttk.Label(frame, text="Kombination").grid(row=0, column=0, sticky="w")
        self.combo_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.combo_var, width=28).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="z. B. ctrl+c, ctrl+shift+esc, f5 (hoechstens 2 Modifier:\n"
                              "ctrl, shift, alt, win + eine Taste)").grid(row=1, column=0, columnspan=2, sticky="w", pady=4)

        frame = self.frames["Makro"] = ttk.Frame(holder)
        self.macro_names = {
            f"Makro {number}: {text if len(text) <= 34 else text[:31] + '...'}": number
            for number, text in sorted(macros.items())
        }
        ttk.Label(frame, text="Makro").grid(row=0, column=0, sticky="w", pady=2)
        self.macro_var = tk.StringVar(value=next(iter(self.macro_names), ""))
        ttk.Combobox(frame, textvariable=self.macro_var, state="readonly", width=44,
                     values=list(self.macro_names)).grid(row=0, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(frame, text="Wiederholen").grid(row=1, column=0, sticky="w", pady=2)
        self.macro_mode = tk.StringVar(value=next(iter(MACRO_MODE_CHOICES)))
        ttk.Combobox(frame, textvariable=self.macro_mode, state="readonly", width=34,
                     values=list(MACRO_MODE_CHOICES)).grid(row=1, column=1, sticky="w", padx=8)
        self.macro_count = tk.StringVar(value="1")
        ttk.Spinbox(frame, from_=1, to=k.MACRO_MAX_REPEAT, textvariable=self.macro_count, width=5).grid(
            row=1, column=2, sticky="w")
        if not self.macro_names:
            ttk.Label(frame, text="Noch kein Makro gespeichert - zuerst im Reiter 'Makros' anlegen.").grid(
                row=2, column=0, columnspan=3, sticky="w", pady=4)

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", **pad)
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Belegen", command=self._accept).pack(side="right")
        self._show_kind()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.wait_visibility()  # grab_set braucht ein sichtbares Fenster
        self.grab_set()

    def _show_kind(self) -> None:
        for frame in self.frames.values():
            frame.pack_forget()
        self.frames[self.kind_var.get()].pack(fill="x")

    def build_action(self) -> tuple[Callable[[AtkMouse], Any], str]:
        """Eingaben pruefen; wirft ValueError mit verstaendlicher Meldung."""
        slot = self.slot
        kind = self.kind_var.get()
        if kind == "Einfache Funktion":
            name = self.simple_var.get()
            cls, value1, value2 = dict(SIMPLE_ACTIONS)[name]
            return (lambda m: m.set_button(slot, cls, value1, value2)), name
        if kind == "Schnellfeuer":
            try:
                interval = int(self.fire_interval.get())
            except ValueError:
                raise ValueError("Abstand: bitte eine Zahl eingeben.") from None
            if not k.FIRE_INTERVAL_MIN <= interval <= k.FIRE_INTERVAL_MAX:
                raise ValueError(f"Abstand muss zwischen {k.FIRE_INTERVAL_MIN} und {k.FIRE_INTERVAL_MAX} ms liegen.")
            count = FIRE_COUNTS[self.fire_count.get()]
            return (lambda m: m.set_fire_key(slot, interval, count)), f"Schnellfeuer alle {interval} ms"
        if kind == "Tastenkombination":
            events = k.parse_combo(self.combo_var.get())
            return (lambda m: m.set_combo(slot, events)), "Tastenkombination " + k.describe_combo(events)
        if not self.macro_names:
            raise ValueError("Es ist noch kein Makro gespeichert.")
        macro = self.macro_names[self.macro_var.get()]
        mode: str | int = MACRO_MODE_CHOICES[self.macro_mode.get()]
        if mode == "count":
            try:
                mode = k.parse_macro_mode(int(self.macro_count.get()))
            except ValueError:
                raise ValueError(f"Anzahl muss zwischen 1 und {k.MACRO_MAX_REPEAT} liegen.") from None
        return (lambda m: m.set_macro_button(slot, macro, mode)), f"Makro {macro}"

    def _accept(self) -> None:
        try:
            self.result, self.summary = self.build_action()
        except ValueError as exc:
            messagebox.showerror("Taste belegen", str(exc), parent=self)
            return
        self.destroy()


def instance_socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, f"atk-a9-gui-{os.getuid()}.sock")


def acquire_instance(path: str | None = None) -> socket.socket | None:
    """Nur eine GUI gleichzeitig.

    Laeuft schon eine, wird sie gebeten, ihr Fenster nach vorn zu holen, und
    es kommt None zurueck. Sonst der lauschende Socket dieser Instanz.
    """
    path = path or instance_socket_path()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(path)
        client.sendall(b"raise")
        return None
    except OSError:
        pass  # keine Instanz (oder verwaister Socket einer abgestuerzten)
    finally:
        client.close()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)
    server.setblocking(False)

    def cleanup() -> None:
        server.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    atexit.register(cleanup)
    return server


class Worker(threading.Thread):
    """Serialisiert alle Geraetezugriffe."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.jobs: "queue.Queue[tuple[Callable[[AtkMouse], Any], Callable[[Any], None], Callable[[str], None]]]" = queue.Queue()
        self.mouse: AtkMouse | None = None

    def submit(self, work: Callable[[AtkMouse], Any], done: Callable[[Any], None], fail: Callable[[str], None]) -> None:
        self.jobs.put((work, done, fail))

    def run(self) -> None:
        while True:
            work, done, fail = self.jobs.get()
            self.run_one(work, done, fail)

    def run_one(
        self,
        work: Callable[[AtkMouse], Any],
        done: Callable[[Any], None],
        fail: Callable[[str], None],
    ) -> None:
        """Einen Auftrag ausfuehren. Darf nie eine Ausnahme durchlassen, sonst
        stirbt der Thread und die Oberflaeche bleibt auf 'arbeite...' haengen."""
        try:
            if self.mouse is None:
                self.mouse = AtkMouse.connect()
            result = work(self.mouse)
        except (DeviceBusy, MouseOffline) as exc:
            # Verbindung ist in Ordnung und bleibt offen
            fail(str(exc))
        except TimeoutError:
            fail("Keine Antwort vom Geraet - bitte erneut versuchen.")
        except (p.ProtocolError, ValueError, WriteVerificationError) as exc:
            fail(str(exc))
        except (DeviceNotFound, OSError) as exc:
            # Dongle abgezogen o. ae.: Handle verwerfen, naechster Auftrag verbindet neu
            self._drop()
            fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - Thread muss ueberleben
            self._drop()
            fail(f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")
        else:
            done(result)

    def _drop(self) -> None:
        if self.mouse is not None:
            try:
                self.mouse.close()
            except OSError:
                pass
            self.mouse = None


class App(tk.Tk):
    def __init__(self, instance: socket.socket | None = None) -> None:
        super().__init__()
        self.instance = instance
        self.title("ATK A9 Ultra")
        self.minsize(640, 700)
        self.worker = Worker()
        self.worker.start()
        self.status: MouseStatus | None = None
        self._busy = 0
        # Tkinter ist nicht threadsicher: der Worker legt Rueckrufe nur in diese
        # Queue, ausgefuehrt werden sie im Hauptthread (_pump).
        self._ui_calls: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self._build()
        self.after(PUMP_MS, self._pump)
        self.after(200, self.refresh)
        self.after(POLL_BATTERY_MS, self._auto_battery)

    # -- Hilfen ------------------------------------------------------------

    def _pump(self) -> None:
        """Rueckrufe des Workers im Hauptthread ausfuehren."""
        try:
            while True:
                call = self._ui_calls.get_nowait()
                try:
                    call()
                except Exception as exc:  # noqa: BLE001 - Pumpe muss weiterlaufen
                    self.statusbar.config(text=f"Anzeigefehler: {type(exc).__name__}: {exc}")
        except queue.Empty:
            pass
        self._check_instance_requests()
        self.after(PUMP_MS, self._pump)

    def _check_instance_requests(self) -> None:
        """Ein zweiter Start (z. B. Klick aufs Tray) holt dieses Fenster nach vorn."""
        if self.instance is None:
            return
        try:
            conn, _ = self.instance.accept()
        except (BlockingIOError, OSError):
            return
        conn.close()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def run_job(self, work: Callable[[AtkMouse], Any], done: Callable[[Any], None] | None = None) -> None:
        self._set_busy(True)

        def finish_ok(result: Any) -> None:
            self._set_busy(False)
            if done:
                done(result)

        def finish_fail(message: str) -> None:
            self._set_busy(False)
            self._show_error(message)

        def on_done(result: Any) -> None:  # laeuft im Worker-Thread
            self._ui_calls.put(lambda: finish_ok(result))

        def on_fail(message: str) -> None:  # laeuft im Worker-Thread
            self._ui_calls.put(lambda: finish_fail(message))

        self.worker.submit(work, on_done, on_fail)

    def _set_busy(self, busy: bool) -> None:
        self._busy += 1 if busy else -1
        self.statusbar.config(text="arbeite..." if self._busy > 0 else "bereit")

    def _show_error(self, message: str) -> None:
        self.statusbar.config(text=message)
        messagebox.showerror("ATK A9 Ultra", message, parent=self)

    # -- Aufbau ------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        self.lbl_model = ttk.Label(header, text="verbinde...", font=("", 12, "bold"))
        self.lbl_model.pack(side="left")
        ttk.Button(header, text="Aktualisieren", command=self.refresh).pack(side="right")
        ttk.Button(header, text="Laden...", command=self._import_settings).pack(side="right", padx=4)
        ttk.Button(header, text="Sichern...", command=self._export_settings).pack(side="right")

        batt = ttk.Frame(self)
        batt.pack(fill="x", **pad)
        ttk.Label(batt, text="Batterie:").pack(side="left")
        self.batt_bar = ttk.Progressbar(batt, maximum=100, length=200)
        self.batt_bar.pack(side="left", padx=8)
        self.lbl_batt = ttk.Label(batt, text="?")
        self.lbl_batt.pack(side="left")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)
        self.tab_dpi = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        self.tab_buttons = ttk.Frame(nb)
        nb.add(self.tab_dpi, text=" DPI ")
        nb.add(self.tab_settings, text=" Einstellungen ")
        self.tab_macros = ttk.Frame(nb)
        nb.add(self.tab_buttons, text=" Tasten ")
        nb.add(self.tab_macros, text=" Makros ")
        self.notebook = nb
        self.macros: dict[int, str] = {}   # Nummer -> Text, nur belegte
        self._macros_loaded = False
        nb.bind("<<NotebookTabChanged>>", lambda _e: self._on_tab_changed())

        self._build_dpi()
        self._build_settings()
        self._build_buttons()
        self._build_macros()

        self.statusbar = ttk.Label(self, text="bereit", relief="sunken", anchor="w")
        self.statusbar.pack(fill="x", side="bottom")

    def _export_settings(self) -> None:
        from tkinter import filedialog
        import json

        path = filedialog.asksaveasfilename(
            title="Einstellungen sichern", defaultextension=".json",
            initialfile="atk-a9-backup.json", filetypes=[("JSON", "*.json"), ("Alle", "*.*")],
        )
        if not path:
            return

        def work(mouse: AtkMouse) -> dict:
            return mouse.export_settings()

        def done(data: dict) -> None:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                self.statusbar.config(text=f"gesichert nach {path}")
            except OSError as exc:
                messagebox.showerror("Sichern fehlgeschlagen", str(exc))

        self.run_job(work, done)

    def _import_settings(self) -> None:
        from tkinter import filedialog
        import json

        path = filedialog.askopenfilename(
            title="Einstellungen laden", filetypes=[("JSON", "*.json"), ("Alle", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Laden fehlgeschlagen", str(exc))
            return
        if not messagebox.askyesno(
            "Einstellungen laden",
            "Alle Einstellungen aus der Datei auf die Maus schreiben?\n"
            "(DPI, Tasten, Makros, Sensor usw. werden ueberschrieben.)",
        ):
            return

        def work(mouse: AtkMouse) -> list:
            return mouse.import_settings(data, dry_run=False)

        def done(changes: list) -> None:
            skipped = [c for c in changes if c.startswith("uebersprungen")]
            self.statusbar.config(
                text=f"geladen: {len(changes) - len(skipped)} uebernommen, {len(skipped)} uebersprungen"
            )
            if skipped:
                messagebox.showwarning("Teilweise uebersprungen", "\n".join(skipped))
            self.refresh()

        self.run_job(work, done)

    def _build_dpi(self) -> None:
        frame = self.tab_dpi
        self.dpi_rows: list[dict[str, Any]] = []
        self.active_stage_var = tk.IntVar(value=1)
        for i in range(8):
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=8, pady=2)
            radio = ttk.Radiobutton(
                row, text=f"Stufe {i + 1}", value=i + 1, variable=self.active_stage_var,
                command=lambda stage=i + 1: self._select_stage(stage),
            )
            radio.pack(side="left")
            var = tk.StringVar()
            entry = ttk.Spinbox(row, from_=10, to=42000, increment=50, textvariable=var, width=8)
            entry.pack(side="left", padx=8)
            btn = ttk.Button(row, text="Setzen", command=lambda stage=i + 1: self._set_dpi(stage))
            btn.pack(side="left")
            color = tk.Label(row, width=3, relief="groove")
            color.pack(side="left", padx=8)
            self.dpi_rows.append(
                {"frame": row, "radio": radio, "var": var, "entry": entry, "btn": btn, "color": color}
            )

        foot = ttk.Frame(frame)
        foot.pack(fill="x", padx=8, pady=8)
        ttk.Label(foot, text="Anzahl Stufen:").pack(side="left")
        self.stage_count_var = tk.IntVar(value=4)
        ttk.Spinbox(foot, from_=1, to=8, textvariable=self.stage_count_var, width=4).pack(side="left", padx=4)
        ttk.Button(foot, text="Uebernehmen", command=self._set_stage_count).pack(side="left", padx=4)
        self.lbl_dpi_range = ttk.Label(foot, text="")
        self.lbl_dpi_range.pack(side="right")

    def _build_settings(self) -> None:
        frame = self.tab_settings
        grid = ttk.Frame(frame)
        grid.pack(fill="x", padx=12, pady=8)
        grid.columnconfigure(1, weight=1)
        r = 0

        def label(text: str) -> None:
            ttk.Label(grid, text=text).grid(row=r, column=0, sticky="w", pady=4)

        label("Polling-Rate (Hz)")
        self.polling_var = tk.StringVar()
        self.polling_box = ttk.Combobox(grid, textvariable=self.polling_var, state="readonly", width=10)
        self.polling_box.grid(row=r, column=1, sticky="w", padx=8)
        self.polling_box.bind("<<ComboboxSelected>>", lambda _e: self._set_polling())
        r += 1

        label("Lift-off (mm)")
        self.lod_var = tk.StringVar()
        self.lod_box = ttk.Combobox(grid, textvariable=self.lod_var, state="readonly", width=10)
        self.lod_box.grid(row=r, column=1, sticky="w", padx=8)
        self.lod_box.bind("<<ComboboxSelected>>", lambda _e: self._set_lod())
        r += 1

        label("Debounce (ms)")
        self.debounce_var = tk.StringVar()
        box = ttk.Combobox(grid, textvariable=self.debounce_var, state="readonly", width=10,
                           values=[str(v) for v in p.DEBOUNCE_MS])
        box.grid(row=r, column=1, sticky="w", padx=8)
        box.bind("<<ComboboxSelected>>", lambda _e: self._set_advanced(debounce_ms=int(self.debounce_var.get())))
        r += 1

        label("Schlafzeit (s)")
        self.sleep_var = tk.StringVar()
        box = ttk.Combobox(grid, textvariable=self.sleep_var, state="readonly", width=10,
                           values=[str(v) for v in p.SLEEP_SECONDS])
        box.grid(row=r, column=1, sticky="w", padx=8)
        box.bind("<<ComboboxSelected>>", lambda _e: self._set_advanced(sleep_seconds=int(self.sleep_var.get())))
        r += 1

        self.motion_var = tk.BooleanVar()
        ttk.Checkbutton(grid, text="Motion Sync", variable=self.motion_var,
                        command=lambda: self._set_advanced(motion_sync=self.motion_var.get())).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1
        self.linear_var = tk.BooleanVar()
        ttk.Checkbutton(grid, text="Geradenkorrektur", variable=self.linear_var,
                        command=lambda: self._set_advanced(straight_line=self.linear_var.get())).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1
        self.ripple_var = tk.BooleanVar()
        ttk.Checkbutton(grid, text="Ripple-Korrektur", variable=self.ripple_var,
                        command=lambda: self._set_advanced(ripple=self.ripple_var.get())).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1

        label("Sensormodus")
        self.sensor_var = tk.StringVar()
        box = ttk.Combobox(grid, textvariable=self.sensor_var, state="readonly", width=12,
                           values=["basic", "competitive", "max"])
        box.grid(row=r, column=1, sticky="w", padx=8)
        box.bind("<<ComboboxSelected>>", lambda _e: self._set_sensor_mode())
        r += 1

        label("Sensorwinkel (Grad)")
        angle_row = ttk.Frame(grid)
        angle_row.grid(row=r, column=1, sticky="w", padx=8)
        self.angle_var = tk.StringVar(value="0")
        self.angle_box = ttk.Spinbox(angle_row, from_=-30, to=30, textvariable=self.angle_var, width=5)
        self.angle_box.pack(side="left")
        self.angle_btn = ttk.Button(angle_row, text="Setzen", command=self._set_angle)
        self.angle_btn.pack(side="left", padx=6)
        r += 1
        self.snap_var = tk.BooleanVar()
        self.snap_check = ttk.Checkbutton(grid, text="Angle-Snapping", variable=self.snap_var,
                                          command=lambda: self._set_snap())
        self.snap_check.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1

        self.longrange_var = tk.BooleanVar()
        self.longrange_check = ttk.Checkbutton(
            grid, text="Reichweiten-Modus (mehr Reichweite, mehr Stromverbrauch)",
            variable=self.longrange_var, command=lambda: self._set_long_range())
        self.longrange_check.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1

        label("Mausrad-Schutz (ms)")
        self.wheel_guard_var = tk.StringVar(value="aus")
        self.wheel_guard_box = ttk.Combobox(grid, textvariable=self.wheel_guard_var, state="readonly", width=10,
                                            values=WHEEL_GUARD_CHOICES)
        self.wheel_guard_box.grid(row=r, column=1, sticky="w", padx=8)
        self.wheel_guard_box.bind("<<ComboboxSelected>>", lambda _e: self._set_wheel_guard())
        r += 1
        ttk.Label(grid, foreground="gray40",
                  text="Ignoriert den ersten Rad-Schritt; gescrollt wird erst, wenn in der\n"
                       "eingestellten Zeit ein zweiter folgt.").grid(row=r, column=0, columnspan=2, sticky="w")

    def _build_buttons(self) -> None:
        frame = self.tab_buttons
        ttk.Label(
            frame,
            text="Aktuelle Belegung. 'Aendern...' bietet Maustasten, DPI, Mausrad, Schnellfeuer,\n"
                 "Tastenkombinationen und Makros.",
        ).pack(anchor="w", padx=8, pady=6)
        self.button_rows: list[dict[str, Any]] = []
        for i in range(8):
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=8, pady=2)
            ttk.Label(row, text=f"Taste {i + 1}", width=8).pack(side="left")
            current = ttk.Label(row, text="?", width=44, anchor="w")
            current.pack(side="left")
            btn = ttk.Button(row, text="Aendern...", command=lambda slot=i + 1: self._edit_button(slot))
            btn.pack(side="left", padx=6)
            self.button_rows.append({"frame": row, "current": current, "btn": btn})

    def _build_macros(self) -> None:
        frame = self.tab_macros
        top = ttk.Frame(frame)
        top.pack(fill="both", expand=True, padx=8, pady=6)
        self.macro_list = tk.Listbox(top, height=12, exportselection=False)
        self.macro_list.pack(side="left", fill="both", expand=True)
        self.macro_list.bind("<<ListboxSelect>>", lambda _e: self._macro_selected())
        side = ttk.Frame(top)
        side.pack(side="left", fill="y", padx=6)
        ttk.Button(side, text="Neu laden", command=self._load_macros).pack(fill="x", pady=2)
        ttk.Button(side, text="Leeren", command=self._clear_macro).pack(fill="x", pady=2)

        edit = ttk.Frame(frame)
        edit.pack(fill="x", padx=8)
        self.macro_title = ttk.Label(edit, text="Makro 1")
        self.macro_title.pack(anchor="w")
        self.macro_text = tk.Text(edit, height=4, wrap="word")
        self.macro_text.pack(fill="x", pady=2)
        ttk.Button(edit, text="Speichern", command=self._save_macro).pack(anchor="e", pady=2)
        ttk.Label(frame, text=MACRO_HELP, foreground="gray40", justify="left").pack(anchor="w", padx=8, pady=4)
        self._fill_macro_list()

    # -- Daten laden --------------------------------------------------------

    def refresh(self) -> None:
        self.run_job(lambda m: m.status(), self._apply_status)

    def _auto_battery(self) -> None:
        def work(m: AtkMouse) -> p.Battery:
            response = m.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
            return p.parse_battery(response.data, response.declared)

        def done(batt: Any) -> None:
            self._apply_battery(batt.percent, batt.charging)

        self.worker.submit(work, lambda b: self._ui_calls.put(lambda: done(b)), lambda _msg: None)
        self.after(POLL_BATTERY_MS, self._auto_battery)

    def _apply_battery(self, percent: int | None, charging: bool | None) -> None:
        self.batt_bar["value"] = percent or 0
        text = f"{percent}%" if percent is not None else "?"
        if charging:
            text += " (laedt)"
        self.lbl_batt.config(text=text)

    def _apply_status(self, status: MouseStatus) -> None:
        self.status = status
        self.lbl_model.config(
            text=f"{status.model_name}  -  {status.sensor or '?'}  -  Firmware {status.firmware or '?'}"
        )
        if status.battery:
            self._apply_battery(status.battery.percent, status.battery.charging)

        # DPI
        stages = {s.index: s for s in status.dpi_stages}
        count = status.system.stage_count if status.system and status.system.stage_count else len(stages)
        self.stage_count_var.set(count or 4)
        if status.system and status.system.active_stage:
            self.active_stage_var.set(status.system.active_stage)
        for i, row in enumerate(self.dpi_rows, start=1):
            stage = stages.get(i)
            visible = count is None or i <= (count or 8)
            state = "normal" if (stage and visible) else "disabled"
            row["radio"].config(state=state)
            row["btn"].config(state=state)
            row["entry"].config(from_=status.dpi_min, to=status.dpi_max)
            if stage and stage.x is not None:
                row["var"].set(str(stage.x))
            elif not stage:
                row["var"].set("")
            if stage and stage.color:
                r, g, b = stage.color
                row["color"].config(bg=f"#{r:02x}{g:02x}{b:02x}")
            else:
                row["color"].config(bg=self.cget("bg"))
        self.lbl_dpi_range.config(text=f"Bereich: {status.dpi_min}-{status.dpi_max} DPI")

        # Einstellungen
        if status.system:
            self.polling_box["values"] = [str(hz) for hz in status.polling_options]
            if status.system.polling_hz:
                self.polling_var.set(str(status.system.polling_hz))
        self.lod_box["values"] = [str(v) for v in status.lod_options]
        if status.lod_mm is not None:
            self.lod_var.set(str(status.lod_mm))
        if status.advanced:
            a = status.advanced
            if a.debounce_ms is not None:
                self.debounce_var.set(str(a.debounce_ms))
            if a.sleep_seconds is not None:
                self.sleep_var.set(str(a.sleep_seconds))
            self.motion_var.set(bool(a.motion_sync))
            self.linear_var.set(bool(a.straight_line))
            self.ripple_var.set(bool(a.ripple))
        if status.sensor_row and status.sensor_row.performance_mode:
            self.sensor_var.set(status.sensor_row.performance_mode)
        # Funktionen, die dieses Modell nicht hat, werden ausgegraut
        has_angle = status.angle is not None
        for widget in (self.angle_box, self.angle_btn, self.snap_check):
            widget.config(state="normal" if has_angle else "disabled")
        if status.angle is not None:
            self.angle_var.set(str(int(status.angle[0])))
            self.snap_var.set(bool(status.angle[1]))
        self.longrange_check.config(state="normal" if status.long_range is not None else "disabled")
        self.longrange_var.set(bool(status.long_range))
        self.wheel_guard_box.config(state="readonly" if status.wheel_guard_ms is not None else "disabled")
        if status.wheel_guard_ms is not None:
            self.wheel_guard_var.set(str(status.wheel_guard_ms) if status.wheel_guard_ms else "aus")

        # Tasten
        for i, row in enumerate(self.button_rows, start=1):
            assignment = next((b for b in status.buttons if b.slot == i), None)
            row["current"].config(text=assignment.label if assignment else "-")
            row["btn"].config(state="normal" if assignment else "disabled")
        self.statusbar.config(text="Status aktualisiert")

    # -- Aktionen -----------------------------------------------------------

    def _set_dpi(self, stage: int) -> None:
        try:
            dpi = int(self.dpi_rows[stage - 1]["var"].get())
        except ValueError:
            return self._show_error("Bitte eine Zahl eingeben.")
        self.run_job(lambda m: m.set_dpi_stage(stage, dpi), lambda _s: self.refresh())

    def _select_stage(self, stage: int) -> None:
        self.run_job(lambda m: m.select_stage(stage), lambda _s: self.statusbar.config(
            text=f"aktive Stufe: {stage}"))

    def _set_stage_count(self) -> None:
        count = self.stage_count_var.get()
        self.run_job(lambda m: m.set_stage_count(count), lambda _s: self.refresh())

    def _set_polling(self) -> None:
        hz = int(self.polling_var.get())
        self.run_job(lambda m: m.set_polling(hz), lambda _s: self.statusbar.config(
            text=f"Polling-Rate: {hz} Hz"))

    def _set_lod(self) -> None:
        value = self.lod_var.get()
        self.run_job(lambda m: m.set_lod(value), lambda _s: self.statusbar.config(
            text=f"Lift-off: {value} mm"))

    def _set_advanced(self, **kwargs: Any) -> None:
        self.run_job(lambda m: m.set_advanced(**kwargs), lambda _a: self.statusbar.config(
            text="Einstellung gespeichert"))

    def _set_sensor_mode(self) -> None:
        mode = self.sensor_var.get()
        self.run_job(lambda m: m.set_sensor_mode(mode), lambda _s: self.statusbar.config(
            text=f"Sensormodus: {mode}"))

    def _set_angle(self) -> None:
        try:
            angle = int(self.angle_var.get())
        except ValueError:
            return self._show_error("Sensorwinkel: bitte eine ganze Zahl eingeben.")
        self.run_job(lambda m: m.set_angle(angle=angle), lambda r: self.statusbar.config(
            text=f"Sensorwinkel: {int(r[0])} Grad"))

    def _set_snap(self) -> None:
        snap = self.snap_var.get()
        self.run_job(lambda m: m.set_angle(snap=snap), lambda _r: self.statusbar.config(
            text="Angle-Snapping " + ("an" if snap else "aus")))

    def _set_long_range(self) -> None:
        enabled = self.longrange_var.get()
        self.run_job(lambda m: m.set_far_distance(enabled), lambda _r: self.statusbar.config(
            text="Reichweiten-Modus " + ("an" if enabled else "aus")))

    def _set_wheel_guard(self) -> None:
        text = self.wheel_guard_var.get()
        ms = 0 if text == "aus" else int(text)
        self.run_job(lambda m: m.set_wheel_guard(ms), lambda _r: self.statusbar.config(
            text="Mausrad-Schutz " + (f"{ms} ms" if ms else "aus")))

    # -- Tasten ---------------------------------------------------------------

    def _edit_button(self, slot: int) -> None:
        if not self._macros_loaded:
            # die Makro-Liste wird fuer die Auswahl gebraucht
            return self._load_macros(then=lambda: self._edit_button(slot))
        current = self.button_rows[slot - 1]["current"].cget("text")
        dialog = ButtonDialog(self, slot, current, self.macros)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        if slot == 1 and not messagebox.askyesno(
            "Linke Maustaste",
            "Taste 1 ist die linke Maustaste. Wirklich neu belegen?\n"
            "Ohne Linksklick laesst sich die Maus nur noch schwer bedienen.",
            parent=self,
        ):
            return
        self.run_job(dialog.result, lambda _b: self.refresh())

    # -- Makros ---------------------------------------------------------------

    def _on_tab_changed(self) -> None:
        if self.notebook.select() == str(self.tab_macros) and not self._macros_loaded:
            self._load_macros()

    def _load_macros(self, then: Callable[[], None] | None = None) -> None:
        def work(m: AtkMouse) -> dict[int, str]:
            found: dict[int, str] = {}
            for number in range(1, k.MACRO_SLOTS + 1):
                events = m.read_macro(number)
                if events:
                    found[number] = k.format_macro(events)
            return found

        def done(found: dict[int, str]) -> None:
            self.macros = found
            self._macros_loaded = True
            self._fill_macro_list()
            self.statusbar.config(text=f"{len(found)} Makro(s) gelesen")
            if then:
                then()

        self.run_job(work, done)

    def _fill_macro_list(self) -> None:
        selected = self._selected_macro()
        self.macro_list.delete(0, "end")
        for number in range(1, k.MACRO_SLOTS + 1):
            self.macro_list.insert("end", f"Makro {number:>2}:  {self.macros.get(number, '(leer)')}")
        self.macro_list.selection_set(selected - 1)
        self._macro_selected()

    def _selected_macro(self) -> int:
        selection = self.macro_list.curselection()
        return selection[0] + 1 if selection else 1

    def _macro_selected(self) -> None:
        number = self._selected_macro()
        self.macro_title.config(text=f"Makro {number}")
        self.macro_text.delete("1.0", "end")
        self.macro_text.insert("1.0", self.macros.get(number, ""))

    def _save_macro(self) -> None:
        number = self._selected_macro()
        try:
            events = k.parse_macro(self.macro_text.get("1.0", "end"))
        except ValueError as exc:
            return self._show_error(str(exc))
        if not events:
            return self._show_error("Das Makro ist leer. Zum Entfernen 'Leeren' verwenden.")

        def done(saved: list[k.KeyEvent]) -> None:
            self.macros[number] = k.format_macro(saved)
            self._fill_macro_list()
            self.statusbar.config(text=f"Makro {number} gespeichert ({len(saved)} Ereignisse)")

        self.run_job(lambda m: m.write_macro(number, events), done)

    def _clear_macro(self) -> None:
        number = self._selected_macro()
        if number not in self.macros:
            return
        if not messagebox.askyesno("Makro leeren", f"Makro {number} wirklich leeren?", parent=self):
            return

        def done(_result: Any) -> None:
            self.macros.pop(number, None)
            self._fill_macro_list()
            self.statusbar.config(text=f"Makro {number} geleert")

        self.run_job(lambda m: m.clear_macro(number), done)


def main() -> int:
    instance = acquire_instance()
    if instance is None:
        return 0  # laufende Instanz wurde nach vorn geholt
    app = App(instance)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

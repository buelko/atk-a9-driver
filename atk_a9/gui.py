"""atk-a9-gui - grafische Oberflaeche fuer die ATK A9 Ultra (Tkinter).

Alle Geraetezugriffe laufen in einem Hintergrund-Thread, damit die
Oberflaeche nicht blockiert (die Maus kann schlafen -> Timeouts).
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from . import protocol as p
from .device import AtkMouse, MouseStatus
from .device import WriteVerificationError
from .hidraw import DeviceBusy, DeviceNotFound

POLL_BATTERY_MS = 60_000

BUTTON_PRESETS: list[tuple[str, tuple[int, int, int] | None]] = [
    ("(unveraendert)", None),
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
    ("Polling durchschalten", (0x07, 0x01, 0x00)),
    ("deaktiviert", (0x00, 0x00, 0x00)),
]


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
        except DeviceBusy as exc:
            fail(str(exc))
        except TimeoutError:
            # Maus schlaeft: Verbindung ist in Ordnung und bleibt offen
            fail("Keine Antwort - Maus bewegen (Schlafmodus) und erneut versuchen.")
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
    def __init__(self) -> None:
        super().__init__()
        self.title("ATK A9 Ultra")
        self.minsize(560, 560)
        self.worker = Worker()
        self.worker.start()
        self.status: MouseStatus | None = None
        self._busy = 0

        self._build()
        self.after(200, self.refresh)
        self.after(POLL_BATTERY_MS, self._auto_battery)

    # -- Hilfen ------------------------------------------------------------

    def run_job(self, work: Callable[[AtkMouse], Any], done: Callable[[Any], None] | None = None) -> None:
        self._set_busy(True)

        def on_done(result: Any) -> None:
            self.after(0, lambda: (self._set_busy(False), done(result) if done else None))

        def on_fail(message: str) -> None:
            self.after(0, lambda: (self._set_busy(False), self._show_error(message)))

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
        nb.add(self.tab_buttons, text=" Tasten ")

        self._build_dpi()
        self._build_settings()
        self._build_buttons()

        self.statusbar = ttk.Label(self, text="bereit", relief="sunken", anchor="w")
        self.statusbar.pack(fill="x", side="bottom")

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
            self.dpi_rows.append({"frame": row, "radio": radio, "var": var, "btn": btn, "color": color})

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
                           values=["basic", "competitive"])
        box.grid(row=r, column=1, sticky="w", padx=8)
        box.bind("<<ComboboxSelected>>", lambda _e: self._set_sensor_mode())

    def _build_buttons(self) -> None:
        frame = self.tab_buttons
        ttk.Label(
            frame,
            text="Aktuelle Belegung; Preset waehlen und 'Setzen' klicken.",
        ).pack(anchor="w", padx=8, pady=6)
        self.button_rows: list[dict[str, Any]] = []
        names = list(BUTTON_PRESETS)
        for i in range(8):
            row = ttk.Frame(frame)
            row.pack(fill="x", padx=8, pady=2)
            ttk.Label(row, text=f"Taste {i + 1}", width=8).pack(side="left")
            current = ttk.Label(row, text="?", width=28, anchor="w")
            current.pack(side="left")
            var = tk.StringVar(value=names[0][0])
            box = ttk.Combobox(row, textvariable=var, state="readonly", width=20,
                               values=[n for n, _ in names])
            box.pack(side="left", padx=6)
            ttk.Button(row, text="Setzen", command=lambda slot=i + 1: self._set_button(slot)).pack(side="left")
            self.button_rows.append({"frame": row, "current": current, "var": var})

    # -- Daten laden --------------------------------------------------------

    def refresh(self) -> None:
        self.run_job(lambda m: m.status(), self._apply_status)

    def _auto_battery(self) -> None:
        def work(m: AtkMouse) -> p.Battery:
            response = m.command(p.Cmd.GET_BATTERY, expect=p.Cmd.GET_BATTERY)
            return p.parse_battery(response.data, response.declared)

        def done(batt: Any) -> None:
            self._apply_battery(batt.percent, batt.charging)

        self.worker.submit(work, lambda b: self.after(0, lambda: done(b)), lambda _msg: None)
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
            opts = [str(hz) for hz in (status.max_polling_hz and self._polling_options() or [])]
            self.polling_box["values"] = opts
            if status.system.polling_hz:
                self.polling_var.set(str(status.system.polling_hz))
        lod_values = self._lod_options()
        self.lod_box["values"] = [str(v) for v in lod_values]
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

        # Tasten
        for i, row in enumerate(self.button_rows, start=1):
            assignment = next((b for b in status.buttons if b.slot == i), None)
            if assignment:
                row["current"].config(text=assignment.label)
                row["frame"].pack(fill="x", padx=8, pady=2)
            else:
                row["current"].config(text="-")
        self.statusbar.config(text="Status aktualisiert")

    def _polling_options(self) -> list[int]:
        limit = self.status.max_polling_hz if self.status and self.status.max_polling_hz else 1000
        return [hz for hz in (125, 250, 500, 1000, 2000, 4000, 8000) if hz <= limit]

    def _lod_options(self) -> list[float]:
        return sorted({round((code + 6) / 10, 1) for code in range(1, 12)})

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

    def _set_button(self, slot: int) -> None:
        name = self.button_rows[slot - 1]["var"].get()
        preset = dict(BUTTON_PRESETS).get(name)
        if preset is None:
            return self._show_error("Bitte zuerst ein Preset waehlen.")
        cls, v1, v2 = preset
        if not messagebox.askyesno(
            "Taste aendern",
            f"Taste {slot} wirklich auf '{name}' setzen?",
            parent=self,
        ):
            return
        self.run_job(lambda m: m.set_button(slot, cls, v1, v2), lambda _b: self.refresh())


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

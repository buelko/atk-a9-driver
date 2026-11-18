# atk-a9-driver

Linux-Userspace-Treiber für ATK-/VXE-Mäuse, entwickelt und getestet mit der
**ATK A9 Ultra** am 1K-Dongle (`373b:101a`). Reines Python, Zugriff über
`/dev/hidraw*` – kein Kernelmodul.

- `atk-a9` – Kommandozeile (Status, DPI, Polling, Lift-off, Tasten, …)
- `atk-a9-gui` – Einstellungsfenster (Tkinter)
- `atk-a9-tray` – Akku-Symbol für die Leiste (GTK 3), warnt bei ≤ 15 %

## Voraussetzungen

| Teil | braucht |
|---|---|
| Treiber, CLI | Python ≥ 3.10, nur Standardbibliothek |
| GUI | Tkinter (`python3-tk`) |
| Tray | PyGObject mit GTK 3 und cairo (`python3-gi`, `gir1.2-gtk-3.0`, `python3-gi-cairo`); für die Akku-Warnung `gir1.2-notify-0.7` oder `notify-send` |

## Installation

```sh
sudo ./install.sh            # udev-Regel, Programme, Menü-Eintrag, Tray-Autostart
sudo ./install.sh --no-tray  # ohne Tray-Autostart
```

Das Skript verlinkt die Programme nach `/usr/local/bin`; sie laufen direkt aus
diesem Verzeichnis, es darf danach also nicht verschoben werden. Die udev-Regel
gibt dem angemeldeten Benutzer (und der Gruppe `plugdev`) Zugriff auf die
hidraw-Knoten der Hersteller-IDs `373b` und `3554`.

Alternativ als Python-Paket (`pyproject.toml` ist vorhanden); die udev-Regel
aus `udev/` muss dann von Hand nach `/etc/udev/rules.d/` kopiert werden.

## Benutzung

```sh
atk-a9 scan                      # Geräte auflisten
atk-a9 status                    # kompletter Status (auch --json)
atk-a9 battery
atk-a9 dpi set 2 1600            # Stufe 2 auf 1600 DPI
atk-a9 dpi select 3              # aktive Stufe wählen
atk-a9 polling set 500
atk-a9 lod set 1.0               # Lift-off in mm (oder code:N)
atk-a9 buttons set 5 mouse back
atk-a9 advanced set --debounce 4 --sleep 300
atk-a9 probe                     # Diagnose
atk-a9 -v status                 # mit allen gesendeten/empfangenen Frames
```

`atk-a9 <befehl> --help` zeigt die Optionen jedes Befehls.

## Verhalten, das man kennen sollte

- **Schlafende Maus:** Die Maus beantwortet Anfragen nur, wenn sie wach ist.
  Der Treiber fragt bei einem Timeout den Dongle nach dem Online-Status und
  meldet dann „Maus ist nicht verbunden“ – Maus bewegen und erneut versuchen.
  Das Tray zeigt in der Zeit den letzten Akkustand ausgegraut.
- **Mehrere Programme gleichzeitig:** CLI, GUI und Tray dürfen parallel laufen.
  Jeder Zugriff hält kurz eine Sperre (`flock`) auf dem hidraw-Knoten. Der
  Hersteller-Treiber „ATK HUB“ (z. B. in einer VM) kennt diese Sperre nicht und
  sollte nicht gleichzeitig laufen.
- **Schreiben:** Jeder Schreibvorgang wird zurückgelesen und verglichen. Steht
  der gewünschte Wert schon im Gerät, wird gar nicht geschrieben.
- **Nur eine GUI:** Ein zweiter Start holt das laufende Fenster nach vorn.

## Aufbau

| Datei | Inhalt |
|---|---|
| `atk_a9/protocol.py` | Frames, Prüfsummen, Kodierungen (siehe `docs/PROTOCOL.md`) |
| `atk_a9/hidraw.py` | Gerätesuche, hidraw-Transport, Sperre |
| `atk_a9/device.py` | `AtkMouse`: alle Lese- und Schreibfunktionen |
| `atk_a9/catalog.py` | Modell-, Sensor- und Empfängerliste |
| `atk_a9/cli.py`, `gui.py`, `tray.py` | Oberflächen |
| `atk_a9/fake.py` | simuliertes Gerät für Tests |

## Tests

```sh
python3 -m unittest discover -s tests
```

Die Tests laufen gegen das simulierte Gerät und verändern die echte Maus nicht.

## Bekannte Grenzen

- Am echten Gerät geprüft ist nur die ATK A9 Ultra am 1K-Dongle. Die übrigen
  Modelle im Katalog stammen aus den Referenzprojekten und sind ungetestet.
- Makros, Tastenkürzel-Slots und Beleuchtung lassen sich nicht bearbeiten.
- Profile umschalten funktioniert an der A9 Ultra nicht: das Gerät lehnt das
  Kommando ab (siehe `docs/PROTOCOL.md`). Das aktive Profil lässt sich lesen.
- Das Tray nutzt `Gtk.StatusIcon` (X11, z. B. XFCE); unter Wayland erscheint
  kein Symbol.

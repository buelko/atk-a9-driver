# Installation

Linux-Treiber für ATK/VXE-Mäuse. Kurzanleitung.

## 1. Abhängigkeiten

```sh
# Debian / Ubuntu
sudo apt install python3 python3-tk \
     python3-gi gir1.2-gtk-3.0 python3-gi-cairo \
     gir1.2-ayatanaappindicator3-0.1   # nur für Wayland-Tray
```

| Teil | braucht |
|---|---|
| CLI (`atk-a9`) | Python ≥ 3.10 (Standardbibliothek, sonst nichts) |
| GUI (`atk-a9-gui`) | `python3-tk` |
| Tray (`atk-a9-tray`) | GTK 3 + cairo; Wayland zusätzlich AppIndicator |

## 2. Installieren

```sh
git clone https://github.com/buelko/atk-a9-driver.git
cd atk-a9-driver
sudo ./install.sh              # udev-Regel, Programme, Menü, Tray-Autostart, Completion
```

Ohne Tray-Autostart: `sudo ./install.sh --no-tray`

Dongle danach einmal neu einstecken. Die Programme werden nach
`/usr/local/bin` verlinkt – den Projektordner danach nicht verschieben.

## 3. Prüfen

```sh
atk-a9 scan          # Gerät gefunden?
atk-a9 status        # Akku, DPI, Tasten
```

## Schnellstart

```sh
atk-a9 dpi set 2 1600 --select   # Stufe 2 auf 1600 DPI, aktiv schalten
atk-a9 dpi color 2 00FF00        # Farbe der Stufe 2
atk-a9 battery --watch 60        # Akku alle 60 s
atk-a9 export profil.json        # alle Einstellungen sichern
atk-a9 import profil.json        # zurückschreiben
atk-a9-gui                       # grafische Oberfläche
```

## Deinstallieren

```sh
sudo rm /usr/local/bin/atk-a9 /usr/local/bin/atk-a9-gui /usr/local/bin/atk-a9-tray
sudo rm /etc/udev/rules.d/70-atk-mouse.rules /etc/bash_completion.d/atk-a9
sudo rm /usr/local/share/applications/atk-a9.desktop /etc/xdg/autostart/atk-a9-tray.desktop
sudo udevadm control --reload
```

## Problemlösung

- **Kein Gerät gefunden** – Dongle neu einstecken; prüfen mit `atk-a9 scan`.
- **Keine Berechtigung** – ein-/ausloggen (udev-Gruppe `plugdev` greift erst dann).
- **Maus schläft** – Maus bewegen/klicken, dann erneut versuchen.
- **Tray unsichtbar unter Wayland** – `gir1.2-ayatanaappindicator3-0.1` installieren.

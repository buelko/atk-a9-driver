#!/bin/sh
# Installiert udev-Regel, Programme (CLI, GUI, Tray) und Desktop-Eintraege.
#
#   sudo ./install.sh             alles installieren
#   sudo ./install.sh --no-tray   ohne Tray-Autostart
#
# Die Programme werden nach /usr/local/bin verlinkt und laufen direkt aus
# diesem Verzeichnis - es darf danach nicht verschoben werden.
# DESTDIR=/pfad ./install.sh installiert in einen Zielbaum (ohne root, ohne
# udev-Neuladen), z. B. zum Testen oder Paketieren.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
DESTDIR="${DESTDIR:-}"
TRAY=1
for arg in "$@"; do
    case "$arg" in
        --no-tray) TRAY=0 ;;
        *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    esac
done

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren: sudo $0" >&2
    exit 1
fi

RULES="$DESTDIR/etc/udev/rules.d"
BIN="$DESTDIR/usr/local/bin"
APPS="$DESTDIR/usr/local/share/applications"
AUTOSTART="$DESTDIR/etc/xdg/autostart"
COMPLETION="$DESTDIR/etc/bash_completion.d"

install -d "$RULES" "$BIN" "$APPS"
rm -f "$RULES/99-atk-mouse.rules"
install -m 0644 "$HERE/udev/70-atk-mouse.rules" "$RULES/70-atk-mouse.rules"
if [ -z "$DESTDIR" ]; then
    udevadm control --reload
    udevadm trigger --subsystem-match=hidraw
fi
echo "udev-Regel installiert. Dongle einmal neu einstecken (oder es wirkt bereits)."

for tool in atk-a9 atk-a9-gui atk-a9-tray; do
    ln -sf "$HERE/bin/$tool" "$BIN/$tool"
    echo "verlinkt: /usr/local/bin/$tool"
done

# Desktop-Eintraege (Exec zeigt auf die Links in /usr/local/bin).
# Gleichnamige Dateien unter ~/.local/share/applications bzw. ~/.config/autostart
# haben Vorrang - es entstehen keine doppelten Eintraege.
install -m 0644 "$HERE/desktop/atk-a9.desktop" "$APPS/atk-a9.desktop"
echo "Menue-Eintrag: /usr/local/share/applications/atk-a9.desktop"
if [ "$TRAY" -eq 1 ]; then
    install -d "$AUTOSTART"
    install -m 0644 "$HERE/desktop/atk-a9-tray.desktop" "$AUTOSTART/atk-a9-tray.desktop"
    echo "Tray-Autostart: /etc/xdg/autostart/atk-a9-tray.desktop"
fi

# bash-Completion (optional, schadet nicht, wenn bash-completion fehlt)
if [ -f "$HERE/completion/atk-a9.bash" ]; then
    install -d "$COMPLETION"
    install -m 0644 "$HERE/completion/atk-a9.bash" "$COMPLETION/atk-a9"
    echo "bash-Completion: /etc/bash_completion.d/atk-a9 (neue Shell oeffnen)"
fi

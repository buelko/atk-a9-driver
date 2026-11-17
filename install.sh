#!/bin/sh
# Installiert die udev-Regel fuer ATK/VXE-Maeuse und optional das CLI.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren: sudo $0" >&2
    exit 1
fi

install -m 0644 "$HERE/udev/99-atk-mouse.rules" /etc/udev/rules.d/99-atk-mouse.rules
udevadm control --reload
udevadm trigger --subsystem-match=hidraw
echo "udev-Regel installiert. Dongle einmal neu einstecken (oder es wirkt bereits)."

if [ -d /usr/local/bin ]; then
    ln -sf "$HERE/bin/atk-a9" /usr/local/bin/atk-a9
    echo "CLI verlinkt: /usr/local/bin/atk-a9"
fi

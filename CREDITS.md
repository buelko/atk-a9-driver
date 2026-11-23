# Credits

Dieser Treiber ist eine eigenständige Python-Implementierung. Das
Protokollwissen (EEPROM-Adressen, Frame-Aufbau, Byte-Bedeutungen,
Geräte-Katalog) stammt aus mehreren offenen Reverse-Engineering-Projekten
und wurde durch Auslesen echter Hardware verifiziert.

Es wurde **kein Quellcode** aus diesen Projekten kopiert – sie dienten als
Referenz für die Bedeutung der Protokollfelder. Dank an die Autoren für ihre
Reverse-Engineering-Arbeit.

## Quellen

| Projekt | Sprache | Lizenz | Verwendet für |
|---|---|---|---|
| [OpenMouse-Project/mouse-protocol](https://github.com/OpenMouse-Project/mouse-protocol) | TypeScript | AGPL-3.0 | Protokoll-Doku (`atk-testing.md`, `hid.ts`), Geräte-Katalog (ATK HUB 3.2.x) |
| [VoideUI/libatk-rs](https://github.com/VoideUI/libatk-rs) (bzw. [cyberphantom52/libatk-rs](https://github.com/cyberphantom52/libatk-rs)) | Rust | GPL-3.0 | EEPROM-Adressen-Enum |
| [VoideUI/Linux-ATK](https://github.com/VoideUI/Linux-ATK) | Rust | GPL-3.0 | Protokoll-Abgleich, DPI-Kodierung |
| [xb-bx/atk-a9-ultra-driver](https://github.com/xb-bx/atk-a9-ultra-driver) | Odin | GPL-2.0 | ATK A9 Ultra: Abfrage-Frames |
| [szabatoro/atkctl](https://github.com/szabatoro/atkctl) | Python | BSD-3-Clause | ATK Dragonfly A9 Pro Max 2.0 |

## Lizenzwahl

Dieses Projekt steht unter der **GNU General Public License v3.0** (siehe
[LICENSE](LICENSE)). GPL-3.0 ist kompatibel mit allen oben genannten Quellen
(AGPL-3.0, GPL-3.0, GPL-2.0-or-later, BSD-3-Clause) und stellt sicher, dass die
abgeleitete Arbeit ebenfalls offen bleibt.

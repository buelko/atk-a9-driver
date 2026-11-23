# Credits

Dieser Treiber ist eine eigenständige Python-Implementierung. Das
Protokollwissen (EEPROM-Adressen, Frame-Aufbau, Byte-Bedeutungen,
Geräte-Katalog) stammt aus mehreren offenen Reverse-Engineering-Projekten
und wurde durch Auslesen echter Hardware verifiziert.

Der ausführbare Code wurde neu geschrieben; aus den Referenzprojekten
wurden keine Code-Zeilen übernommen. **Ausgenommen ist die Gerätetabelle**
in [`atk_a9/catalog.py`](atk_a9/catalog.py): Sie ist eine Übertragung der
Gerätedaten aus dem OpenMouse-Katalog (siehe Abschnitt „Abgeleitete Daten").
Dank an die Autoren für ihre Reverse-Engineering-Arbeit.

## Quellen

| Projekt | Sprache | Lizenz | Verwendet für |
|---|---|---|---|
| [OpenMouse-Project/mouse-protocol](https://github.com/OpenMouse-Project/mouse-protocol) | TypeScript | AGPL-3.0 | Protokoll-Doku (`docs/atk-testing.md`, `src/drivers/*/hid.ts`), **Geräte-Katalog** (ATK HUB 3.2.x) |
| [cyberphantom52/libatk-rs](https://github.com/cyberphantom52/libatk-rs) (Original) – Fork: [VoideUI/libatk-rs](https://github.com/VoideUI/libatk-rs) | Rust | GPL-3.0 | EEPROM-Adressen-Enum |
| [VoideUI/Linux-ATK](https://github.com/VoideUI/Linux-ATK) | Rust | GPL-3.0 | Protokoll-Abgleich, DPI-Kodierung |
| [szabatoro/atkctl](https://github.com/szabatoro/atkctl) | Python | BSD-3-Clause | ATK Dragonfly A9 Pro Max 2.0 |

### Nicht verwendet

[xb-bx/atk-a9-ultra-driver](https://github.com/xb-bx/atk-a9-ultra-driver)
(Odin, **GPL-2.0-only**) war in einer früheren Fassung dieser Datei als Quelle
für „ATK A9 Ultra: Abfrage-Frames" genannt. Diese Angabe war falsch – die
betreffenden Daten stammen aus dem OpenMouse-Katalog. Das Projekt wird hier
**nicht** als Quelle verwendet, da GPL-2.0-only (ohne „or later") weder mit
GPL-3.0 noch mit AGPL-3.0 kompatibel ist.

## Abgeleitete Daten: Gerätekatalog

`atk_a9/catalog.py` enthält 187 VID/PID-Einträge (Marke, Modell, Sensor,
CID/MID, Usage-Page, Feature-Flags). Diese Daten sind aus
`src/drivers/atk/device-catalog.generated.ts` des OpenMouse-Projekts
übernommen und lediglich ins Python-Format umgesetzt. Es handelt sich damit
um ein **abgeleitetes Werk** dieser Datei.

OpenMouse steht unter **AGPL-3.0**. Da diese Daten ein abgeleitetes Werk
sind, bestimmt ihre Lizenz die Lizenz des Gesamtprojekts mit – siehe unten.

## Lizenzwahl

Dieses Projekt steht unter der **GNU Affero General Public License v3.0 or
later** (siehe [LICENSE](LICENSE); SPDX: `AGPL-3.0-or-later`).

**Warum AGPL und nicht GPL?** Der Gerätekatalog ist aus AGPL-3.0-Material
abgeleitet. Die AGPL ist mit der GPL-3.0 *nicht* in dem Sinne kompatibel,
dass man AGPL-Code einfach unter GPL-3.0 weitergeben dürfte – AGPL §13
erlaubt nur das *Kombinieren* beider Lizenzen in einem Projekt, wobei der
AGPL-Teil AGPL bleibt. Statt einer Mischlizenz steht deshalb das gesamte
Projekt unter AGPL-3.0-or-later. Das ist die strengere und damit
zulässige Richtung.

In der Praxis ändert das für Nutzerinnen und Nutzer nichts: Bei einem
lokalen CLI-/Desktop-Werkzeug entspricht die AGPL der GPL-3.0. Die
Zusatzpflicht aus §13 (Quelltext für Nutzer, die über ein Netzwerk
interagieren) wird nur ausgelöst, wenn das Programm als Netzwerkdienst
betrieben wird.

Kompatibilität der verwendeten Quellen:

| Quelle | Lizenz | Richtung zu AGPL-3.0-or-later |
|---|---|---|
| OpenMouse | AGPL-3.0 | identisch – maßgeblich für die Lizenzwahl |
| libatk-rs | GPL-3.0 | kompatibel – GPL-3.0-Code darf nach AGPL-3.0 §13 eingebunden werden |
| Linux-ATK | GPL-3.0 | kompatibel (wie oben) |
| atkctl | BSD-3-Clause | kompatibel (permissiv, Copyright-Hinweis unten) |

### BSD-3-Clause-Hinweis (atkctl)

```
Copyright (c) 2024, ATK Mouse contributors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the conditions of the
BSD 3-Clause License are met. Neither the name of the copyright holder
nor the names of its contributors may be used to endorse or promote
products derived from this software without specific prior written
permission.
```

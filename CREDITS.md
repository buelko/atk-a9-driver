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
**nicht** als Quelle verwendet, da GPL-2.0-only (ohne „or later") nicht mit
GPL-3.0 kompatibel ist.

## Abgeleitete Daten: Gerätekatalog

`atk_a9/catalog.py` enthält 187 VID/PID-Einträge (Marke, Modell, Sensor,
CID/MID, Usage-Page, Feature-Flags). Diese Daten sind aus
`src/drivers/atk/device-catalog.generated.ts` des OpenMouse-Projekts
übernommen und lediglich ins Python-Format umgesetzt. Es handelt sich damit
um ein **abgeleitetes Werk** dieser Datei.

OpenMouse steht unter **AGPL-3.0**. Für den hier vorliegenden Fall – ein
lokales CLI-/Desktop-Tool ohne Netzwerkdienst – entspricht die AGPL in der
Wirkung der GPL-3.0; die zusätzliche Pflicht aus AGPL §13 (Quelltext für
Nutzer über Netzwerk) wird nicht ausgelöst, solange das Programm nicht als
Netzwerkdienst betrieben wird.

> **Hinweis:** Wer diesen Treiber als Netzwerkdienst anbietet, muss die
> AGPL-§13-Pflicht der übernommenen Katalogdaten beachten.

## Lizenzwahl

Dieses Projekt steht unter der **GNU General Public License v3.0 or later**
(siehe [LICENSE](LICENSE); SPDX: `GPL-3.0-or-later`).

Kompatibilität der verwendeten Quellen:

| Quelle | Lizenz | Richtung zu GPL-3.0-or-later |
|---|---|---|
| OpenMouse | AGPL-3.0 | zulässig – AGPL-3.0-Material darf mit GPL-3.0-Code kombiniert werden (GPL-3.0 §13); die AGPL-Pflichten bleiben für diesen Teil bestehen |
| libatk-rs | GPL-3.0 | kompatibel |
| Linux-ATK | GPL-3.0 | kompatibel |
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

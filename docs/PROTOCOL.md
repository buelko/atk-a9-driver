# ATK/Compx-Konfigurationsprotokoll (Report 0x08)

Diese Beschreibung gibt wieder, was `atk_a9/protocol.py` und `atk_a9/device.py`
umsetzen. Die Feldbedeutungen stammen aus offenen Referenzprojekten:

- OpenMouse-Project/mouse-protocol (`docs/atk-testing.md`, `hid.ts`)
- libatk-rs (EEPROM-Adressen)
- VoideUI/Linux-ATK
- xb-bx/atk-a9-ultra-driver (ATK A9 Ultra)
- szabatoro/atkctl

Mit **[geprüft]** markierte Punkte wurden in diesem Projekt an einer ATK A9
Ultra (CID/MID 2,87, Firmware 3.0) am 1K-Dongle `373b:101a` beobachtet. Alles
andere ist aus den Referenzen übernommen.

## Transport

Der Dongle meldet drei HID-Interfaces. Konfiguriert wird über das Interface,
dessen Report-Descriptor einen Output-Report `0x08` mit 16 Byte enthält
(hier `if01`). Anfragen werden als Output-Report geschrieben, Antworten kommen
als Input-Report mit derselben Report-ID. **[geprüft]**

Jeder offene hidraw-Handle erhält alle Antworten, auch die auf Anfragen anderer
Prozesse. Der Treiber serialisiert deshalb mit `flock` und gleicht beim
EEPROM-Lesen die Adresse der Antwort ab.

## Frame (17 Byte)

| Byte | Bedeutung |
|---|---|
| 0 | Report-ID `0x08` |
| 1 | Kommando |
| 2 | Status – Anfrage `0x00`; Antwort `0x00` = ok, `0x01` = Fehler |
| 3–4 | Adresse (big endian) |
| 5 | deklarierte Datenlänge (max. 10) |
| 6–15 | Daten |
| 16 | Prüfsumme: Summe aller 17 Byte & `0xFF` == `0x55` |

Beispiel **[geprüft]** – Tasten-Slot 1 lesen:

```
>> 08 08 00 00 60 04 00 00 00 00 00 00 00 00 00 00 e1
<< 08 08 00 00 60 04 01 01 00 53 00 00 00 00 00 00 8c
```

Die Antwort wiederholt Kommando und Adresse der Anfrage. **[geprüft]** Laut
Referenzen kann eine Lese-Antwort auch die Kommando-ID `0x07` tragen; der
Treiber akzeptiert beide.

## Kommandos

| ID | Name | Hinweis |
|---|---|---|
| `0x03` | GET_ONLINE | beantwortet der Dongle selbst; Daten[0] = 1 verbunden, 0 nicht; danach 3 Byte RF-Kennung **[geprüft]** |
| `0x04` | GET_BATTERY | Daten[0] = Prozent, Daten[1] = lädt **[geprüft]** |
| `0x05` | SET_PAIR | Pairing starten |
| `0x06` | GET_PAIR_RESULT | Status + Restsekunden |
| `0x07` | SET_EEPROM | Daten an Adresse schreiben |
| `0x08` | GET_EEPROM | Länge muss deklariert werden, sonst laut Referenzen Status 1 und keine Daten |
| `0x09` | RESTORE_FACTORY | Werkseinstellung |
| `0x0E` / `0x0F` | GET/SET_CURRENT_CONFIG | aktives Profil; Lesen liefert ein rohes Byte (Länge 1). Setzen wird abgelehnt: rohes Byte unbeantwortet, Wertepaar Status 1 **[geprüft]** |
| `0x10` | GET_CID_MID | Daten[0] = CID, Daten[1] = MID → Modell **[geprüft]** |
| `0x12` | GET_MOUSE_VERSION | zwei Bytes Haupt-/Nebenversion, als BCD gedeutet; beobachtet `03 00` = 3.0 |
| `0x13` | DONGLE_EXIT_PAIR | Pairing abbrechen |
| `0x16` / `0x17` | SET/GET_FAR_DISTANCE | Reichweiten-Modus; Lesen liefert Länge 10, alles Null **[geprüft]** |
| `0x18` / `0x19` | SET/GET_DONGLE_LIGHT | Dongle-LED; Lesen am 1K-Dongle mit Status 1 abgelehnt **[geprüft]** |
| `0x1D` | GET_RECEIVER_VERSION | zwei Bytes; beobachtet `03 00` **[geprüft]** |

Weitere IDs (`0x01`, `0x02`, `0x0A`, `0x0D`, `0x14`, `0x15`) sind in
`protocol.py` benannt, werden aber nicht benutzt. `0x0D` (Boot-Modus) dient dem
Firmware-Update und wird vom Treiber nie gesendet.

Schläft die Maus, antwortet nur noch der Dongle auf `0x03`; `0x04`, `0x08`
und `0x10` blieben dann unbeantwortet. **[geprüft]**

## Prüfsummen im EEPROM

- **Wertepaar:** Einzelwerte liegen als `(wert, 0x55 - wert)`.
- **4-Byte-Record** (DPI, DPI-Farbe, Taste): die vier Bytes summieren auf `0x55`.

Laut Referenzen lehnt die Firmware Schreibvorgänge mit falscher Prüfsumme ab
(Status 1).

## EEPROM-Karte

| Adresse | Länge | Inhalt |
|---|---|---|
| `0x0000` | 6 (R1-Familie: 10) | Systemzeile: Polling-Code, DPI-Stufenzahl, aktive Stufe (0-basiert), je als Paar |
| `0x000A` | 2 | Lift-off-Code als Paar |
| `0x000C` | 8 × 4 | DPI-Stufen 1–8 |
| `0x002C` | 8 × 4 | DPI-Farben `[r, g, b, prüf]` |
| `0x004C` | 8 | RGB (nicht umgesetzt) |
| `0x0054` | 12 | zweite Beleuchtung (nicht umgesetzt) |
| `0x0060` | 16 × 4 | Tasten `[klasse, wert1, wert2, prüf]` |
| `0x00A9` | 10 | Erweitert: Debounce, Motion Sync, Schlafzeit, Geradenkorrektur, Ripple (Paare) |
| `0x00B5` | 6 | Sensor: Schlaf aktiv, Schlafzeit, Modus (0 basic, 1 competitive) |
| `0x00BD` | 4 | Sensorwinkel (vorzeichenbehaftet), Angle-Snap |
| `0x0100` | 16 × 32 | Tastenkürzel-Slots (nur roh lesbar) |
| `0x0300` | 16 × 384 | Makros (nicht umgesetzt) |

Der Treiber liest eine Zeile, ändert die betroffenen Paare und schreibt die
ganze Zeile in der gelesenen Länge zurück (so verhalten sich auch die
Referenztreiber). Ein Lese- oder Schreib-Frame trägt höchstens 10 Byte.

## Kodierungen

**Polling-Rate**

| Hz | 125 | 250 | 500 | 1000 | 2000 | 4000 | 8000 |
|---|---|---|---|---|---|---|---|
| Code | `0x08` | `0x04` | `0x02` | `0x01` | `0x10` | `0x20` | `0x40` |

Der 1K-Dongle `101a` lässt höchstens 1000 Hz zu.

**Schlafzeit:** Code × 10 Sekunden. Erlaubt: 10, 30, 50, 60, 120, 180, 300,
600, 900, 1200, 1500, 1800 s.

**Debounce:** Millisekunden direkt; erlaubt 0, 1, 2, 4, 8, 15, 20.

**Lift-off** (je Sensor):

| Sensoren | Skala |
|---|---|
| PAW3950Ultra, PAW3395Ultra | Code 1–11 → 0,7–1,7 mm (`mm = (code + 6) / 10`) |
| PAW3950, PAW3950DM | 3 → 0,7 mm, 1 → 1,0 mm, 2 → 2,0 mm |
| übrige | 1 → 1,0 mm, 2 → 2,0 mm |

**DPI-Record** `[x_byte, y_byte, modus, prüf]`: Das Modus-Byte enthält je Achse
ein Nibble (X unten, Y oben). Bits 2–3 erweitern den Zähler um zwei Bit, Bit 1
wählt den 50er-Bereich oberhalb 10 000, Bit 0 verdoppelt.

| Familie | Bereich | Schrittweite |
|---|---|---|
| `ultra` (PAW3950Ultra) | 10–42 000 | 10 bis 10 000, dann 50 bis 30 000, dann 100 |
| `step50` | 50–36 000 | 50 bis 30 000, dann 100 |
| `paw3395se` | 200–18 000 | feste Codetabelle, 50 bis 10 000, dann 100 |
| `stepped` | 200–15 000 | 100 |

Für `ultra` bis 10 000 DPI gilt `zähler = dpi / 10 - 1`.

**Tasten** – Klassen:

| Klasse | Bedeutung | wert1 |
|---|---|---|
| `0x00` | deaktiviert | |
| `0x01` | Maustaste | Bitmaske: 1 links, 2 rechts, 4 Mitte, 8 zurück, 16 vor |
| `0x02` | DPI | 1 durchschalten, 2 erhöhen, 3 senken |
| `0x03` | horizontal scrollen | 1 links, 2 rechts |
| `0x04` | Fire-Key | |
| `0x05` | Tastenkürzel | Slot |
| `0x06` | Makro | Slot |
| `0x07` | Polling-Rate umschalten | |
| `0x08` | Beleuchtung | |
| `0x09` | Profil umschalten | |
| `0x0A` | DPI-Sperre | |
| `0x0B` | Mausrad | 1 hoch, 2 runter |

Ein Slot mit `00 00 00 00` gilt als unprogrammiert (Ende der Tastenliste),
`ff ff ff ff` als leer.

# ATK/Compx-Konfigurationsprotokoll (Report 0x08)

Diese Beschreibung gibt wieder, was `atk_a9/protocol.py` und `atk_a9/device.py`
umsetzen. Die Feldbedeutungen stammen aus offenen Referenzprojekten und, für
Schnellfeuer, Tastenkombinationen, Makros, Mausrad-Schutz und Reichweiten-Modus,
aus dem JavaScript der Herstellersoftware ATK HUB 3.2.33 (hub.atk.pro):

- OpenMouse-Project/mouse-protocol (`docs/atk-testing.md`, `hid.ts`)
- libatk-rs (EEPROM-Adressen)
- VoideUI/Linux-ATK
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
| `0x0000` | 6 (R1-Familie: 10) | Systemzeile: Polling-Code, DPI-Stufenzahl, aktive Stufe (0-basiert), je als Paar. Die Herstellersoftware liest und schreibt immer 10 Byte: Byte 6 = Mausrad-Schutz, Byte 8 = Tastenmodus (ungenutzt). An der A9 Ultra sind alle fünf Paare gültig **[geprüft]** |
| `0x000A` | 2 | Lift-off-Code als Paar |
| `0x000C` | 8 × 4 | DPI-Stufen 1–8 |
| `0x002C` | 8 × 4 | DPI-Farben `[r, g, b, prüf]` |
| `0x004C` | 8 | RGB (nicht umgesetzt) |
| `0x0054` | 12 | zweite Beleuchtung (nicht umgesetzt) |
| `0x0060` | 16 × 4 | Tasten `[klasse, wert1, wert2, prüf]` |
| `0x00A9` | 10 | Erweitert: Debounce, Motion Sync, Schlafzeit, Geradenkorrektur, Ripple (Paare) |
| `0x00B5` | 6 | Sensor: Schlaf aktiv, Schlafzeit, Modus (0 basic, 1 competitive, 2 max) |
| `0x00BD` | 4 | Sensorwinkel (vorzeichenbehaftet), Angle-Snap |
| `0x0100` | 16 × 32 | Tastenkombinationen, ein Slot je Taste; die Herstellersoftware nutzt 6 |
| `0x0300` | 16 × 384 | Makros; die Herstellersoftware nutzt 12 |

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
| `0x04` | Schnellfeuer | Abstand in ms (3–255); wert2 = Klicks je Druck 1–3, 0 = Dauerfeuer beim Halten |
| `0x05` | Tastenkombination | 0; die Tastenfolge steht im Slot der Taste |
| `0x06` | Makro | Makro-Nummer (0-basiert); wert2 = Wiederholung, siehe unten |
| `0x07` | Polling-Rate umschalten | |
| `0x08` | Beleuchtung | |
| `0x09` | Profil umschalten | |
| `0x0A` | DPI-Sperre | |
| `0x0B` | Mausrad | 1 hoch, 2 runter |
| `0x0C` | 180-Grad-Drehung | nur mit Firmware-Merkmal (Versionsantwort Byte 2, Bit 0); A9 Ultra: nein |

Ein Slot mit `00 00 00 00` gilt als unprogrammiert (Ende der Tastenliste),
`ff ff ff ff` als leer.

## Tastenereignisse

Tastenkombinationen und Makros bestehen aus Ereignissen:

| Byte | Bedeutung |
|---|---|
| 0 | Flags: Bit 7 drücken, Bit 6 loslassen; Bits 0–2 Typ |
| 1 | wert1 |
| 2 | wert2 (bisher immer 0) |
| 3–4 | nur Makro: Pause nach dem Ereignis in ms, big endian |

| Typ | Bedeutung | wert1 |
|---|---|---|
| 0 | Modifier | Bitmaske: 1 Strg, 2 Umschalt, 4 Alt, 8 Win, 16–128 die rechten |
| 1 | Taste | HID-Usage (z. B. `0x04` = A, `0x1e` = 1) |
| 2 / 3 | Medien- / Systemtaste | nicht umgesetzt |
| 4 | Maustaste | Bitmaske wie bei Klasse `0x01` (ungeprüft) |
| 6 | Mausrad | 1 hoch, 255 runter; danach folgt ein Eintrag mit 0 |

## Tastenkombination (Slot `0x0100 + taste * 32`)

`[anzahl, anzahl × 3 Byte, prüfsumme]`, höchstens 6 Ereignisse. Die Prüfsumme
ergänzt Anzahl und Ereignisbytes auf `0x55`. Reihenfolge der Herstellersoftware:
Modifier drücken, Taste drücken, Modifier loslassen, Taste loslassen.
Geschrieben wird in 10-Byte-Rahmen ab Slot-Anfang (ein oder zwei Rahmen),
danach der Tasten-Record `05 00 00 50`.

Aus der Maus gelesen **[geprüft]** – Taste 5 sendet „1“:

```
0180  02 81 1e 00 41 1e 00 55 00 00
```

## Makro (Slot `0x0300 + nummer * 384`)

| Offset | Inhalt |
|---|---|
| 0–30 | fester Kopf: `08`, achtmal `02`, 22-mal `ff` |
| 31 | Anzahl der Ereignisse (1–70; 0 = leer) |
| 32… | Ereignisse zu je 5 Byte |
| danach | Prüfsumme über Anzahl und Ereignisbytes |

Geschrieben wird der Slot in 10-Byte-Rahmen ab Slot-Anfang, der letzte Rahmen
mit Nullen aufgefüllt. Der Treiber kürzt den letzten Rahmen, damit ein volles
Makro (383 Byte) nicht in den nächsten Slot schreibt. Leeren: Kopf und Anzahl 0.

Aus der Maus gelesen **[geprüft]** – „1“ drücken, 15 ms, loslassen, 5 ms:

```
031f  02 81 1e 00 00 0f 41 1e 00 00 05 41
```

Wiederholung im Tasten-Record (`06 nummer wert2 prüf`): `0xFE` solange
gehalten, `0xFD` bis zum nächsten Druck der Taste, `0xFF` bis zu einem
beliebigen Tastendruck, sonst die Anzahl der Durchläufe. Die Bezeichnungen sind
aus den Namen der Herstellersoftware abgeleitet (StopImmediately,
RepeatUntilNextKeyPress, RepeatUntilNextAnyKeyPress).

## Weitere Funktionen

- **Sensorwinkel** (`0x00BD`): `[winkel, prüf, snap, prüf]`, Winkel als
  vorzeichenbehaftetes Byte. Der Treiber erlaubt −30…30 Grad; die Grenze ist
  eine Annahme.
- **Mausrad-Schutz** (Systemzeile Byte 6): Zeit in 10-ms-Schritten, 0 = aus;
  die Herstellersoftware bietet 100–1000 ms.
- **Reichweiten-Modus**: Kommando `0x16` mit deklarierter Länge 10, Byte 0 =
  0/1; Lesen mit `0x17`.
- **Werkseinstellungen**: Kommando `0x09`; die Herstellersoftware leert danach
  zusätzlich alle Makro-Slots.

Die Schreibwege dieses Abschnitts wurden an der Hardware noch nicht ausgeführt;
geprüft sind das Lesen und die Übereinstimmung der Kodierer mit den Bytes, die
die Herstellersoftware in die Maus geschrieben hat.

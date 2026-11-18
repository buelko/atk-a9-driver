"""Tastenereignisse fuer Tastenkombinationen und Makros.

Formate wie in der Herstellersoftware (ATK HUB 3.2.33, COMPX-Protokoll) und
gegen die in einer ATK A9 Ultra gespeicherten Daten geprueft:

Ereignis (3 Byte, Tastenkombination)   [flags, wert1, wert2]
Ereignis (5 Byte, Makro)               [flags, wert1, wert2, delay_hi, delay_lo]

    flags  Bit 7 = druecken, Bit 6 = loslassen, Bits 0-2 = Typ
    Typ    0 Modifier (wert1 = Bitmaske), 1 Taste (wert1 = HID-Usage),
           4 Maustaste (wert1 = Bitmaske), 6 Mausrad (wert1 = 1 hoch / 255 runter)

Tastenkombination (Slot 0x0100 + taste * 32):
    [anzahl, ereignisse..., pruefsumme]            anzahl <= 6
Makro (Slot 0x0300 + nr * 384):
    [31 Byte Kopf, anzahl, ereignisse..., pruefsumme]   anzahl <= 70

Die Pruefsumme ergaenzt Anzahl + Ereignisbytes auf 0x55.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .protocol import CHECKSUM_TARGET, ProtocolError

FLAG_DOWN = 0x80
FLAG_UP = 0x40
TYPE_MASK = 0x07


class KeyType(IntEnum):
    MODIFIER = 0
    KEY = 1
    MEDIA = 2
    POWER = 3
    MOUSE = 4
    MOVE = 5
    SCROLL = 6


SHORTCUT_MAX_EVENTS = 6
SHORTCUT_SLOTS = 6  # ein Slot je Taste 1..6 (Slot-Nummer = Tastennummer)
MACRO_SLOTS = 12
MACRO_MAX_EVENTS = 70
MACRO_HEADER = bytes([0x08] + [0x02] * 8 + [0xFF] * 22)  # fester Kopf der Herstellersoftware
MACRO_COUNT_OFFSET = len(MACRO_HEADER)  # 31
DEFAULT_DELAY_MS = 10
MAX_DELAY_MS = 0xFFFF
SCROLL_UP = 1
SCROLL_DOWN = 255

# Makro-Ausfuehrung am Tasten-Record (Klasse 6, wert2); 1..250 = so oft ausfuehren
MACRO_MODE_HOLD = 0xFE     # Hersteller: StopImmediately
MACRO_MODE_TOGGLE = 0xFD   # Hersteller: RepeatUntilNextKeyPress
MACRO_MODE_ANYKEY = 0xFF   # Hersteller: RepeatUntilNextAnyKeyPress
MACRO_MODES: dict[str, int] = {"hold": MACRO_MODE_HOLD, "toggle": MACRO_MODE_TOGGLE, "anykey": MACRO_MODE_ANYKEY}
MACRO_MODE_LABELS: dict[int, str] = {
    MACRO_MODE_HOLD: "solange gehalten",
    MACRO_MODE_TOGGLE: "bis zum naechsten Druck dieser Taste",
    MACRO_MODE_ANYKEY: "bis zu einem beliebigen Tastendruck",
}
MACRO_MAX_REPEAT = 250

FIRE_INTERVAL_MIN = 3
FIRE_INTERVAL_MAX = 255
FIRE_COUNT_MAX = 3  # 0 = Dauerfeuer, solange die Taste gehalten wird

MODIFIERS: dict[str, int] = {
    "ctrl": 0x01, "shift": 0x02, "alt": 0x04, "win": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "rwin": 0x80,
}
MODIFIER_ALIASES: dict[str, str] = {
    "strg": "ctrl", "control": "ctrl", "lctrl": "ctrl", "lshift": "shift", "lalt": "alt",
    "super": "win", "meta": "win", "cmd": "win", "lwin": "win", "altgr": "ralt", "rstrg": "rctrl",
}
MOUSE_BUTTONS: dict[str, int] = {"left": 0x01, "right": 0x02, "middle": 0x04, "back": 0x08, "forward": 0x10}
MOUSE_ALIASES: dict[str, str] = {
    "links": "left", "rechts": "right", "mitte": "middle", "mittel": "middle",
    "zurueck": "back", "vor": "forward", "lmb": "left", "rmb": "right", "mmb": "middle",
}


def _build_hid_keys() -> dict[str, int]:
    keys: dict[str, int] = {}
    for index, letter in enumerate("abcdefghijklmnopqrstuvwxyz"):
        keys[letter] = 0x04 + index
    for index, digit in enumerate("123456789"):
        keys[digit] = 0x1E + index
    keys["0"] = 0x27
    named = {
        "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
        "minus": 0x2D, "equal": 0x2E, "lbracket": 0x2F, "rbracket": 0x30, "backslash": 0x31,
        "semicolon": 0x33, "quote": 0x34, "grave": 0x35, "comma": 0x36, "dot": 0x37, "slash": 0x38,
        "capslock": 0x39, "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48, "insert": 0x49,
        "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
        "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52, "numlock": 0x53,
        "kpslash": 0x54, "kpasterisk": 0x55, "kpminus": 0x56, "kpplus": 0x57, "kpenter": 0x58,
        "kp0": 0x62, "kpdot": 0x63, "menu": 0x65,
    }
    keys.update(named)
    for number in range(1, 13):
        keys[f"f{number}"] = 0x3A + number - 1
    for number in range(13, 25):
        keys[f"f{number}"] = 0x68 + number - 13
    for number in range(1, 10):
        keys[f"kp{number}"] = 0x59 + number - 1
    return keys


HID_KEYS: dict[str, int] = _build_hid_keys()
KEY_ALIASES: dict[str, str] = {
    "return": "enter", "escape": "esc", "leertaste": "space", "entf": "delete", "del": "delete",
    "einfg": "insert", "ins": "insert", "pos1": "home", "ende": "end", "bildauf": "pageup",
    "bildab": "pagedown", "pgup": "pageup", "pgdn": "pagedown", "druck": "printscreen",
    "rechts": "right", "links": "left", "runter": "down", "hoch": "up",
}
_KEY_NAMES = {code: name for name, code in HID_KEYS.items()}
_MODIFIER_NAMES = {bit: name for name, bit in MODIFIERS.items()}
_MOUSE_NAMES = {bit: name for name, bit in MOUSE_BUTTONS.items()}


def key_code(name: str) -> int:
    """HID-Usage einer Taste: Name ('a', 'f5', 'enter') oder Zahl ('0x5a')."""
    text = name.strip().lower()
    text = KEY_ALIASES.get(text, text)
    if text in HID_KEYS:
        return HID_KEYS[text]
    try:
        code = int(text, 0)
    except ValueError:
        raise ValueError(f"unbekannte Taste {name!r}") from None
    if not 1 <= code <= 0xFF:
        raise ValueError(f"Tastencode {code} ausserhalb 1..255")
    return code


def key_name(code: int) -> str:
    return _KEY_NAMES.get(code, f"0x{code:02x}")


def modifier_bit(name: str) -> int | None:
    text = name.strip().lower()
    return MODIFIERS.get(MODIFIER_ALIASES.get(text, text))


def mouse_bit(name: str) -> int:
    text = name.strip().lower()
    text = MOUSE_ALIASES.get(text, text)
    if text not in MOUSE_BUTTONS:
        raise ValueError(f"unbekannte Maustaste {name!r} ({', '.join(MOUSE_BUTTONS)})")
    return MOUSE_BUTTONS[text]


@dataclass(frozen=True)
class KeyEvent:
    type: int
    down: bool | None  # None: Mausrad (weder druecken noch loslassen)
    value1: int
    value2: int = 0
    delay_ms: int = 0  # nur Makros: Pause nach dem Ereignis

    @property
    def flags(self) -> int:
        state = 0 if self.down is None else (FLAG_DOWN if self.down else FLAG_UP)
        return state | (self.type & TYPE_MASK)

    @classmethod
    def from_bytes(cls, flags: int, value1: int, value2: int, delay_ms: int = 0) -> "KeyEvent":
        # Reihenfolge wie beim Hersteller: Bit 6 (loslassen) hat Vorrang
        down: bool | None = False if flags & FLAG_UP else (True if flags & FLAG_DOWN else None)
        return cls(flags & TYPE_MASK, down, value1, value2, delay_ms)

    def raw_token(self) -> str:
        return f"raw:{self.flags:02x}:{self.value1:02x}:{self.value2:02x}"

    def describe(self) -> str:
        """Token in der Makro-Textform (ohne Pause). Was sich nicht benennen
        laesst (Medientasten, unbekannte Typen), wird als raw:FLAGS:W1:W2
        ausgegeben und bleibt so beim Bearbeiten erhalten."""
        if self.down is None or self.value2:
            return self.raw_token()
        sign = "+" if self.down else "-"
        if self.type == KeyType.MODIFIER and self.value1:
            return sign + "+".join(name for bit, name in _MODIFIER_NAMES.items() if self.value1 & bit)
        if self.type == KeyType.KEY and self.value1:
            return sign + key_name(self.value1)
        if self.type == KeyType.MOUSE and self.value1 in _MOUSE_NAMES:
            return sign + "mouse:" + _MOUSE_NAMES[self.value1]
        return self.raw_token()


def _checksum(body: bytes) -> int:
    return (CHECKSUM_TARGET - sum(body)) & 0xFF


# ---------------------------------------------------------------------------
# Tastenkombination
# ---------------------------------------------------------------------------


def combo_events(key: str | int, modifiers: list[str] | tuple[str, ...] = ()) -> list[KeyEvent]:
    """Ereignisfolge wie beim Hersteller: Modifier druecken, Taste druecken,
    Modifier loslassen, Taste loslassen. Hoechstens 2 Modifier (6 Ereignisse)."""
    code = key if isinstance(key, int) else key_code(key)
    bits: list[int] = []
    for name in modifiers:
        bit = modifier_bit(name)
        if bit is None:
            raise ValueError(f"unbekannter Modifier {name!r} ({', '.join(MODIFIERS)})")
        if bit not in bits:
            bits.append(bit)
    if len(bits) > 2:
        raise ValueError("hoechstens zwei Modifier je Tastenkombination")
    downs = [KeyEvent(KeyType.MODIFIER, True, bit) for bit in bits] + [KeyEvent(KeyType.KEY, True, code)]
    ups = [KeyEvent(KeyType.MODIFIER, False, bit) for bit in bits] + [KeyEvent(KeyType.KEY, False, code)]
    return downs + ups


def parse_combo(text: str) -> list[KeyEvent]:
    """'ctrl+shift+a' -> Ereignisfolge. Der letzte Teil ist die Taste."""
    parts = [part.strip() for part in text.replace(" ", "").split("+") if part.strip()]
    if not parts:
        raise ValueError("leere Tastenkombination")
    *modifiers, key = parts
    if modifier_bit(key) is not None:
        raise ValueError("eine Tastenkombination braucht am Ende eine normale Taste (z. B. ctrl+c)")
    return combo_events(key, modifiers)


def describe_combo(events: list[KeyEvent]) -> str:
    """Gedrueckte Tasten in Reihenfolge, z. B. 'ctrl+shift+a'."""
    parts: list[str] = []
    for event in events:
        if not event.down:
            continue
        token = event.describe()
        parts.extend(token[1:].split("+") if token[0] == "+" else [token])
    return "+".join(parts) if parts else "(leer)"


def encode_shortcut(events: list[KeyEvent]) -> bytes:
    if not 1 <= len(events) <= SHORTCUT_MAX_EVENTS:
        raise ValueError(f"Tastenkombination braucht 1..{SHORTCUT_MAX_EVENTS} Ereignisse")
    body = bytearray([len(events)])
    for event in events:
        body += bytes([event.flags, event.value1 & 0xFF, event.value2 & 0xFF])
    return bytes(body) + bytes([_checksum(body)])


def decode_shortcut(raw: bytes) -> list[KeyEvent]:
    """Leere Liste fuer unprogrammierte Slots; ProtocolError bei defekter Pruefsumme."""
    if not raw:
        return []
    count = raw[0]
    if count == 0 or count > SHORTCUT_MAX_EVENTS:
        return []
    end = 1 + count * 3
    if len(raw) < end + 1:
        raise ProtocolError("Tastenkombination: Slot zu kurz")
    if _checksum(raw[:end]) != raw[end]:
        raise ProtocolError("Tastenkombination: Pruefsumme falsch (" + raw[: end + 1].hex(" ") + ")")
    return [KeyEvent.from_bytes(*raw[1 + i * 3 : 4 + i * 3]) for i in range(count)]


# ---------------------------------------------------------------------------
# Makro
# ---------------------------------------------------------------------------


def encode_macro(events: list[KeyEvent]) -> bytes:
    """Kompletter Slot-Anfang: Kopf, Anzahl, Ereignisse, Pruefsumme."""
    if len(events) > MACRO_MAX_EVENTS:
        raise ValueError(f"Makro hat {len(events)} Ereignisse, erlaubt sind {MACRO_MAX_EVENTS}")
    body = bytearray([len(events)])
    for event in events:
        if not 0 <= event.delay_ms <= MAX_DELAY_MS:
            raise ValueError(f"Pause {event.delay_ms} ms ausserhalb 0..{MAX_DELAY_MS}")
        body += bytes([event.flags, event.value1 & 0xFF, event.value2 & 0xFF])
        body += event.delay_ms.to_bytes(2, "big")
    return MACRO_HEADER + bytes(body) + bytes([_checksum(body)])


def macro_count(slot_head: bytes) -> int:
    """Anzahl der Ereignisse aus den ersten 32 Byte eines Slots (0 = leer)."""
    if len(slot_head) <= MACRO_COUNT_OFFSET:
        return 0
    count = slot_head[MACRO_COUNT_OFFSET]
    return count if 0 < count <= MACRO_MAX_EVENTS else 0


def decode_macro(raw: bytes) -> list[KeyEvent]:
    count = macro_count(raw)
    if count == 0:
        return []
    start = MACRO_COUNT_OFFSET
    end = start + 1 + count * 5
    if len(raw) < end + 1:
        raise ProtocolError("Makro: Slot zu kurz")
    if _checksum(raw[start:end]) != raw[end]:
        raise ProtocolError("Makro: Pruefsumme falsch")
    events = []
    for index in range(count):
        offset = start + 1 + index * 5
        flags, value1, value2 = raw[offset : offset + 3]
        delay = int.from_bytes(raw[offset + 3 : offset + 5], "big")
        events.append(KeyEvent.from_bytes(flags, value1, value2, delay))
    return events


# -- Textform -----------------------------------------------------------------
#
#   a            Taste tippen (druecken + loslassen)
#   +ctrl -ctrl  druecken / loslassen (Modifier, Taste oder mouse:left)
#   click:left   Mausklick (druecken + loslassen)
#   wheel:up     Mausrad einen Schritt (wheel:down)
#   50ms         Pause nach dem vorherigen Ereignis (Standard 10 ms)


def _press_event(name: str, down: bool) -> KeyEvent:
    text = name.strip().lower()
    if text.startswith("mouse:"):
        return KeyEvent(KeyType.MOUSE, down, mouse_bit(text[6:]))
    bits = [modifier_bit(part) for part in text.split("+")]
    if all(bit is not None for bit in bits):
        mask = 0
        for bit in bits:
            mask |= bit or 0
        return KeyEvent(KeyType.MODIFIER, down, mask)
    return KeyEvent(KeyType.KEY, down, key_code(text))


def _raw_event(token: str) -> KeyEvent:
    try:
        flags, value1, value2 = (int(part, 16) for part in token.split(":")[1:])
    except ValueError:
        raise ValueError(f"ungueltiges Roh-Ereignis {token!r} (raw:FLAGS:W1:W2, hexadezimal)") from None
    if not all(0 <= value <= 0xFF for value in (flags, value1, value2)):
        raise ValueError(f"Roh-Ereignis {token!r}: Werte ausserhalb 00..ff")
    return KeyEvent.from_bytes(flags, value1, value2)


def parse_macro(text: str, default_delay: int = DEFAULT_DELAY_MS) -> list[KeyEvent]:
    events: list[KeyEvent] = []

    def add(event: KeyEvent) -> None:
        events.append(KeyEvent(event.type, event.down, event.value1, event.value2, default_delay))

    for token in text.replace(",", " ").split():
        lower = token.lower()
        if lower.endswith("ms") and lower[:-2].isdigit():
            if not events:
                raise ValueError("eine Pause braucht ein Ereignis davor")
            delay = int(lower[:-2])
            if not 0 <= delay <= MAX_DELAY_MS:
                raise ValueError(f"Pause {delay} ms ausserhalb 0..{MAX_DELAY_MS}")
            last = events[-1]
            events[-1] = KeyEvent(last.type, last.down, last.value1, last.value2, delay)
            # Mausrad besteht aus zwei Eintraegen mit gleicher Pause
            if last.type == KeyType.SCROLL and len(events) >= 2 and events[-2].type == KeyType.SCROLL:
                first = events[-2]
                events[-2] = KeyEvent(first.type, first.down, first.value1, first.value2, delay)
        elif lower.startswith("raw:"):
            add(_raw_event(lower))
        elif lower in ("wheel:up", "wheel:down", "rad:hoch", "rad:runter"):
            direction = SCROLL_UP if lower.endswith(("up", "hoch")) else SCROLL_DOWN
            # wie beim Hersteller: Schritt, danach ein Eintrag mit 0
            add(KeyEvent(KeyType.SCROLL, None, direction))
            add(KeyEvent(KeyType.SCROLL, None, 0))
        elif lower.startswith(("click:", "klick:")):
            bit = mouse_bit(lower.split(":", 1)[1])
            add(KeyEvent(KeyType.MOUSE, True, bit))
            add(KeyEvent(KeyType.MOUSE, False, bit))
        elif lower[0] in "+-" and len(lower) > 1:
            add(_press_event(lower[1:], lower[0] == "+"))
        else:
            add(_press_event(lower, True))
            add(_press_event(lower, False))
    if len(events) > MACRO_MAX_EVENTS:
        raise ValueError(f"Makro hat {len(events)} Ereignisse, erlaubt sind {MACRO_MAX_EVENTS}")
    return events


def format_macro(events: list[KeyEvent], default_delay: int = DEFAULT_DELAY_MS) -> str:
    """Textform; parse_macro(format_macro(x)) ergibt wieder x."""
    tokens: list[str] = []
    index = 0
    while index < len(events):
        event = events[index]
        following = events[index + 1] if index + 1 < len(events) else None
        if (
            event.type == KeyType.SCROLL
            and event.value1 in (SCROLL_UP, SCROLL_DOWN)
            and following is not None
            and following.type == KeyType.SCROLL
            and following.value1 == 0
            and following.delay_ms == event.delay_ms
        ):
            tokens.append("wheel:up" if event.value1 == SCROLL_UP else "wheel:down")
            index += 2
        else:
            tokens.append(event.describe())
            index += 1
        if event.delay_ms != default_delay:
            tokens.append(f"{event.delay_ms}ms")
    return " ".join(tokens)


def describe_macro_mode(value: int) -> str:
    if value in MACRO_MODE_LABELS:
        return "wiederholen " + MACRO_MODE_LABELS[value]
    return f"{value}x ausfuehren"


def parse_macro_mode(text: str | int) -> int:
    if isinstance(text, int) or str(text).strip().isdigit():
        count = int(text)
        if not 1 <= count <= MACRO_MAX_REPEAT:
            raise ValueError(f"Wiederholungen {count} ausserhalb 1..{MACRO_MAX_REPEAT}")
        return count
    key = str(text).strip().lower()
    if key not in MACRO_MODES:
        raise ValueError(f"Makro-Modus {text!r} unbekannt (Zahl oder {', '.join(MACRO_MODES)})")
    return MACRO_MODES[key]

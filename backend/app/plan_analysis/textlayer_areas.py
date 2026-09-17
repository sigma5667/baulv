"""v24.9 — Schritt C: PDF-Textlayer als Wahrheitsquelle für Raumflächen.

Vision findet die Räume (Name, Einheit, Typ, Öffnungen). Die FLÄCHE kommt aus
dem Textlayer, sobald ein Raumstempel eindeutig einem Vision-Raum zugeordnet
werden kann. CAD-Stempel sind auf zwei Nachkommastellen exakt, Vision-Lesungen
nicht (gemessen auf GWM EP-04: rund ein Drittel der Flächen falsch gelesen).

Grundsätze:
  * Räume werden NIE angelegt oder gelöscht. Stempel ohne Raum werden nur
    protokolliert.
  * Unsichere Zuordnung → der Vision-Wert bleibt. Kein Raten.
  * Der Textlayer zählt nur, wenn er vertrauenswürdig ist. Scan, OCR-Ebene
    (unsichtbarer Text, OCR-Schrift) oder großflächiges Rasterbild → im Zweifel
    wie ein Scan behandeln: C schweigt. Ein falscher OCR-Wert wäre schlimmer
    als ein leeres Feld.
  * Wohnungssummen ("TOP 1  101,73m2") sind nie eine Raumfläche. Ein
    Vision-Wert, der exakt einer solchen Summe entspricht und zu keinem
    Raumstempel passt, wird geleert.

Zuordnung — jede Stufe vergibt nur eindeutige Paare, jeder Stempel höchstens
einmal:
  1. Raumnummer:  Stempel "TOP 1.4" ↔ Vision ``room_number`` "1.4"
  2. Text:        Einheit + Name ("TOP 2" + "Vorraum" ↔ "TOP 2.1 Vorraum"),
                  sonst Name eindeutig auf dem Blatt
  3. Position:    Pin des Raums in PDF-Punkten → klar nächster Stempel
  4. Wert-Nähe:   Vision-Lesung liegt klar näher an genau einem Kandidaten
  Nach Stufe 3 und 4 läuft Stufe 2 erneut (Ausschluss: bleibt genau ein Raum
  für genau einen Stempel übrig).

Kandidaten eines Raums sind nur die Stempel der BESTEN Passungs-Stufe (gleiche
Einheit vor einheitenlos, gleicher Name vor Teilmenge). Ist diese Stufe schon
vergeben, fällt der Raum NICHT auf eine schwächere zurück — sonst bekäme ein
doppelt erkannter "TOP 2 / Vorraum" die Fläche des Stiegenhaus-Vorraums.

Schutzregel (``policy="cautious"``, Produktions-Standard): hat ein ANDERER
Raum gleichen Namens exakt den Wert des Stempels gelesen — oder dieser Raum
exakt den Wert eines anderen Stempels gleichen Namens —, wird nicht
geschrieben. Hintergrund: Vision ordnet Räume gelegentlich der falschen
Wohnung zu; dann würde die Einheiten-Zuordnung richtige Werte mit Werten der
falschen Wohnung überschreiben. ``policy="trust_unit"`` existiert nur für die
Messung.

Alle Zuordnungsfunktionen sind rein (kein fitz-/DB-Zugriff). Nur
``read_page_textlayer`` / ``process_page`` brauchen das fitz-Dokument und
laufen deshalb im fitz-Executor.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.plan_analysis.textlayer_backfill import (
    ANON_STAMP_NAME,
    _AREA_RE,
    _BELAG_KW,
    _clean_name,
    _to_float,
    harvest_stamps,
)

MODES = ("off", "shadow", "apply")
POLICIES = ("cautious", "trust_unit")

# Plan druckt zwei Nachkommastellen → alles innerhalb ±0,01 ist derselbe Wert.
_EXACT_TOL = 0.011

# --- Vertrauens-Prüfung ------------------------------------------------------
# Gemessen an allen Plänen im Test-Ordner (40 Seiten): CAD-Exporte haben nur
# sichtbaren Text (texttrace ``type`` 0) und Rasterbilder von höchstens 4 %
# der Seite (Logo, Firmenstempel). Bildbasierte Grundrisse liegen bei
# 50–100 %. Die Schwelle ist bewusst niedrig angesetzt: im Zweifel Scan.
_MAX_RASTER_COVERAGE = 0.30
_MIN_NAMED_STAMPS = 3
# texttrace ``type`` 3 = unsichtbarer Text (Render-Mode 3) — genau so legen
# Scanner-/OCR-Programme ihre Textebene über das Bild.
_INVISIBLE_TEXT_TYPE = 3
_OCR_FONT_HINTS = ("glyphless", "ocr", "tesseract", "hocr")

# --- Positions-Stufe -----------------------------------------------------------
# Ein Pin gilt als eindeutig, wenn der nächste Kandidat höchstens 5 % der
# Blattdiagonale entfernt ist und der zweitnächste mindestens doppelt so weit.
_POSITION_MAX_DIAG_FRAC = 0.05
_POSITION_RATIO = 2.0

# --- Wert-Stufe ----------------------------------------------------------------
# Lesung darf höchstens 10 % vom Kandidaten abweichen, und der zweitbeste
# Kandidat muss deutlich weiter weg liegen (3× + 0,05 m²).
_VALUE_MAX_REL = 0.10
_VALUE_SECOND_FACTOR = 3.0
_VALUE_SECOND_ABS = 0.05

_UMLAUT = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss",
                         "Ä": "a", "Ö": "o", "Ü": "u"})
_UNIT_RE = re.compile(
    r"(?<![a-z0-9])(?:top|whg|wohnung|w)\s*\.?\s*(\d+)(?:\s*[./]\s*(\d+))?(?![0-9])"
)
_GENERAL_UNIT_KW = ("allgemein", "erschl", "gemeinschaft", "stiegenhaus", "treppenhaus")
_SUM_KW = ("gesamt", "summe", "nutzflache", "wohnflache", "wnfl", "nfl", "bgf")
_PERIMETER_RE = re.compile(r"^\s*(?:u\s*[:=]\s*)?\d+(?:[.,]\d+)?\s*m\s*$", re.I)
_A_FLAECHE_RE = re.compile(r"(?<![a-z])a\s*\.?\s*fl(?:a|ae)che")

# Kanonische Raumwörter. NUR Abkürzungen und Schreibvarianten werden
# gleichgesetzt — keine Synonyme verschiedener Räume: Diele, Gang, Eingang und
# Vorraum sind auf echten Plänen getrennte Räume (50.02, TOP 1).
_ROOM_WORDS: dict[str, tuple[str, ...]] = {
    "vorraum": ("vorraum",), "vr": ("vorraum",), "vorr": ("vorraum",),
    "vorzimmer": ("vorraum",),
    "abstellraum": ("abstellraum",), "ar": ("abstellraum",),
    "abst": ("abstellraum",), "abstell": ("abstellraum",),
    "abstellr": ("abstellraum",), "afl": ("abstellraum",),
    "abstellflache": ("abstellraum",), "abstellkammer": ("abstellraum",),
    "bad": ("bad",), "badezimmer": ("bad",), "wc": ("wc",), "toilette": ("wc",),
    "dusche": ("dusche",),
    "wohnen": ("wohnen",), "wohnzimmer": ("wohnen",), "wohnraum": ("wohnen",),
    "wohnkuche": ("wohnen", "kuche"),
    "kochen": ("kuche",), "kuche": ("kuche",), "kueche": ("kuche",),
    "kochnische": ("kuche",),
    "essen": ("essen",), "essplatz": ("essen",), "essbereich": ("essen",),
    "esszimmer": ("essen",),
    "schlafen": ("schlafen",), "schlafzimmer": ("schlafen",),
    "schlafraum": ("schlafen",),
    "kind": ("kind",), "kinderzimmer": ("kind",), "kinder": ("kind",),
    "zimmer": ("zimmer",), "gang": ("gang",), "flur": ("gang",),
    "diele": ("diele",), "eingang": ("eingang",), "windfang": ("windfang",),
    "garderobe": ("garderobe",), "speis": ("speis",), "speise": ("speis",),
    "speisekammer": ("speis",),
    "hwr": ("hauswirtschaft",), "hauswirtschaft": ("hauswirtschaft",),
    "hauswirtschaftsraum": ("hauswirtschaft",),
    "technik": ("technik",), "technikraum": ("technik",),
    "keller": ("keller",), "kellerabteil": ("keller",), "lager": ("lager",),
    "buro": ("buro",), "buero": ("buro",), "arbeitszimmer": ("buro",),
    "gast": ("gast",), "gastezimmer": ("gast",),
    "stiegenhaus": ("stiegenhaus",), "stg": ("stiegenhaus",),
    "stgh": ("stiegenhaus",), "stiege": ("stiegenhaus",),
    "treppenhaus": ("stiegenhaus",), "treppe": ("stiegenhaus",),
    "podest": ("podest",), "lift": ("lift",), "aufzug": ("lift",),
    "terrasse": ("terrasse",), "balkon": ("balkon",), "loggia": ("loggia",),
    "garten": ("garten",), "lichthof": ("lichthof",),
    "spielplatz": ("spielplatz",), "nebenanlage": ("nebenanlage",),
    "fahrradraum": ("fahrrad",), "radraum": ("fahrrad",),
    "fahrradplatze": ("fahrrad",), "fahrradabstellplatze": ("fahrrad",),
    "mullraum": ("mull",), "waschkuche": ("waschkuche",),
    "galerie": ("galerie",), "schrankraum": ("schrankraum",),
}
_OUTDOOR_WORDS = frozenset({"terrasse", "balkon", "loggia", "garten", "lichthof",
                            "spielplatz", "nebenanlage"})


# ---------------------------------------------------------------------------
# Namen zerlegen
# ---------------------------------------------------------------------------


def _fold(s: str | None) -> str:
    return (s or "").translate(_UMLAUT).lower()


@dataclass(frozen=True)
class ParsedName:
    unit: str | None          # "u1" | "general" | None
    room_no: str | None       # "1.4"
    words: frozenset          # kanonische Raumwörter (+ "#2" für "Zimmer 2")
    generic: bool             # kein verwertbarer Name ("Raum", leer)
    has_letters: bool         # außer Einheit/Technik überhaupt Text übrig


def parse_name(raw: str | None, unit_hint: str | None = None) -> ParsedName:
    """Name (+ optionaler Einheiten-Name) → Einheit, Raumnummer, Raumwörter.

    Die Einheit im NAMEN gewinnt ("Lichthof TOP 1" unter "Allgemein"), sonst
    gilt ``unit_hint``. Klammerzusätze und alles ab "inkl." zählen nicht zum
    Namen: "Garten TOP 1 (inkl. Terrasse, Lichthof)" ist ein Garten, kein
    Lichthof.
    """
    s = _fold(raw)
    unit = room_no = None
    m = _UNIT_RE.search(s)
    if m:
        unit = f"u{int(m.group(1))}"
        if m.group(2):
            room_no = f"{int(m.group(1))}.{int(m.group(2))}"
        s = s[:m.start()] + " " + s[m.end():]
    if unit is None and unit_hint is not None:
        hint = _fold(unit_hint)
        hm = _UNIT_RE.search(hint)
        if hm:
            unit = f"u{int(hm.group(1))}"
        elif any(k in hint for k in _GENERAL_UNIT_KW):
            unit = "general"
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.split(r"\binkl\b", s)[0]
    s = _A_FLAECHE_RE.sub(" abstellraum ", s)
    s = _clean_name(s)
    tokens = re.findall(r"[a-z]+|\d+", s.lower())
    words: set[str] = set()
    prev_room_word = False
    fallback: set[str] = set()
    for t in tokens:
        if t.isdigit():
            if prev_room_word:
                words.add(f"#{int(t)}")
            prev_room_word = False
            continue
        canon = _ROOM_WORDS.get(t)
        if canon:
            words.update(canon)
            prev_room_word = True
        else:
            prev_room_word = False
            if len(t) >= 3:
                fallback.add(t)
    has_letters = bool(words or fallback)
    if not any(not w.startswith("#") for w in words):
        # Kein bekanntes Raumwort: auf die übrigen Wörter ausweichen, damit
        # z. B. zwei gleich benannte Sonderräume trotzdem zusammenfinden.
        words = fallback
    generic = not words or words <= {"raum"}
    return ParsedName(unit, room_no, frozenset(words), generic, has_letters)


def _is_sum_name(raw: str, parsed: ParsedName) -> bool:
    low = _fold(raw)
    if any(k in low for k in _SUM_KW):
        return True
    # Nur eine Einheit, sonst nichts ("TOP 3" über "28,07m2").
    return parsed.unit is not None and not parsed.has_letters


# ---------------------------------------------------------------------------
# Stempel aus dem Textlayer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stamp:
    idx: int
    raw: str
    area: float
    x: float                  # Mittelpunkt des ganzen Stempels (PDF-Punkte)
    y: float
    kind: str                 # "room" | "outdoor" | "sum" | "anon"
    unit: str | None
    room_no: str | None
    words: frozenset
    table: bool = False       # Zeile einer Raumtabelle (Name links neben der Zahl)

    @property
    def assignable(self) -> bool:
        return self.kind in ("room", "outdoor")


def _is_belag_line(text: str) -> bool:
    low = text.strip().lower()
    return low.startswith("b:") or any(k in low for k in _BELAG_KW)


def parse_stamps(page_text_dict: dict) -> list[Stamp]:
    """Flächen-Stempel aus ``page.get_text("dict")`` — blockbasiert.

    Der Name eines Stempels sind die Zeilen im SELBEN Textblock zwischen der
    vorigen und dieser m²-Zeile (ohne Belag- und Umfangszeilen). CAD-Exporte
    legen einen Stempel als einen Block ab ("TOP 2.1 | Vorraum | B: Feinsteinz.
    | F: 2,93 m2"); so fließen keine Nachbar-Texte wie "STUK +2,20" oder
    Kanal-Beschriftungen in den Namen. Steht im Block kein Name (Zahl als
    eigener Block), wird die Nachbarschafts-Ernte aus ``harvest_stamps``
    übernommen.

    Raumtabellen (Name LINKS neben der Zahl, gleiche Zeilenhöhe) liefern
    dieselben Werte noch einmal. Hat eine Tabellenzeile einen gleichnamigen,
    gleich großen Stempel in der Zeichnung, wird sie verworfen — sonst hätte
    jeder Raum zwei Kandidaten und die Namens-Zuordnung wäre nie eindeutig.
    Tabellenzeilen ohne Gegenstück bleiben als Quelle erhalten.
    """
    fallback_names = {
        (round(s["x"], 1), round(s["y"], 1)): s["name"]
        for s in harvest_stamps(page_text_dict)
    }
    parsed_stamps: list[Stamp] = []
    for b in (page_text_dict or {}).get("blocks", []):
        if b.get("type") != 0:
            continue
        pending: list[tuple[str, tuple]] = []
        for ln in b.get("lines", []):
            text = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
            if not text:
                continue
            bb = tuple(ln["bbox"])
            m = _AREA_RE.match(text)
            if not m:
                pending.append((text, bb))
                continue
            name_lines = [
                (t, lbb) for t, lbb in pending
                if re.search(r"[A-Za-zÄÖÜäöüß]", t)
                and not _AREA_RE.match(t)
                and not _PERIMETER_RE.match(t)
                and not _is_belag_line(t)
            ]
            pending = []
            ax, ay = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            table = bool(name_lines) and all(
                lbb[2] <= bb[0] and lbb[1] <= ay <= lbb[3] for _, lbb in name_lines
            )
            if name_lines:
                raw = " ".join(t for t, _ in name_lines)
                boxes = [lbb for _, lbb in name_lines] + [bb]
                cx = (min(v[0] for v in boxes) + max(v[2] for v in boxes)) / 2
                cy = (min(v[1] for v in boxes) + max(v[3] for v in boxes)) / 2
            else:
                raw = fallback_names.get((round(ax, 1), round(ay, 1)), ANON_STAMP_NAME)
                cx, cy = ax, ay
            try:
                area = _to_float(m.group(1))
            except ValueError:
                continue
            parsed = parse_name(raw)
            if raw == ANON_STAMP_NAME:
                kind = "anon"
            elif _is_sum_name(raw, parsed):
                kind = "sum"
            elif parsed.generic:
                kind = "anon"
            elif parsed.words & _OUTDOOR_WORDS:
                kind = "outdoor"
            else:
                kind = "room"
            parsed_stamps.append(Stamp(0, raw, area, cx, cy, kind, parsed.unit,
                                       parsed.room_no, parsed.words, table))

    def _twin(row: Stamp, other: Stamp) -> bool:
        return (not other.table and other.words == row.words
                and _exact(other.area, row.area)
                and (row.unit is None or other.unit is None or row.unit == other.unit))

    kept = [
        s for s in parsed_stamps
        if not (s.table and s.words and any(_twin(s, o) for o in parsed_stamps))
    ]
    return [
        Stamp(i, s.raw, s.area, s.x, s.y, s.kind, s.unit, s.room_no, s.words, s.table)
        for i, s in enumerate(kept)
    ]


def assess_trust(
    *, trace: list[dict] | None, image_coverage: float, stamps: list[Stamp],
) -> str | None:
    """``None`` = Textlayer vertrauenswürdig, sonst der Grund dagegen.

    Reihenfolge = Diagnose-Priorität. Jeder Zweifel führt zu "nicht
    vertrauenswürdig" — ein fehlender Wert fällt auf, ein falscher nicht.
    """
    if trace is None:
        return "texttrace_unavailable"
    if not trace:
        return "no_text"
    if any(t.get("type") == _INVISIBLE_TEXT_TYPE for t in trace):
        return "invisible_text"
    if any((t.get("opacity") is not None and float(t["opacity"]) <= 0.0) for t in trace):
        return "invisible_text"
    fonts = {_fold(str(t.get("font") or "")) for t in trace}
    if any(h in f for f in fonts for h in _OCR_FONT_HINTS):
        return "ocr_font"
    if image_coverage >= _MAX_RASTER_COVERAGE:
        return "raster_image"
    if sum(1 for s in stamps if s.assignable) < _MIN_NAMED_STAMPS:
        return "too_few_stamps"
    return None


@dataclass
class PageTextlayer:
    stamps: list[Stamp]
    trust_reason: str | None
    width: float
    height: float
    rotation: int = 0

    @property
    def trusted(self) -> bool:
        return self.trust_reason is None


def read_page_textlayer(doc, page_number: int) -> PageTextlayer:
    """fitz-Zugriff für eine Seite. MUSS im fitz-Executor laufen."""
    page = doc[page_number - 1]
    width, height = float(page.rect.width), float(page.rect.height)
    stamps = parse_stamps(page.get_text("dict"))
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001 — ohne Trace keine OCR-Prüfung → Scan
        trace = None
    page_area = width * height
    coverage = 0.0
    if page_area > 0:
        for info in page.get_image_info():
            x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
            coverage = max(coverage, abs(x1 - x0) * abs(y1 - y0) / page_area)
    reason = assess_trust(trace=trace, image_coverage=coverage, stamps=stamps)
    return PageTextlayer(stamps, reason, width, height, int(page.rotation or 0))


# ---------------------------------------------------------------------------
# Zuordnung
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Room:
    idx: int
    ui: int
    ri: int
    label: str
    unit: str | None
    number: str | None
    words: frozenset
    generic: bool
    area: float | None
    pin: tuple[float, float] | None


@dataclass
class RoomDecision:
    ui: int
    ri: int
    label: str
    action: str               # confirm | fill | correct | conflict | sum_cleared | unmatched
    step: str | None          # number | text | elimination | position | value
    vision_area: float | None
    stamp: str | None = None
    stamp_area: float | None = None

    @property
    def new_area(self) -> float | None:
        if self.action in ("fill", "correct"):
            return self.stamp_area
        if self.action == "sum_cleared":
            return None
        return self.vision_area


@dataclass
class AreaReport:
    page: int
    policy: str
    trust_reason: str | None
    stamps_total: int = 0
    stamps_assignable: int = 0
    rooms_total: int = 0
    pins_usable: int = 0
    decisions: list[RoomDecision] = field(default_factory=list)
    orphan_stamps: list[str] = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for d in self.decisions if d.action == action)


def _area(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _room_number(raw, unit: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw)
    m = re.search(r"(\d+)\s*[./]\s*(\d+)", s)
    if m:
        return f"{int(m.group(1))}.{int(m.group(2))}"
    m = re.fullmatch(r"\s*(\d+)\s*", s)
    if m and unit and unit.startswith("u"):
        return f"{unit[1:]}.{int(m.group(1))}"
    return None


def _pin_to_pdf(room: dict, frame: dict | None) -> tuple[float, float] | None:
    if not frame:
        return None
    try:
        px, py = float(room["position_x"]), float(room["position_y"])
        iw, ih = float(frame["image_w"]), float(frame["image_h"])
        x0, y0, x1, y1 = (float(frame[k]) for k in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError):
        return None
    if iw <= 0 or ih <= 0 or not (0 <= px <= iw and 0 <= py <= ih):
        return None
    return x0 + px / iw * (x1 - x0), y0 + py / ih * (y1 - y0)


def _collect_rooms(result: dict, frame: dict | None) -> list[_Room]:
    rooms: list[_Room] = []
    for ui, unit in enumerate((result or {}).get("units") or []):
        if not isinstance(unit, dict):
            continue
        unit_name = unit.get("unit_name")
        for ri, room in enumerate(unit.get("rooms") or []):
            if not isinstance(room, dict):
                continue
            name = room.get("room_name")
            parsed = parse_name(name, unit_hint=unit_name)
            rooms.append(_Room(
                idx=len(rooms), ui=ui, ri=ri,
                label=f"{unit_name or '-'} / {name or '-'}",
                unit=parsed.unit,
                number=_room_number(room.get("room_number"), parsed.unit) or parsed.room_no,
                words=parsed.words, generic=parsed.generic,
                area=_area(room.get("area_m2")),
                pin=_pin_to_pdf(room, frame),
            ))
    return rooms


def _unit_tier(room_unit: str | None, stamp_unit: str | None) -> int | None:
    if room_unit is not None and room_unit.startswith("u"):
        if stamp_unit is None:
            return 1
        return 0 if stamp_unit == room_unit else None
    if room_unit == "general":
        return 1 if stamp_unit is None else None
    return 1


def _name_tier(room_words: frozenset, stamp_words: frozenset) -> int | None:
    """0 = gleicher Name, 1 = Vision-Name ist Teil des Stempelnamens
    ("Stiegenhaus" ↔ "Stg/Vr verglast", "Wohnen" ↔ "WOHNEN / KOCHEN").

    Bewusst NICHT umgekehrt: ein Vision-Name, der mehr umfasst als der Stempel
    ("A Fläche / Wohnen", "Bad/WC"), ist ein zusammengelegter Raum — der
    Stempel deckt nur einen Teil davon ab (gemessen: GWM-Lauf, in dem der
    Mischraum sonst dem echten "Wohnen" den Stempel weggenommen hätte)."""
    if not room_words or not stamp_words:
        return None
    if room_words == stamp_words:
        return 0
    if room_words < stamp_words:
        return 1
    return None


def _exact(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= _EXACT_TOL


class _Matcher:
    def __init__(self, rooms: list[_Room], stamps: list[Stamp], diag: float):
        self.rooms = rooms
        self.stamps = [s for s in stamps if s.assignable]
        self.by_idx = {s.idx: s for s in self.stamps}
        self.diag = diag
        self.assigned: dict[int, tuple[int, str]] = {}   # room idx → (stamp idx, step)
        self.consumed: set[int] = set()
        self.text_rounds = 0

    # -- Kandidaten -------------------------------------------------------
    def group(self, room: _Room) -> list[Stamp]:
        """Freie Stempel der BESTEN Passungs-Stufe (siehe Modul-Docstring)."""
        if room.generic:
            return []
        best = None
        tiers: list[tuple[tuple[int, int], Stamp]] = []
        for s in self.stamps:
            ut = _unit_tier(room.unit, s.unit)
            nt = _name_tier(room.words, s.words)
            if ut is None or nt is None:
                continue
            key = (ut, nt)
            tiers.append((key, s))
            if best is None or key < best:
                best = key
        return [s for key, s in tiers if key == best and s.idx not in self.consumed]

    def open_rooms(self) -> list[_Room]:
        return [r for r in self.rooms if r.idx not in self.assigned]

    def _dist(self, room: _Room, stamp: Stamp) -> float:
        return math.hypot(room.pin[0] - stamp.x, room.pin[1] - stamp.y)

    # -- Vergabe ----------------------------------------------------------
    def commit(self, proposals: dict[int, int], step: str) -> int:
        """Nur eindeutige Vorschläge übernehmen. Wollen mehrere Räume denselben
        Stempel, gewinnt der einzige mit exakt gelesenem Wert, sonst der
        einzige klar nächste Pin — sonst niemand."""
        by_stamp: dict[int, list[int]] = defaultdict(list)
        for r_idx, s_idx in proposals.items():
            by_stamp[s_idx].append(r_idx)
        n = 0
        for s_idx, r_idxs in by_stamp.items():
            stamp = self.by_idx[s_idx]
            winner = None
            if len(r_idxs) == 1:
                winner = r_idxs[0]
            else:
                exact = [i for i in r_idxs if _exact(self.rooms[i].area, stamp.area)]
                if len(exact) == 1:
                    winner = exact[0]
                elif all(self.rooms[i].pin for i in r_idxs):
                    ranked = sorted(r_idxs, key=lambda i: self._dist(self.rooms[i], stamp))
                    d1 = self._dist(self.rooms[ranked[0]], stamp)
                    d2 = self._dist(self.rooms[ranked[1]], stamp)
                    if d2 >= _POSITION_RATIO * d1:
                        winner = ranked[0]
            if winner is not None:
                self.assigned[winner] = (s_idx, step)
                self.consumed.add(s_idx)
                n += 1
        return n

    def step_number(self) -> None:
        proposals = {}
        for r in self.open_rooms():
            if not r.number:
                continue
            hits = [
                s for s in self.stamps
                if s.idx not in self.consumed and s.room_no == r.number
                and _unit_tier(r.unit, s.unit) is not None
                and (r.generic or _name_tier(r.words, s.words) is not None)
            ]
            if len(hits) == 1:
                proposals[r.idx] = hits[0].idx
        self.commit(proposals, "number")

    def step_text(self) -> None:
        while True:
            proposals = {}
            for r in self.open_rooms():
                g = self.group(r)
                if len(g) == 1:
                    proposals[r.idx] = g[0].idx
            step = "text" if self.text_rounds == 0 else "elimination"
            self.text_rounds += 1
            if not proposals or not self.commit(proposals, step):
                return

    def step_position(self) -> None:
        max_d = _POSITION_MAX_DIAG_FRAC * self.diag
        proposals = {}
        for r in self.open_rooms():
            if r.pin is None:
                continue
            g = sorted(self.group(r), key=lambda s: self._dist(r, s))
            if not g:
                continue
            d1 = self._dist(r, g[0])
            d2 = self._dist(r, g[1]) if len(g) > 1 else math.inf
            if d1 <= max_d and d2 >= _POSITION_RATIO * d1:
                proposals[r.idx] = g[0].idx
        self.commit(proposals, "position")

    def step_value(self) -> None:
        proposals = {}
        same_value_groups: dict[tuple[int, ...], list[_Room]] = defaultdict(list)
        for r in self.open_rooms():
            g = self.group(r)
            if len(g) < 2:
                continue
            areas = [s.area for s in g]
            if max(areas) - min(areas) <= _EXACT_TOL:
                # Alle Kandidaten gleich groß (gespiegelte Wohnungen, "AR 2,00"
                # zweimal): welcher Stempel es ist, ändert die Fläche nicht.
                same_value_groups[tuple(sorted(s.idx for s in g))].append(r)
                continue
            if r.area is None:
                continue
            ranked = sorted(g, key=lambda s: abs(r.area - s.area))
            d1 = abs(r.area - ranked[0].area)
            d2 = abs(r.area - ranked[1].area)
            if (d1 <= _VALUE_MAX_REL * ranked[0].area
                    and d2 >= _VALUE_SECOND_FACTOR * d1 + _VALUE_SECOND_ABS):
                proposals[r.idx] = ranked[0].idx
        self.commit(proposals, "value")
        for stamp_ids, rooms in same_value_groups.items():
            free = [i for i in stamp_ids if i not in self.consumed]
            rooms = [r for r in rooms if r.idx not in self.assigned]
            if rooms and len(rooms) <= len(free):
                for r, s_idx in zip(rooms, free):
                    self.assigned[r.idx] = (s_idx, "value")
                    self.consumed.add(s_idx)

    # -- Schutzregel ------------------------------------------------------
    def conflicts(self) -> set[int]:
        out: set[int] = set()
        for r_idx, (s_idx, _step) in self.assigned.items():
            room, stamp = self.rooms[r_idx], self.by_idx[s_idx]
            if _exact(room.area, stamp.area):
                continue
            if self._pin_confirms(room, stamp):
                continue
            # (B) Dieser Raum hat exakt den Wert eines ANDEREN Stempels gleichen
            #     Namens gelesen.
            if room.area is not None and any(
                o.idx != s_idx and _exact(o.area, room.area)
                and not _exact(o.area, stamp.area)
                and _name_tier(room.words, o.words) is not None
                for o in self.stamps
            ):
                out.add(r_idx)
                continue
            # (A) Ein anderer Raum gleichen Namens hat exakt diesen Stempelwert
            #     gelesen, ohne dass sein eigener Stempel das erklärt.
            for other in self.rooms:
                if other.idx == r_idx or not _exact(other.area, stamp.area):
                    continue
                if _name_tier(other.words, stamp.words) is None:
                    continue
                own = self.assigned.get(other.idx)
                if own is not None and _exact(self.by_idx[own[0]].area, other.area):
                    continue
                out.add(r_idx)
                break
        return out

    def _pin_confirms(self, room: _Room, stamp: Stamp) -> bool:
        if room.pin is None:
            return False
        same_name = [s for s in self.stamps if _name_tier(room.words, s.words) is not None]
        ranked = sorted(same_name, key=lambda s: self._dist(room, s))
        if not ranked or ranked[0].idx != stamp.idx:
            return False
        d1 = self._dist(room, ranked[0])
        d2 = self._dist(room, ranked[1]) if len(ranked) > 1 else math.inf
        return d1 <= _POSITION_MAX_DIAG_FRAC * self.diag and d2 >= _POSITION_RATIO * d1


def assign_areas(
    result: dict,
    textlayer: PageTextlayer,
    *,
    page: int = 1,
    frame: dict | None = None,
    policy: str = "cautious",
) -> AreaReport:
    """Rein: entscheidet pro Vision-Raum, was mit seiner Fläche passiert.
    Ändert ``result`` NICHT — das macht ``apply_report``."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    if textlayer.rotation:
        frame = None   # gedrehte Seiten: Pin↔Punkt-Umrechnung nicht verlässlich
    report = AreaReport(page=page, policy=policy, trust_reason=textlayer.trust_reason,
                        stamps_total=len(textlayer.stamps),
                        stamps_assignable=sum(1 for s in textlayer.stamps if s.assignable))
    rooms = _collect_rooms(result, frame)
    report.rooms_total = len(rooms)
    report.pins_usable = sum(1 for r in rooms if r.pin is not None)
    if not textlayer.trusted:
        report.decisions = [
            RoomDecision(r.ui, r.ri, r.label, "unmatched", None, r.area) for r in rooms
        ]
        return report

    diag = math.hypot(textlayer.width, textlayer.height)
    m = _Matcher(rooms, textlayer.stamps, diag)
    m.step_number()
    m.step_text()
    m.step_position()
    m.step_text()
    m.step_value()
    m.step_text()
    conflicts = m.conflicts() if policy == "cautious" else set()

    sum_values = [s.area for s in textlayer.stamps if s.kind == "sum"]
    room_values = [s.area for s in m.stamps]
    for r in rooms:
        hit = m.assigned.get(r.idx)
        if hit is not None:
            stamp = m.by_idx[hit[0]]
            if r.idx in conflicts:
                action = "conflict"
            elif r.area is None:
                action = "fill"
            elif _exact(r.area, stamp.area):
                action = "confirm"
            else:
                action = "correct"
            report.decisions.append(RoomDecision(
                r.ui, r.ri, r.label, action, hit[1], r.area, stamp.raw, stamp.area))
            continue
        action = "unmatched"
        if (r.area is not None
                and any(_exact(r.area, v) for v in sum_values)
                and not any(_exact(r.area, v) for v in room_values)):
            action = "sum_cleared"
        report.decisions.append(RoomDecision(r.ui, r.ri, r.label, action, None, r.area))
    report.orphan_stamps = [
        f"{s.raw} ({s.area:.2f})" for s in m.stamps if s.idx not in m.consumed
    ]
    return report


def apply_report(result: dict, report: AreaReport) -> int:
    """Schreibt die Entscheidungen in ``result`` zurück. Liefert die Zahl der
    geänderten Flächen. Räume werden weder angelegt noch entfernt."""
    changed = 0
    units = (result or {}).get("units") or []
    for d in report.decisions:
        if d.action not in ("fill", "correct", "sum_cleared"):
            continue
        try:
            room = units[d.ui]["rooms"][d.ri]
        except (IndexError, KeyError, TypeError):
            continue
        room["area_m2"] = d.new_area
        changed += 1
    return changed


def format_log(report: AreaReport, plan_id, mode: str) -> str:
    head = (
        f"textlayer_areas plan={plan_id} page={report.page} mode={mode} "
        f"policy={report.policy} trusted={report.trust_reason is None} "
        f"reason={report.trust_reason} stamps={report.stamps_total} "
        f"assignable={report.stamps_assignable} rooms={report.rooms_total} "
        f"pins={report.pins_usable} confirm={report.count('confirm')} "
        f"fill={report.count('fill')} correct={report.count('correct')} "
        f"conflict={report.count('conflict')} sum_cleared={report.count('sum_cleared')} "
        f"unmatched={report.count('unmatched')} orphan_stamps={len(report.orphan_stamps)}"
    )
    details = [
        f"{d.action}:{d.label}={d.vision_area}->{d.new_area}"
        for d in report.decisions if d.action in ("correct", "conflict", "sum_cleared")
    ]
    if details:
        head += " changes=" + "; ".join(details[:20])
    if report.orphan_stamps:
        head += " orphans=" + "; ".join(report.orphan_stamps[:10])
    return head


def process_page(
    doc, page_number: int, result: dict, frame: dict | None, mode: str,
    policy: str = "cautious",
) -> AreaReport:
    """Textlayer lesen, zuordnen und — nur bei ``mode == "apply"`` — schreiben.
    Läuft komplett im fitz-Executor (Textlayer-Zugriff + reine Rechnung)."""
    textlayer = read_page_textlayer(doc, page_number)
    report = assign_areas(result, textlayer, page=page_number, frame=frame, policy=policy)
    if mode == "apply":
        apply_report(result, report)
    return report

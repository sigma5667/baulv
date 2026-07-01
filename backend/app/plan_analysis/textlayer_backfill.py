"""v24.5 — Stufe 4a: TROCKENLAUF (measure-only) für den PDF-Textlayer-Backfill.

Reines Mess-/Diagnose-Modul. Es schreibt NICHTS, ändert das Vision-Ergebnis
NICHT und berührt weder DB noch Frontend. Es wird ausschliesslich aufgerufen,
wenn ``settings.textlayer_backfill_mode != "off"`` (Default: ``off`` → dieses
Modul wird dann nicht einmal importiert).

Zweck: an echten Analysen die ENTSCHEIDENDE Zahl loggen — von den Räumen, die
die Vision-KI ohne Fläche zurückgibt (``area_m2 = null`` → später „unvollständig"),
wie viele hätten wir aus dem PDF-Textlayer sicher nachfüllen können. Aufschlüsselung
``by_name`` / ``by_position`` / ``uncertain`` / ``no_match`` plus die reale
Pin-Null-Rate.

Zuordnungslogik = die in Stufe 1–3 an echten Plänen bewiesene: Name-Match zuerst;
die Pin-Position wird nur zum Trennen mehrfach gleicher Namen gebraucht; im Zweifel
„uncertain" statt eines falschen Werts (Marge-Gate). Für den reinen Trockenlauf
genügt zur Klassifikation: eindeutiger Name → ``by_name``; Duplikat-Name mit
vorhandenem Pin → ``by_position``; Duplikat-Name ohne Pin → ``uncertain``;
kein Namens-/Texttreffer → ``no_match``. (Die exakte Pixel↔Punkt-Positions-
Prüfung folgt erst im schreibenden 4b, wo der Transform vorliegt.)

Alle Funktionen sind rein und ohne fitz-/DB-Import — Eingabe ist das bereits
extrahierte ``page.get_text("dict")`` und das Vision-Ergebnis-dict.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

# --- Erkennungs-/Bereinigungsmuster (portiert aus den Stufe-3-Skripten) ----
_AREA_RE = re.compile(r"^\s*(?:F\s*[:=]\s*)?(\d+(?:[.,]\d+)?)\s*m\s*[²2]\s*$", re.I)
_BELAG_KW = ("parkett", "feinsteinzeug", "fliesen", "laminat", "vinyl", "teppich",
             "linoleum", "naturstein", "betonplatten", "beton", "estrich",
             "boden", "dielen")
_TECH_TOKEN_RE = re.compile(
    r"\b(?:LH|UK|OK|FFOK|FOK|RFB|FFB|STUK|FPH|DDB|WS|WD|DN|RS|KS|TH|BRH|EI\d*C?)\b"
    r"\s*[=:]?\s*[+\-]?\d*[.,]?\d*\s*(?:cm|m)?", re.I)
_KOTE_RE = re.compile(r"[+\-]\d+[.,]\d+")
_NONROOM_KW = ("balkon", "terrasse", "garten", "loggia", "spielplatz", "nebenanlage",
               "stiegenhaus", "stgh", "stg ", "podest", "lift", "vorplatz")


def _to_float(s: str) -> float:
    s = s.strip()
    return float(s.replace(".", "").replace(",", ".")) if s.count(",") else float(s.replace(",", "."))


def _clean_name(raw: str) -> str:
    t = " " + (raw or "") + " "
    t = _TECH_TOKEN_RE.sub(" ", t)
    t = _KOTE_RE.sub(" ", t)
    t = re.sub(r"\b[BFU]\s*[:=]\s*", " ", t, flags=re.I)  # B:/F:/U:-Präfixe
    for kw in sorted(_BELAG_KW, key=len, reverse=True):
        t = re.sub(rf"\b{kw}\w*\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" .:")


def _match_key(raw: str) -> str:
    return re.sub(r"\s+", " ", _clean_name(raw).lower()).strip()


def _core_key(raw: str) -> str:
    """Kern-Name ohne TOP-x.y-Präfix — konservativer Worst-Case (Kollisionen)."""
    k = re.sub(r"\btop\s*\d+(?:[.,]\d+)?\b", " ", _match_key(raw), flags=re.I)
    return re.sub(r"\s+", " ", k).strip()


def _is_non_room(raw: str) -> bool:
    low = (raw or "").lower()
    return any(k in low for k in _NONROOM_KW)


@dataclass(frozen=True)
class MeasureStats:
    page: int
    text_stamps: int          # alle „…m²"-Stempel im Textlayer
    room_stamps: int          # davon echte Räume (Nicht-Räume gefiltert)
    no_text_layer: bool       # True → gescanntes/rasterisiertes PDF
    rooms_total: int          # Vision-Räume auf dieser Seite
    pins_present: int         # davon mit position_x
    rooms_incomplete: int     # Vision-Räume ohne Fläche (Backfill-Ziele)
    by_name: int              # davon: sicher per eindeutigem Namen
    by_position: int          # davon: Duplikat-Name, aber Pin vorhanden
    uncertain: int            # davon: Duplikat-Name ohne Pin
    no_match: int             # davon: kein Namens-/Texttreffer


def harvest_stamps(page_text_dict: dict) -> list[dict]:
    """Aus ``page.get_text("dict")`` die Flächen-Stempel ziehen (PDF-Punkte).

    Liefert ``[{name, area, x, y, nonroom}]``. Rein, kein fitz-/IO-Zugriff.
    """
    lines: list[dict] = []
    for b in (page_text_dict or {}).get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
            if txt:
                lines.append({"t": txt, "bb": tuple(ln["bbox"])})

    def hov(a, b):
        return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / max(1e-6, min(a[2]-a[0], b[2]-b[0]))

    hs = sorted(l["bb"][3] - l["bb"][1] for l in lines)
    lh = hs[len(hs)//2] if hs else 10.0

    def _is_belag_line(s):
        body = re.sub(r"^\s*[bB]\s*[:=]\s*", "", s)
        return s.strip().lower().startswith("b:") or any(k in body.lower() for k in _BELAG_KW)

    out: list[dict] = []
    for al in lines:
        m = _AREA_RE.match(al["t"])
        if not m:
            continue
        ab = al["bb"]
        above = [(ab[1]-o["bb"][1], o) for o in lines
                 if o is not al and hov(ab, o["bb"]) >= 0.30 and 0 < ab[1]-o["bb"][1] <= lh*3.2]
        above.sort(key=lambda x: x[0])
        parts = [o["t"] for _, o in above
                 if re.search(r"[A-Za-zÄÖÜäöüß]", o["t"])
                 and not _AREA_RE.match(o["t"]) and not _is_belag_line(o["t"])]
        raw = " ".join(reversed(parts)) if parts else "(ohne Namen)"
        out.append({"name": raw, "area": _to_float(m.group(1)),
                    "x": (ab[0]+ab[2])/2, "y": (ab[1]+ab[3])/2, "nonroom": _is_non_room(raw)})
    return out


def classify(result: dict, stamps: list[dict], page: int = 1) -> MeasureStats:
    """Reine Klassifikation. Liest ``result`` NUR — keine Mutation, kein IO."""
    room_stamps = [s for s in stamps if not s["nonroom"]]
    by_full: dict[str, int] = defaultdict(int)
    by_core: dict[str, int] = defaultdict(int)
    for s in room_stamps:
        by_full[_match_key(s["name"])] += 1
        by_core[_core_key(s["name"])] += 1

    total = pins = incomplete = bn = bp = unc = nm = 0
    for unit in (result or {}).get("units", []) or []:
        for r in (unit.get("rooms", []) or []):
            total += 1
            if r.get("position_x") is not None:
                pins += 1
            area = r.get("area_m2")
            if area is not None and float(area) > 0:
                continue  # hat Fläche → kein Backfill-Ziel
            incomplete += 1
            if not room_stamps:            # kein Textlayer / keine Raumstempel
                nm += 1
                continue
            name = r.get("room_name") or ""
            if by_full.get(_match_key(name), 0) == 1:
                bn += 1
                continue
            c = by_core.get(_core_key(name), 0)
            if c == 0:
                nm += 1
            elif c == 1:
                bn += 1
            elif r.get("position_x") is not None:
                bp += 1
            else:
                unc += 1

    return MeasureStats(
        page=page, text_stamps=len(stamps), room_stamps=len(room_stamps),
        no_text_layer=(len(room_stamps) == 0), rooms_total=total, pins_present=pins,
        rooms_incomplete=incomplete, by_name=bn, by_position=bp,
        uncertain=unc, no_match=nm,
    )


def measure_page(page_text_dict: dict, result: dict, page: int = 1) -> MeasureStats:
    """Convenience: harvest + classify. Ändert nichts, schreibt nichts."""
    return classify(result or {}, harvest_stamps(page_text_dict or {}), page)


def format_log(st: MeasureStats, plan_id) -> str:
    pin_null = st.rooms_total - st.pins_present
    rate = (100.0 * pin_null / st.rooms_total) if st.rooms_total else 0.0
    backfillable = st.by_name + st.by_position
    return (
        f"textlayer_backfill.measure plan={plan_id} page={st.page} "
        f"text_stamps={st.text_stamps} room_stamps={st.room_stamps} "
        f"no_text_layer={st.no_text_layer} rooms_total={st.rooms_total} "
        f"rooms_incomplete={st.rooms_incomplete} backfillable={backfillable} "
        f"by_name={st.by_name} by_position={st.by_position} uncertain={st.uncertain} "
        f"no_match={st.no_match} pin_null_rate={rate:.0f}% "
        f"(pins_present={st.pins_present}/{st.rooms_total})"
    )

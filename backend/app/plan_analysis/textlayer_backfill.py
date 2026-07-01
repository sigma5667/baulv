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

import math
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


# Mindest-Abstand (normiert) zum zweitnächsten Stempel, damit eine
# Positions-Zuordnung als sicher gilt (Marge-Gate). 0.05 ≈ 3× des in Stufe 2
# gemessenen realistischen Pin-Rauschens.
_POSITION_MARGIN = 0.05


def _is_generic_name(raw: str) -> bool:
    """True wenn Vision keinen echten Namen lesen konnte (Prompt-Fallback
    „Raum" / „Raum links unten") oder der Name leer ist."""
    c = _core_key(raw)
    return c == "" or c == "raum" or c.startswith("raum ")


def _norm_fn(points):
    """Normierungsfunktion (x, y) -> [0,1]^2 über den Extent der Punkte, oder
    None wenn < 2 Punkte (kein Extent bestimmbar). Extent-basiert, damit man
    Pins (Bildpixel) und Stempel (PDF-Punkte) OHNE gespeicherten Transform in
    einen gemeinsamen relativen Rahmen bringt (Schätzung für den Trockenlauf)."""
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = (x1 - x0) or 1.0
    sy = (y1 - y0) or 1.0
    return lambda x, y: ((x - x0) / sx, (y - y0) / sy)


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
    # v24.5b — Positions-Rettung für namenlose Fälle:
    no_match_but_has_pin: int = 0        # no_match-Räume mit Pin (rettbar-Kandidaten)
    position_first_recoverable: int = 0  # davon per reiner Position + Marge rettbar
    unmatched_names: tuple = ()          # Vision-Raumnamen der no_match/uncertain-Fälle


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
    # Name -> Liste der Stempel-INDIZES (für Consumption in der Positions-Sim).
    by_full: dict[str, list[int]] = defaultdict(list)
    by_core: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(room_stamps):
        by_full[_match_key(s["name"])].append(i)
        by_core[_core_key(s["name"])].append(i)

    all_rooms = [r for unit in (result or {}).get("units", []) or []
                 for r in (unit.get("rooms", []) or [])]
    total = len(all_rooms)
    pins = sum(1 for r in all_rooms if r.get("position_x") is not None)

    # Gemeinsamer relativer Rahmen (Extent-Normierung, KEIN Transform nötig).
    stamp_pts = [(s["x"], s["y"]) for s in room_stamps]
    pin_pts = [(r["position_x"], r["position_y"]) for r in all_rooms
               if r.get("position_x") is not None and r.get("position_y") is not None]
    stamp_norm = _norm_fn(stamp_pts)
    pin_norm = _norm_fn(pin_pts)
    stamp_np = [stamp_norm(x, y) for x, y in stamp_pts] if stamp_norm else []

    incomplete = bn = bp = unc = nm = nm_pin = 0
    unmatched_names: list[str] = []
    consumed: set[int] = set()          # von eindeutigen Namens-Treffern belegt
    generic_pin_rooms: list[dict] = []  # namenlose Fälle mit Pin -> Positions-Sim

    for r in all_rooms:
        area = r.get("area_m2")
        if area is not None and float(area) > 0:
            continue  # hat Fläche → kein Backfill-Ziel
        incomplete += 1
        has_pin = r.get("position_x") is not None
        name = r.get("room_name") or ""
        if not room_stamps:               # kein Textlayer / keine Raumstempel
            nm += 1
            if has_pin:
                nm_pin += 1
            unmatched_names.append(name or "(leer)")
            continue
        full = by_full.get(_match_key(name), [])
        if len(full) == 1:
            bn += 1
            consumed.add(full[0])
            continue
        cand = by_core.get(_core_key(name), [])
        if len(cand) == 1:
            bn += 1
            consumed.add(cand[0])
            continue
        if len(cand) == 0:
            nm += 1
            unmatched_names.append(name or "(leer)")
            if has_pin:
                nm_pin += 1
                if _is_generic_name(name):
                    generic_pin_rooms.append(r)
            continue
        # Duplikat-Name (mehrere Stempel gleichen Kern-Namens)
        if has_pin:
            bp += 1
        else:
            unc += 1
            unmatched_names.append(name or "(leer)")

    # --- Positions-first-Simulation NUR für namenlose no_match-Räume mit Pin --
    # (Schätzung im normierten Rahmen; die exakte Pixel↔Punkt-Prüfung folgt im
    #  schreibenden 4b, wo der echte Transform vorliegt.)
    posfirst = 0
    if pin_norm and stamp_norm and stamp_np:
        for r in generic_pin_rooms:
            vx, vy = pin_norm(r["position_x"], r["position_y"])
            ranked = sorted(
                (math.hypot(vx - sx, vy - sy), i)
                for i, (sx, sy) in enumerate(stamp_np) if i not in consumed
            )
            if not ranked:
                continue
            best_d, best_i = ranked[0]
            margin = (ranked[1][0] - best_d) if len(ranked) > 1 else 1.0
            if margin >= _POSITION_MARGIN:
                posfirst += 1
                consumed.add(best_i)

    return MeasureStats(
        page=page, text_stamps=len(stamps), room_stamps=len(room_stamps),
        no_text_layer=(len(room_stamps) == 0), rooms_total=total, pins_present=pins,
        rooms_incomplete=incomplete, by_name=bn, by_position=bp, uncertain=unc,
        no_match=nm, no_match_but_has_pin=nm_pin, position_first_recoverable=posfirst,
        unmatched_names=tuple(unmatched_names[:10]),
    )


def measure_page(page_text_dict: dict, result: dict, page: int = 1) -> MeasureStats:
    """Convenience: harvest + classify. Ändert nichts, schreibt nichts."""
    return classify(result or {}, harvest_stamps(page_text_dict or {}), page)


def format_log(st: MeasureStats, plan_id) -> str:
    pin_null = st.rooms_total - st.pins_present
    rate = (100.0 * pin_null / st.rooms_total) if st.rooms_total else 0.0
    backfillable = st.by_name + st.by_position
    names = ",".join(st.unmatched_names) if st.unmatched_names else "-"
    return (
        f"textlayer_backfill.measure plan={plan_id} page={st.page} "
        f"text_stamps={st.text_stamps} room_stamps={st.room_stamps} "
        f"no_text_layer={st.no_text_layer} rooms_total={st.rooms_total} "
        f"rooms_incomplete={st.rooms_incomplete} backfillable={backfillable} "
        f"by_name={st.by_name} by_position={st.by_position} uncertain={st.uncertain} "
        f"no_match={st.no_match} no_match_but_has_pin={st.no_match_but_has_pin} "
        f"position_first_recoverable={st.position_first_recoverable} "
        f"pin_null_rate={rate:.0f}% (pins_present={st.pins_present}/{st.rooms_total}) "
        f"unmatched_names=[{names}]"
    )

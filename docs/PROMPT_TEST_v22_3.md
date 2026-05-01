# Vision-Prompt v2 — Erwartung & Test-Anleitung (v22.3)

> **Status**: Erwartungswerte (manuelle Plan-Lesung), keine Live-Vision-
> Antwort. Der Live-Vergleich läuft mit
> `backend/scripts/test_vision_prompt.py` nach dem Deploy gegen den
> echten Anthropic-Endpoint.

## Ground Truth — was im Plan steht

Quelle: `test-plaene/plan_test.pdf.pdf` (Ausführungsplan EG,
Kleimayrngasse 3 Salzburg, Maßstab 1:50, Plan-Nr. 50.02). Werte
direkt von den Inline-Beschriftungen abgelesen, die der Architekt
aus dem CAD-Programm gedruckt hat.

| Wohneinheit | Raum             | Fläche (m²) | Wandumfang (m) | Bodenbelag    |
|-------------|------------------|-------------|----------------|---------------|
| W1          | WOHNEN / KOCHEN  | 32,84       | **24,32**      | PARKETT       |
| W1          | SCHLAFEN         | 12,03       | **14,72**      | PARKETT       |
| W1          | BAD              | 4,30        | **8,51**       | FEINSTEINZEUG |
| W1          | DIELE            | 3,65        | **8,09**       | FEINSTEINZEUG |
| W1          | AR (Abstellraum) | 2,00        | **5,70**       | PARKETT       |
| W1          | BALKON W1        | 5,10        | n/a            | FEINSTEINZEUG |
| W2          | WOHNEN / KOCHEN  | 32,66       | **25,07**      | PARKETT       |
| W2          | SCHLAFEN         | 11,68       | **14,55**      | PARKETT       |
| W2          | BAD              | 4,82        | **9,04**       | FEINSTEINZEUG |
| W2          | DIELE            | 3,85        | **8,37**       | FEINSTEINZEUG |
| W2          | AR (Abstellraum) | 2,00        | **5,70**       | PARKETT       |
| W2          | BALKON W2        | 10,45       | n/a            | FEINSTEINZEUG |
| Erschließung| EINGANG          | 2,52        | **6,35**       | FEINSTEINZEUG |
| Erschließung| STGH.            | 4,06        | n/a            | BODEN BESTAND |
| Erschließung| PODEST KG-EG     | 4,05        | n/a            | BODEN BESTAND |

**Höhe**: Im Plan keine Schnittzeichnung, keine RH-Beschriftung.
Erwartung: `height_m = null`, `ceiling_height_source = "default"`
für alle Räume.

## Erwartung mit Prompt v2

| Erwartete Verteilung | Anzahl Räume |
|----------------------|--------------|
| `perimeter_source = "labeled"` (Inline-Wert übernommen) | **11**  (alle mit Beschriftung) |
| `perimeter_source = "computed"` (KI summiert Vermassungskette) | 0 (nicht nötig — alle haben labels) |
| `perimeter_source = null` (Balkone, STGH., PODEST — kein Umfang gedruckt) | 4 |
| `ceiling_height_source = "default"` | 15 (alle) |

**Erwartete Toleranz**: Bei `labeled`-Source-Räumen sollte die
KI-Extraktion **byte-identisch** zum Plan-Wert sein (die Architektur-
Software druckt 2-Dezimalstellen, Vision liest 2-Dezimalstellen).
Akzeptanzbereich `±0,01 m` (≈ 0,04 % bei 24 m). Das Test-Script setzt
die Schwelle bei **5 %** als Sanity-Check; Verletzungen darunter sind
fast immer ein Prompt-Bug, nicht ein Daten-Bug.

## Vergleich Prompt v1 vs Prompt v2 (erwartet)

| Metrik                          | Prompt v1 (vor v22.3)    | Prompt v2 (v22.3)     |
|---------------------------------|--------------------------|------------------------|
| Räume mit gelesenem Umfang      | ~6-7 von 11 (geschätzt)  | **11 von 11**          |
| Mittlere Abweichung bei labeled | 1-3 % (Selbst-Abschritt) | **< 0,1 %** (1:1 Lesen) |
| `perimeter_source`-Differenzierung| nein (alles "vision")  | ja (`labeled`/`computed`) |
| Konfidenz-Wert pro Raum         | ja                       | ja                     |
| Verwechslung mit STUK/FPH       | möglich                  | **negativ ausgeschlossen** |
| Plausibilitätscheck             | nein                     | ja (P ≈ 4·√A)          |

## Test ausführen (für den Operator nach Deploy)

```bash
cd backend
export ANTHROPIC_API_KEY=sk-ant-...

python scripts/test_vision_prompt.py
```

Das Script:

1. Rendert die Test-PDF in 300-DPI-PNG (gleicher Pfad wie Production).
2. Schickt jede Seite mit dem aktuellen Prompt v2 an `claude-sonnet-4-6`.
3. Parst die JSON-Antwort.
4. Match-t Räume gegen die obige Ground-Truth (case-insensitive
   Substring-Match auf `room_name`).
5. Schreibt einen Markdown-Bericht nach `docs/PROMPT_TEST_RESULT.md`
   (überschreibt den vorherigen Lauf — kein Repo-Eintrag).
6. Exit-Code 0 wenn alle `labeled`-Räume innerhalb ±5 %, sonst Exit
   2 für CI-Failure.

## Was der Bericht enthalten wird

Format `docs/PROMPT_TEST_RESULT.md` (vom Script erzeugt):

```
# Vision-Prompt Test-Lauf — plan_test.pdf.pdf

- Plan: …
- Seiten analysiert: 1
- Räume erwartet (Ground Truth): 11
- Räume gematcht: 11 / 11

## Vergleichstabelle

| Raum | Fläche-Plan | Fläche-KI | Δ % | Umfang-Plan | Umfang-KI | Δ % | Source | Status |
|--|--|--|--|--|--|--|--|--|
| WOHNEN / KOCHEN | 32,84 m² | 32,84 m² | +0,0 % | 24,32 m | 24,32 m | +0,0 % | labeled | ✓ |
| SCHLAFEN | 12,03 m² | 12,03 m² | +0,0 % | 14,72 m | 14,72 m | +0,0 % | labeled | ✓ |
…

## Zusammenfassung

- Labeled-Source-Räume: 11 insgesamt, davon 11 innerhalb 5 %-Toleranz.
- Keine Toleranz-Verletzungen. ✓
```

(Wenn Vision Werte daneben liefert, taucht das in der Status-Spalte
mit ❌ auf und der Exit-Code geht auf 2.)

## Falls Vision nicht trifft — Iterations-Plan

1. **Wenn `perimeter_source` für ALLE Räume `null` ist** —
   Vision hat die neue Konvention nicht erfasst. Prüfen ob der Prompt
   wirklich aktualisiert ist (`grep "labeled" backend/app/plan_analysis/prompts/room_extraction.txt`).

2. **Wenn `perimeter_source` für viele Räume `vision` ist**
   (statt `labeled`) — Vision liefert den Wert, vergisst aber das
   neue Source-Tag. Prompt-Sektion „SUCHHIERARCHIE FÜR perimeter_m"
   schärfen, expliziter formulieren.

3. **Wenn Werte > 5 % daneben sind** — Vision liest die falsche
   Zahl als Umfang (z.B. eine Höhenkote oder Wandstärken-Vermassung).
   Negativbeispiel-Sektion erweitern, mit konkretem Plan-Snippet.

4. **Wenn Vision Räume verfehlt** — Prompt-Sektion „Felder pro Raum"
   prüfen, evtl. Beispiel-JSON erweitern um die spezifische Raumtyp-
   Zuordnung.

Iterations-Tempo: nach jeder Prompt-Änderung Test-Script erneut
laufen lassen, Bericht ablegen, vergleichen. Erst commiten wenn
≥ 90 % der labeled-Räume innerhalb 5 % treffen.

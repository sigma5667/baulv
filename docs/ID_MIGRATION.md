# Stabile IDs für LV-Positionen — Migrations-Konzept

**Stand:** 2026-04-27
**Anlass:** Schritt 2 der Agent-Integration (siehe `AGENT_READINESS.md`).
**Status:** Konzept — **noch keine Umsetzung.**

> **TL;DR:** Position-, Leistungsgruppen- und Berechnungsnachweis-IDs
> sind in der DB UUIDs. **Sie sind aber nicht stabil**, weil
> `POST /api/lv/{id}/calculate` aktuell **alle** Gruppen + Positionen
> + Berechnungsnachweise eines LVs löscht und neu anlegt. Jeder
> erneute Calculate-Run weist neue IDs zu. Für Agenten, die per ID
> auf Positionen zugreifen, ist das ein Showstopper.

---

## 1. Welche Felder dienen derzeit als ID?

### 1.1 DB-Ebene

Alle drei betroffenen Tabellen haben einen **UUID v4** als
Primary Key, defaultet auf `uuid.uuid4()`:

| Tabelle | PK-Spalte | Typ | Default |
|---|---|---|---|
| `leistungsgruppen` | `id` | UUID | `uuid.uuid4()` |
| `positionen` | `id` | UUID | `uuid.uuid4()` |
| `berechnungsnachweise` | `id` | UUID | `uuid.uuid4()` |

→ `backend/app/db/models/lv.py` Zeile 13, 29, 42 und
`backend/app/db/models/calculation.py`.

### 1.2 Was die API exponiert

`LVResponse` (in `backend/app/schemas/lv.py`) gibt für jede Position
exakt diese DB-`id` zurück. Frontend und Agenten konsumieren also den
DB-PK 1:1.

### 1.3 Was als Sekundär-Identifier dient

| Feld | Bedeutung | Stabilität |
|---|---|---|
| `Leistungsgruppe.nummer` | „01", „02", … | Stabil **innerhalb** eines LVs, sofern der Calculator denselben Code liefert. **Nicht** unique über alle LVs. |
| `Position.positions_nummer` | „01.01", „01.02.003", … | Wie oben — vom Calculator gesetzt, kann sich nach Raum-Änderungen verschieben. |
| `Position.gruppe_id` | FK | Bricht beim Re-Calculate (siehe unten). |

**Es gibt keinen anderen stabilen, agent-tauglichen Identifier.**

---

## 2. Wo entsteht Instabilität?

### 2.1 `POST /api/lv/{lv_id}/calculate` — der eigentliche Übeltäter

`backend/app/calculation_engine/engine.py` Zeile 140–143:

```python
# Clear existing positions and calculations for this LV
for gruppe in lv.gruppen:
    await db.delete(gruppe)
await db.flush()
```

Das löscht **alle** Leistungsgruppen des LVs. Per
`cascade="all, delete-orphan"` (`lv.py` Zeile 23, 36, 58)
werden mitgelöscht:

- alle `Positionen` der Gruppe,
- alle `Berechnungsnachweise` jeder Position.

Anschließend (Zeile 146–197) werden sie aus dem Calculator-Output
**neu erzeugt** mit frischen `uuid.uuid4()`. **Folge:**

- Position-IDs ändern sich bei jedem `/calculate`.
- Gruppen-IDs ändern sich bei jedem `/calculate`.
- Berechnungsnachweis-IDs ändern sich bei jedem `/calculate`.
- Auch **manuell vom User editierte** `langtext`, `einheitspreis`,
  `is_locked`, `text_source = "manual"` gehen verloren — der neue
  Position-Datensatz hat die Defaults, weil der Calculator diese
  Felder nicht kennt.

### 2.2 Andere Endpoints — alle stabil

| Endpoint | Verhalten | ID-Stabilität |
|---|---|---|
| `PUT /lv/positionen/{id}` | `setattr` auf bestehende Row | ✅ stabil |
| `POST /lv/{id}/sync-wall-areas` | iteriert `positions`, setzt `pos.menge` | ✅ stabil |
| `POST /lv/{id}/generate-texts` | matcht via `positions_nummer`, setzt `pos.langtext` | ✅ stabil |
| `POST /lv/from-template` | erstellt frische Rows | n/a (initial) |
| `PUT /lv/{id}` | LV-Stammdaten | ✅ Position-IDs unberührt |
| `POST /lv/projects/{pid}/lv` | LV anlegen, kein Position-Touch | n/a |

→ Nur `/calculate` ist destruktiv.

### 2.3 Zusätzlicher Befund: Gruppen ohne Timestamps

`Leistungsgruppe` (lv.py Zeile 26-36) hat **kein** `created_at` /
`updated_at`. Position und LV haben es. Damit ist eine reine
Heuristik „Wenn `created_at` einer Position zu alt ist, war sie
vor dem Calculate da" nicht durchziehbar — die Gruppe hat keinen
Anker.

### 2.4 Kollateral: `is_locked` schützt nicht vor Calculate

`/sync-wall-areas` und `/generate-texts` respektieren `is_locked`.
**`/calculate` nicht** — die Lock-Information sitzt auf der Position,
und die Position wird gelöscht, bevor `is_locked` ausgelesen wird.
Ein Agent oder User, der eine Position bewusst gegen Überschreiben
sperrt, verliert sie beim nächsten Calculate.

---

## 3. Vorgeschlagene Lösung

Drei mögliche Strategien, sortiert nach Aufwand und Robustheit:

### 3.1 Option A — **Upsert statt Delete+Recreate** (empfohlen)

`engine.py::calculate_lv` so umbauen, dass es **bestehende** Gruppen
und Positionen findet und nur deren berechnete Felder
(`menge`, neue Berechnungsnachweise) aktualisiert. Match-Schlüssel:

- `Leistungsgruppe`: `(lv_id, nummer)` — unique, weil pro LV nur
  einmal „01 Vorarbeiten" existieren darf.
- `Position`: `(gruppe_id, positions_nummer)` — analog.

**Pseudocode:**

```python
# Vor dem Berechnen: Index der bestehenden Strukturen
existing_gruppen = {g.nummer: g for g in lv.gruppen}
existing_positions = {
    (g.nummer, p.positions_nummer): p
    for g in lv.gruppen for p in g.positionen
}

for pos_qty in results:
    gruppe = existing_gruppen.get(pos_qty.gruppe_nummer)
    if gruppe is None:
        gruppe = Leistungsgruppe(...)  # neu anlegen
        db.add(gruppe)
    else:
        gruppe.bezeichnung = pos_qty.gruppe_name  # ggf. refresh
        gruppe.sort_order = _code_to_sort_order(pos_qty.gruppe_nummer)

    key = (pos_qty.gruppe_nummer, pos_qty.position_code)
    pos = existing_positions.get(key)
    if pos is None:
        pos = Position(...)  # neu anlegen
        db.add(pos)
    elif not pos.is_locked:
        # Nur überschreiben, wenn nicht gesperrt.
        pos.kurztext = pos_qty.short_text
        pos.einheit = pos_qty.unit
        pos.menge = float(pos_qty.total_quantity)
        pos.sort_order = _code_to_sort_order(pos_qty.position_code)
    # langtext, einheitspreis, is_locked, text_source NIE überschreiben.

    # Berechnungsnachweise: hier ist Replace OK, weil sie reine
    # Berechnungs-Beweisspur sind. Berechnungsnachweis-IDs sind nicht
    # agent-relevant — Agenten greifen auf Position zu, nicht auf BN.
    for bn in list(pos.berechnungsnachweise):
        await db.delete(bn)
    for line in pos_qty.measurement_lines:
        db.add(Berechnungsnachweis(position_id=pos.id, ...))

# Positionen, die der neue Calculate nicht mehr enthält:
seen_keys = {(q.gruppe_nummer, q.position_code) for q in results}
for key, pos in existing_positions.items():
    if key not in seen_keys and not pos.is_locked:
        await db.delete(pos)
    # gesperrte Positionen, die der neue Calc nicht produziert,
    # bleiben stehen (User wollte sie ja behalten).
```

**Vorteile:**
- Position-IDs bleiben stabil.
- Manuelle Edits (`langtext`, `einheitspreis`, `is_locked = true`)
  überleben Calculate.
- Kein DB-Schema-Eingriff — nur Logik-Änderung.
- Frontend muss nichts anpassen.

**Nachteile:**
- Wenn der Calculator zwischen zwei Runs `positions_nummer` ändert
  (z. B. weil der User eine Gruppe umnummeriert), wird die
  Position als „neu" angelegt und die alte gelöscht. Mitigieren:
  `positions_nummer` ist heute deterministisch aus
  `_code_to_sort_order` und Trade-Logik abgeleitet und ändert sich
  nicht von selbst.

### 3.2 Option B — Hash-basierte deterministische UUIDs (zusätzlich)

Für Agenten-/MCP-Zugriffe ein **paralleles** Identifier-Feld
einführen, das aus stabilen Eingaben deterministisch berechnet wird:

```python
import uuid

LV_NS = uuid.UUID("c2c5b4f0-1234-5678-9abc-def012345678")  # konstanter Namespace

def position_stable_key(lv_id, gruppe_nummer, positions_nummer):
    return uuid.uuid5(LV_NS, f"{lv_id}|{gruppe_nummer}|{positions_nummer}")
```

Neue Spalte `Position.stable_key` (UUID, indexed, unique pro LV)
als sekundärer agent-tauglicher Identifier — die DB-`id` bleibt
weiterhin eindeutiger PK, der Agent referenziert aber lieber per
`stable_key`.

**Vorteile:**
- Robust gegen Re-Insert: selbst wenn intern doch mal alle Positionen
  gelöscht und neu angelegt würden, bliebe `stable_key` gleich.
- Agent kann „berechne dich selbst" („gib mir die Position mit
  `gruppe_nummer=01 + positions_nummer=01.02`") ohne Round-Trip.

**Nachteile:**
- Doppelter ID-Apparat — DB-PK + stable_key — erhöht kognitive Last.
- DB-Migration nötig (neue Spalte, Backfill, Index, Unique-Constraint).
- `stable_key` ist nur stabil, solange `positions_nummer` stabil
  ist. Hilft also nichts gegen die Wurzel des Problems.

**Empfehlung:** **Nicht jetzt**. Nur sinnvoll, wenn nach Option A
weitere Edge Cases auftauchen, in denen die DB-`id` doch instabil
würde.

### 3.3 Option C — Calculate sperren, nur Berechnungsnachweise neu

Statt das ganze LV neu zu generieren, **nur die
Berechnungsnachweise** löschen + neu anlegen. Positionen bleiben
unangetastet, ihre `menge` wird aus den neuen Nachweisen aufaddiert.

**Vorteile:**
- Position-IDs maximal stabil.
- Position-Lifecycle entkoppelt von Calculate.

**Nachteile:**
- Wenn der Calculator eine **neue** Position ableitet (z. B. neuer
  Raumtyp aus Plananalyse), gibt es keinen Mechanismus, diese in
  die Position-Tabelle zu bringen.
- Bricht den heutigen Workflow „Plan analysieren → LV calculate →
  alle Positionen werden vom Trade-Calculator emittiert".

**Empfehlung:** **Nein**. Zu invasiv für den Use Case.

### 3.4 Empfehlung

**Option A allein**. Implementierungsskizze in 3.1, kein
DB-Schema-Eingriff, kein Frontend-Eingriff, Agenten-tauglich,
respektiert dabei `is_locked` auch beim Calculate (Bonus-Fix für
2.4).

---

## 4. Migrations-Aufwand (Schätzung)

| Schritt | Aufwand | Anmerkung |
|---|---|---|
| `engine.py::calculate_lv` umbauen auf Upsert (Option A) | **3–4 h** | Match-Logik + Tombstone-Handling für entfernte Positionen + Berechnungsnachweis-Replace |
| Unit-Tests für Upsert-Verhalten | **3–4 h** | Calc → manuell editieren → Calc → IDs stabil, Edits erhalten, gelockte Positionen unberührt |
| Integrationstest: zweiter Calculate auf demselben LV | **1 h** | API-Level: erste Position-`id` aus Response 1 muss in Response 2 wieder vorkommen |
| Logging + Diagnose (`calculate.upserted=12 calculate.deleted=3 calculate.created=2`) | **0,5 h** | Konsistent mit dem bestehenden Logging-Stil in `lv.py` |
| Code-Review + manueller Smoke-Test gegen Wandflächen-Test-Projekt | **1 h** | Sicherstellen, dass die Live-Daten weiter rendern |
| Optional: `Leistungsgruppe.created_at` / `updated_at` (Befund 2.3) | **0,5 h Migration + 0,5 h Backfill** | Nicht zwingend für ID-Stabilität, aber hilft Agent-Sync |

**Gesamt: ≈ 9–12 h** für Option A inklusive Tests und Smoke-Test.

Optional + 1 h für die Gruppen-Timestamps. Option B wäre +4–6 h
(Migration, Backfill, Index, Unique-Constraint, Endpoint-Lookup auf
beiden Keys).

---

## 5. Auswirkungen auf bestehende Daten

### 5.1 DB-Migration

**Keine schema-Änderung** für Option A. Alle bestehenden Rows
funktionieren unverändert.

### 5.2 Bestehende LVs auf Production

Beim ersten Calculate **nach** dem Rollout würden die Positions-IDs
heute **noch einmal** rotieren (eben dieser Calculate-Run löscht
und legt neu an — das ist die alte Logik, die wir gerade
ablösen). Ab dann sind die IDs stabil.

→ **Konsequenz:** Wir können nicht garantieren, dass eine Position-ID,
die ein Agent **vor** dem Rollout aus der API gelesen hat, nach dem
Rollout noch existiert. Alle nach dem Rollout gelesenen IDs sind
stabil.

→ **Mitigation:** Ankündigung im `/api/health`-Output o. ä., dass ab
v18 IDs stabil sind, und Hinweis in `AGENT_READINESS.md` ergänzen.

### 5.3 Bestehende Frontend-Builds

Frontend nutzt `position.id` lediglich als React-Key und für `PUT
/lv/positionen/{id}`. Beide Use Cases sind nach dem Rollout
**robuster** — der Editor-State, der nach einem Calculate aktuell
springt (alle Positionen neu, alle Edits weg), bleibt nun stabil.
Keine Frontend-Änderung nötig, das v17-UI profitiert sofort.

### 5.4 Berechnungsnachweise

Berechnungsnachweis-IDs **bleiben** instabil (Option A in Abschnitt
3.1 löscht und legt sie pro Calculate neu an). Das ist **akzeptabel**,
weil:

- Agenten interessieren sich für Position-IDs, nicht BN-IDs.
- BNs sind reine Beweisspur, sie werden nie direkt referenziert.
- Stabilität hier hätte keinen User Value, würde aber den Upsert
  deutlich komplizierter machen (Match-Schlüssel
  `(position_id, room_id, formula_expression)` ist fragil).

### 5.5 Locked Positions

Mit Option A überleben gesperrte Positionen jetzt auch das Calculate.
Das ist **eine Verhaltensänderung** gegenüber heute:

- Heute: `is_locked = true` schützt vor `/sync-wall-areas` und
  `/generate-texts`, aber nicht vor `/calculate` (weil dort die ganze
  Position weg ist, bevor der Lock geprüft werden kann).
- Mit Option A: `is_locked = true` schützt vor allen drei Endpoints.

→ Das ist die **erwartete** Semantik des Lock-Buttons im UI und
sollte nicht zu Beschwerden führen — eher das Gegenteil. Trotzdem
in den Release-Notes ausweisen.

---

## 6. Was **nicht** angefasst wird

- DB-Schema (kein neuer Spalten / Tabellen / Indexes für Option A).
- API-Pfade, -Methoden, -Response-Felder.
- Frontend-Code.
- `/sync-wall-areas` und `/generate-texts` (sind bereits korrekt).
- Berechnungsnachweis-Modell (BN-IDs bleiben bewusst instabil).
- JWT, Stripe, Service-Worker, Auth-Layer.

---

## 7. Offene Fragen

1. Wenn der Calculator eine Position zwischen zwei Runs **umnummeriert**
   (`01.02` → `01.03`), behandelt Option A das als „alte gelöscht, neue
   angelegt". Ist das akzeptabel, oder soll das Konzept hier zusätzlich
   einen Match per `kurztext` als Fallback erwägen? Heute kommt das
   praktisch nicht vor, weil `_code_to_sort_order` deterministisch
   ist — Frage notiert für Schritt 3.

2. Soll Option B (`stable_key`) trotzdem schon vorbereitet werden,
   damit Agenten eine **idempotente** Position-Erzeug-Schnittstelle
   bekommen (Agent sagt „leg Position mit `stable_key=foo` an", Server
   antwortet mit Insert oder mit der bestehenden)? Sinnvolle Diskussion
   im Schritt „Agent-Write-Endpoints", nicht jetzt.

3. Sollen `Leistungsgruppe.created_at` / `updated_at` als Mini-Side-Quest
   in derselben Migration mit ergänzt werden? Empfehlung: Ja, sehr
   billig (1 h inkl. Backfill auf `now()`).

---

## 8. Status

- [x] Befund: IDs **nicht** stabil bei `/calculate`.
- [x] Lösungsoptionen ausgearbeitet.
- [x] Aufwand geschätzt.
- [x] Auswirkungen identifiziert.
- [ ] **User-Freigabe für Schritt 3 abgewartet.**

**Bitte mit „OK weiter mit Schritt 3" bestätigen, wenn das Konzept
passt.**

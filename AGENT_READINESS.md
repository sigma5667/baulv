# BauLV — Agent-Readiness-Analyse

**Stand:** 2026-04-27
**Aktuelle App-Version:** v17 (`baulv-v17-2026-04-23-lv-templates`)
**Letzter relevanter Commit:** `4d97f5e` (LV-Vorlagen-Bibliothek)
**Deployment:** Railway — `https://baulv-production.up.railway.app`

> **Zweck dieses Dokuments:** Bestandsaufnahme vor der schrittweisen
> Anbindung externer KI-Agenten (Claude Desktop, ChatGPT, n8n) via
> MCP-Protokoll. **Keine Code-Änderungen** in diesem Schritt — nur
> Analyse der Ist-Architektur und Identifikation der Lücken.

---

## 1. Verwendete Technologie

### 1.1 Backend

| Komponente | Version / Variante | Zweck |
|---|---|---|
| Python | ≥ 3.11 | Laufzeit (gepinnt in `pyproject.toml`) |
| FastAPI | ≥ 0.115.0 | HTTP-Framework, async, OpenAPI-fähig |
| SQLAlchemy | ≥ 2.0 (asyncio) | ORM, async sessions |
| asyncpg | latest | Postgres-Treiber (async) |
| Alembic | latest | DB-Migrationen, läuft im `lifespan`-Hook automatisch beim Boot |
| Pydantic | ≥ 2.0 | Request-/Response-Validation |
| python-jose | latest | JWT (HS256) |
| bcrypt + passlib[bcrypt] | latest | Passwort-Hashing |
| Anthropic SDK | ≥ 0.40.0 | Claude-Integration (Plan-Vision, Support-Chat, Project-Chat) |
| Stripe | latest | Subscription-Billing |
| sse-starlette | latest | Server-sent events (Chat-Streaming) |
| pymupdf, pdf2image, Pillow | latest | Plan-Ingest (PDF → Bild) |
| openpyxl, reportlab | latest | LV-Export (Excel/PDF) |
| pgvector | latest | Vorbereitung für semantische Suche (noch nicht aktiv) |

### 1.2 Frontend

| Komponente | Version | Zweck |
|---|---|---|
| React | 19 | UI-Framework |
| TypeScript | 5.7 | Typsystem |
| Vite | 6 | Build, Dev-Server |
| react-router-dom | 7.1 | Client-side Routing |
| @tanstack/react-query | 5.62 | Server-state-Caching |
| Zustand | 5 | UI-state (kleiner Scope) |
| axios | latest | HTTP-Client mit JWT-Interceptor |
| Tailwind CSS | 3.4 | Styling |
| Radix UI | latest | Headless-Komponenten (Dialog, Dropdown, Toast …) |
| lucide-react | 0.468 | Icons |

### 1.3 Datenbank & Infrastruktur

| Komponente | Variante / Version | Hinweis |
|---|---|---|
| PostgreSQL | Railway-managed | UUIDs als Primary Keys, JSONB für Templates und Berechnungs-Deductions |
| Alembic | mehrere Migrationen, automatisch beim Boot | siehe `backend/alembic/versions/` |
| Service Worker | `frontend/public/sw.js` | network-first für HTML, cache-first für hashed assets; `CACHE_NAME` synchronisiert mit `APP_BUILD_TAG` |
| Hosting | Railway (single container) | FastAPI serviert API unter `/api/*` und SPA-Fallback unter `/` |

---

## 2. Bestehende API-Endpoints

Alle Routen unter `/api/*`. Authentifizierung: **JWT (Bearer-Token)**
mit `jti`-Claim → DB-Lookup gegen `user_sessions`. Login + Register
sind die einzigen Endpoints ohne `get_current_user`-Dependency
(zusätzlich `support_chat` ohne Auth).

### 2.1 Auth (`/api/auth/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/auth/register` | Neuen User anlegen, Token + User zurückgeben |
| POST | `/auth/login` | Session erzeugen, Token zurückgeben |
| GET | `/auth/me` | Aktuellen User abrufen |
| PUT | `/auth/me` | Profil-Update (`full_name`, `company_name`) |
| GET | `/auth/me/features` | Feature-Flags je Plan (Pro/Basis), Beta-Override |
| GET | `/auth/me/usage` | Nutzungs-Counter (Projektzahl, AI-Calls etc.) |
| POST | `/auth/password-reset` | E-Mail-Token-Flow (anonym) |
| POST | `/auth/me/password` | Passwort ändern (Re-Auth via altem Passwort) |
| GET | `/auth/me/export` | DSGVO Art. 20 — JSON-Dump aller User-Daten |
| POST | `/auth/me/delete` | DSGVO Art. 17 — Account-Löschung, verlangt `confirmation = "LÖSCHEN"` + Passwort |
| PUT | `/auth/me/privacy` | Marketing-Consent flag |
| GET | `/auth/me/sessions` | Liste der aktiven Sessions (Geräte) |
| DELETE | `/auth/me/sessions/{id}` | Einzelne Session widerrufen |
| POST | `/auth/me/sessions/revoke-others` | Alle anderen Sessions kicken |
| GET | `/auth/me/audit-log` | Audit-Trail (Logins, Deletions, Exports …) |

### 2.2 Projekte (`/api/projects/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/projects` | Projektliste des Users |
| POST | `/projects` | Projekt anlegen (Plan-Limit aus Subscription) |
| GET | `/projects/{id}` | Projektdetails |
| PUT | `/projects/{id}` | Stammdaten ändern |
| DELETE | `/projects/{id}` | Kaskadierende Löschung |
| GET | `/projects/{id}/structure` | **Aggregierter Hierarchie-Tree** (Building → Floor → Unit → Room → Opening) |
| POST | `/projects/{id}/quick-add/single-family` | Schnell-Anlage „Einfamilienhaus" (seed-data) |

### 2.3 Pläne / Plan-Analyse (`/api/plans/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/projects/{id}/plans` | PDF-Upload (multipart, Magic-Byte-Check) |
| GET | `/projects/{id}/plans` | Pläne eines Projekts |
| POST | `/plans/{id}/analyze` | **Pro-gated** — Claude Vision extrahiert Räume, Öffnungen, Höhen |
| GET | `/plans/{id}` | Plan-Metadaten + Analyse-Status |

### 2.4 Gebäudestruktur (`/api/*` — kein einheitliches Prefix, Pfad bildet Hierarchie ab)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/projects/{id}/buildings` | Gebäude anlegen |
| GET | `/projects/{id}/buildings` | Gebäudeliste |
| GET/PUT/DELETE | `/buildings/{id}` | Gebäude-CRUD |
| POST | `/buildings/{id}/floors` | Stockwerk anlegen |
| GET/PUT/DELETE | `/floors/{id}` | Stockwerk-CRUD |
| POST | `/floors/{id}/units` | Wohneinheit anlegen |
| GET/PUT/DELETE | `/units/{id}` | Wohneinheits-CRUD |

### 2.5 Räume & Öffnungen (`/api/rooms/*`, `/api/openings/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/units/{id}/rooms` | Raum anlegen |
| GET | `/projects/{id}/rooms` | Flache Raumliste eines Projekts |
| GET/PUT/DELETE | `/rooms/{id}` | Raum-CRUD |
| POST | `/rooms/{id}/calculate-walls` | Wandflächen-Berechnung (Umfang × Höhe – Öffnungen × Faktor) |
| POST | `/projects/{id}/rooms/bulk-calculate-walls` | Bulk-Wandberechnung über alle Räume |
| POST | `/rooms/{id}/openings` | Tür/Fenster/Sonstiges anlegen |
| GET/PUT/DELETE | `/openings/{id}` | Öffnungs-CRUD |

### 2.6 Leistungsverzeichnis (`/api/lv/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/lv/projects/{id}/lv` | LV anlegen (leer) |
| POST | `/lv/from-template` | LV aus Template instanziieren |
| GET | `/lv/projects/{id}/lv` | LVs eines Projekts |
| GET | `/lv/{id}` | LV-Vollbaum (Gruppen + Positionen + Berechnungsnachweise) |
| PUT | `/lv/{id}` | LV-Stammdaten (`name`, `status`, `vorbemerkungen`) |
| POST | `/lv/{id}/calculate` | Mengen aus Räumen berechnen, persistieren als Berechnungsnachweise |
| POST | `/lv/{id}/generate-texts` | Claude generiert Lang-/Kurztexte |
| POST | `/lv/{id}/sync-wall-areas` | Wandflächen aus Räumen ins LV übernehmen |
| PUT | `/lv/positionen/{id}` | Einzelne Position editieren (`kurztext`, `langtext`, `einheitspreis`, `is_locked`) |
| POST | `/lv/{id}/export?format=xlsx\|pdf` | Datei-Export |

### 2.7 LV-Vorlagen (`/api/templates/*`) — **neu in v17**

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/templates` | Templates listen (System-Templates + eigene), nur Counts ohne Payload |
| GET | `/templates/{id}` | Template-Details inkl. `template_data` (JSONB) |
| POST | `/templates` | LV als Template speichern (`category` Pflicht) |
| DELETE | `/templates/{id}` | Eigenes Template löschen (System-Templates immutable) |

### 2.8 Chat (`/api/chat/*`) — Pro-gated

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/chat/sessions` | Chat-Session erzeugen (optional an Projekt gebunden) |
| GET | `/chat/sessions` | Eigene Sessions |
| PATCH | `/chat/sessions/{id}` | Titel ändern |
| DELETE | `/chat/sessions/{id}` | Session löschen |
| GET | `/chat/sessions/{id}/messages` | Nachrichten lesen |
| POST | `/chat/sessions/{id}/messages` | Nachricht senden, SSE-Stream zurück |

### 2.9 Stripe (`/api/stripe/*`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/stripe/checkout` | Checkout-Session erzeugen |
| POST | `/stripe/portal` | Customer-Portal-Link |
| POST | `/stripe/webhook` | Webhook-Empfänger (Subscription-State sync) |

### 2.10 Misc

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/health` | Health-Check (Railway nutzt das) |
| POST | `/api/support-chat` | **Public**, rate-limited (20/h/IP), Landing-Page-Widget mit `claude-haiku-4-5` |

**Auto-OpenAPI:** FastAPI exponiert per Default `/openapi.json`,
`/docs` (Swagger UI), `/redoc`. Diese sind aktuell **nicht explizit
deaktiviert** — bedeutet: das Schema ist bereits maschinenlesbar
abrufbar (gut für MCP-Codegen), wird aber aktuell von keinem Agenten
konsumiert.

---

## 3. Aufbau der LV-Datenstruktur

### 3.1 Hierarchie der Bau-Entitäten

```
User (1) ─── (N) Project
                    │
                    ├── (N) Plan       ◄─── PDF-Datei + Claude-Vision-Analyse-Status
                    │
                    ├── (N) Building
                    │        │
                    │        └── (N) Floor (Stockwerk, level_number, floor_height_m)
                    │                  │
                    │                  └── (N) Unit (Wohnung/Bürofläche)
                    │                            │
                    │                            └── (N) Room
                    │                                      │  area_m2, perimeter_m, height_m
                    │                                      │  wall_area_gross_m2, wall_area_net_m2
                    │                                      │  applied_factor, ai_confidence
                    │                                      │
                    │                                      └── (N) Opening
                    │                                            (Tür/Fenster/Sonstiges,
                    │                                             width_m × height_m × count)
                    │
                    ├── (N) LV (Leistungsverzeichnis, trade=Gewerk, status)
                    │        │
                    │        └── (N) Leistungsgruppe (nummer, bezeichnung)
                    │                  │
                    │                  └── (N) Position
                    │                            │  positions_nummer, kurztext, langtext,
                    │                            │  einheit, menge, einheitspreis,
                    │                            │  positionsart, text_source, is_locked
                    │                            │
                    │                            └── (N) Berechnungsnachweis
                    │                                  (position_id × room_id ⇒
                    │                                   raw_quantity, formula_*,
                    │                                   onorm_factor, deductions, net_quantity)
                    │
                    └── (N) ChatSession (optional projekt-gebunden, ON DELETE SET NULL)
                              │
                              └── (N) ChatMessage (role, content, context_refs JSONB)
```

### 3.2 LV-Templates (separater Mandant)

```
LVTemplate (id, name, category, gewerk, is_system, created_by_user_id?)
   │
   └── template_data: JSONB
         {
           "gruppen": [
             {
               "nummer": "01",
               "bezeichnung": "Vorarbeiten",
               "positionen": [
                 {
                   "positions_nummer": "01.01",
                   "kurztext": "...",
                   "langtext": "...",
                   "einheit": "m²",
                   "kategorie": "wand|decke|boden|vorarbeit|sonstiges"
                 }
               ]
             }
           ]
         }
```

System-Templates (`is_system = true`): immutable, von Migrationen
geseedet, allen Usern sichtbar. User-Templates (`is_system = false`,
`created_by_user_id` gesetzt): nur dem Owner sichtbar (404-Maskierung
für Cross-Tenant-Zugriffe).

### 3.3 IDs

- **Alle Primary Keys: UUID v4** (`uuid.uuid4()` als default).
- Foreign Keys cascadieren: User → Project (CASCADE), Project →
  Building/Floor/Unit/Room/Opening/Plan/LV/ChatSession (CASCADE),
  LV → Leistungsgruppe → Position → Berechnungsnachweis (CASCADE).
- **Ausnahmen:**
  - `ChatSession.project_id` → `ON DELETE SET NULL` (Chats überleben
    Projektlöschung).
  - `AuditLogEntry.user_id` → `ON DELETE SET NULL` (DSGVO-Audit
    überlebt Account-Löschung anonymisiert).
  - `Berechnungsnachweis.room_id` → `ON DELETE CASCADE` an Room.

### 3.4 Gewerke (trade)

Aktuell ein **String-Feld** auf `LV.trade` und `LVTemplate.gewerk`.
Kein Enum, keine DB-Constraint. Frontend-seitig:

```ts
// frontend/src/types/template.ts
TEMPLATE_GEWERK_LABELS = { malerarbeiten: "Malerarbeiten" }
```

→ aktuell nur ein offizielles Gewerk (`malerarbeiten`). Erweiterung
auf Elektro/Sanitär/Heizung wäre eine **reine Daten-Änderung**
(Templates seeden + Label registrieren), kein Schema-Eingriff.

### 3.5 Position-Felder (für Agenten besonders relevant)

| Feld | Typ | Bedeutung |
|---|---|---|
| `id` | UUID | Persistente PK |
| `gruppe_id` | UUID FK | → Leistungsgruppe |
| `positions_nummer` | String | z. B. `"01.02.03"` (manuell vergeben) |
| `kurztext` | String | Eine Zeile, Listen-Anzeige |
| `langtext` | Text \| null | Detailbeschreibung, kann von Claude generiert werden |
| `einheit` | String | `m²`, `m`, `Stk.`, `psch`, `h` … (kein Enum!) |
| `menge` | Float \| null | Wird über `/lv/{id}/calculate` aus Räumen berechnet, kann manuell überschrieben werden |
| `einheitspreis` | Float \| null | Vom User gesetzt |
| `gesamtpreis` | Float \| null | Computed: `menge * einheitspreis` |
| `positionsart` | String | z. B. `"normal"`, `"eventual"`, `"alternativ"` (aktuell informativ, kein Constraint) |
| `text_source` | String | `"manual"` / `"ai_generated"` / `"template"` |
| `is_locked` | Bool | Schützt vor Überschreiben durch erneutes „Texte generieren" |
| `sort_order` | Int | Reihenfolge innerhalb der Gruppe |

### 3.6 Berechnungsnachweis (Beweisspur)

Für jede Position × Raum-Kombination, an der die Mengen-Berechnung
arbeitet, wird ein `Berechnungsnachweis` persistiert:

- `formula_description` — menschlich lesbarer Text
  (`"Wandfläche brutto - Türen/Fenster"`)
- `formula_expression` — mathematischer Ausdruck (`"15.20 * 2.50 - 1.80*2.10*1"`)
- `onorm_factor` / `onorm_rule_ref` / `onorm_paragraph` — Math-Metadata
  des Berechnungs-Engines. **Achtung:** API gibt diese als
  `rule_factor` / `rule_ref` / `rule_paragraph` zurück (Pydantic-Alias).
  DB-Spaltennamen behalten den `onorm_`-Prefix für Backwards-Compat;
  die App ist eine Berechnungs-Engine, keine Norm-Bibliothek.
- `deductions` — JSONB-Liste der abgezogenen Öffnungen
  (`{ opening, area, deducted, reason? }`).
- `net_quantity` — finale Menge nach Faktor & Abzügen.

Diese Felder sind das, was Agenten brauchen, um zu **erklären, wie eine
Position-Menge zustande kommt** — der Audit-Trail ist die wertvollste
Größe in der ganzen Datenstruktur.

### 3.7 Wand/Decke-Routing

Wand- und Deckenflächen werden **nicht über ein Enum-Feld** an
Positionen gebunden, sondern per **Keyword-Match auf `kurztext` +
Einheits-Check (`einheit = "m²"`)**. Die Logik sitzt in
`lv/{id}/sync-wall-areas`. Konsequenz für Agenten: das LV-Modell hat
keinen expliziten "ist das eine Wandposition?"-Marker — der Agent
müsste denselben Heuristik-Match nachbauen oder sich auf den
serverseitigen Sync verlassen.

---

## 4. Lücken für Agent-Integration

Diese Liste ist die wichtigste in diesem Dokument. Sie zeigt, **was
fehlt**, damit ein externer Agent (Claude Desktop, n8n, ChatGPT mit
Custom-Connector) zuverlässig gegen BauLV arbeiten kann.

### 4.1 Authentifizierung — JWT ist nicht agent-tauglich

**Status quo:** Auth ist ausschließlich JWT mit 7-Tage-TTL und
DB-gestützter Session-Row (jti). Token wird nur im Login-/Register-Flow
ausgegeben. Es gibt **keinen API-Key-Pfad**, keinen OAuth-Flow, kein
PAT-Konzept.

**Problem:**
- Headless-Agenten (n8n, MCP-Server, Cron-Jobs) können sich aktuell
  nur einloggen, indem sie ein User-Passwort kennen — das ist ein
  Antipattern für Maschinen-Konten.
- Das 7-Tage-Token muss von Hand rotiert werden, sonst kippt der
  Agent jede Woche aus der Auth.
- Token-Ausstellung ist an `Request`-Kontext (User-Agent, IP) gebunden
  und wandert in eine `UserSession`-Row, die in der UI als „Gerät"
  auftaucht — agentische Sessions würden das UI verschmutzen.

**Lücke:** Eigener Mechanismus für **Programmatic-Access-Tokens (PAT)**
oder **API-Keys** mit:
- separate Tabelle (`api_keys`), nicht `user_sessions`,
- Scope-Felder (z. B. `read:projects`, `write:lv`, `admin:templates`),
- optional Rate-Limit-Bucket je Key,
- Header-basierte Auth (`Authorization: Bearer pat_...` oder
  `X-API-Key: ...`).

### 4.2 Kein MCP-Server vorhanden

Die App **hat keinen MCP-Server**. Es gibt keine Beschreibung der
Tools (Resources/Prompts), die Claude Desktop/n8n/ChatGPT als
Schnittstelle konsumieren könnten.

**Lücke:** Ein dedizierter `backend/app/mcp/` (oder ein separates
Service) der:
- die Domänen-Operationen (Projekt erstellen, Raum hinzufügen, LV
  generieren, Position bearbeiten …) als **MCP-Tools** beschreibt,
- die **Audit-/Berechnungsnachweis-Daten** als MCP-Resources exponiert
  (Agenten lesen "wie kam diese Menge zustande?" direkt),
- gegen die bestehenden FastAPI-Endpoints proxiert (kein doppelter
  Business-Code).

### 4.3 OpenAPI ist da, aber nicht agent-optimiert

`/openapi.json` ist verfügbar (FastAPI default), aber:
- viele Endpoints haben **keine `description=`** und nur den deutschen
  Docstring → Schemas wirken im Tool-Picker eines Agenten kryptisch
  (`POST /projects/{id}/quick-add/single-family` braucht eine
  englische, agent-freundliche Erklärung).
- Pfad-Parameter sind **UUIDs ohne Beispielwerte** → Agenten haben
  keinen Anhaltspunkt, woher sie die ID nehmen.
- **`operation_id`** ist überall der FastAPI-Default (Funktionsname +
  Pfad-Hash) — schlecht zu lesen, schlecht zu verlinken.
- Response-Examples fehlen flächendeckend.

**Lücke:** `responses=`, `description=`, `summary=` und `operation_id=`
in jedem Endpoint nachziehen, plus ein paar
`Field(..., examples=[...])`.

### 4.4 Keine konsistente List-/Search-Schicht

Mehrere Entitäten haben **keine flache Listen-Endpoint**:
- Räume: `GET /projects/{id}/rooms` ✓ (aber kein
  `GET /units/{id}/rooms`, kein `GET /buildings/{id}/rooms`).
- Öffnungen: gar keine globale Liste — Agent muss erst Räume holen,
  dann pro Raum die Öffnungen extrahieren.
- Positionen: kein direkter `GET /positionen?lv_id=X` — Agent muss
  immer das gesamte LV-Tree fetchen (`GET /lv/{id}`).
- Berechnungsnachweise: keine eigene Liste, nur eingebettet in
  Position-Response.

**Auch fehlend:** Filter, Pagination, Sortierung. Listen geben *alles*
zurück. Bei einem 400-Räume-Projekt wird das eklig.

**Lücke:** Vereinheitlichte Query-Convention
(`?limit=&offset=&sort=&filter[trade]=`) auf allen List-Endpoints,
plus separate flache List-Endpoints für `positionen` und
`berechnungsnachweise`.

### 4.5 Keine Idempotency-Keys

POST-Endpoints (Projekt erstellen, LV erstellen, Position editieren,
Plan-Analyse triggern) sind **nicht idempotent**. Wiederholt der Agent
dieselbe Operation (z. B. weil der erste Request in einen Timeout
lief), bekommt er **doppelte Daten**.

**Lücke:** `Idempotency-Key`-Header-Konvention (Stripe-Style) auf
allen mutierenden Endpoints — Server hält die Antwort 24 h vor und
gibt sie bei Wiederholung zurück.

### 4.6 Keine Rate-Limits auf authenticated Endpoints

Nur `POST /api/support-chat` ist rate-limited (20/h pro IP,
unauthenticated). Authenticated Endpoints haben **kein Limit**. Ein
falsch konfigurierter n8n-Loop kann das Backend mit Plan-Analyse-
Triggers (Claude Vision = teuer) fluten.

**Lücke:** Per-User- und per-API-Key-Bucket (`X-RateLimit-*`-Header
in der Response), differenziert nach Endpoint-Klasse:
- billig (CRUD): 600/min
- moderat (Calculate, Sync): 60/min
- teuer (Plan-Analyse, Generate-Texts): 20/h

### 4.7 Keine Webhooks raus

BauLV sendet keine Outbound-Webhooks. Agenten müssen **pollen**, um
mitzukommen, ob z. B. ein Plan fertig analysiert ist (`analysis_status
= "completed"`). Ineffizient, fehleranfällig.

**Lücke:** Outbound-Webhook-Subscriptions
(`POST /api/webhooks/subscribe { url, events: ["plan.analyzed",
"lv.calculated", "position.updated"] }`) plus signierte Payloads.

### 4.8 Keine Batch-Operationen

Aktuell muss ein Agent jeden Raum, jede Öffnung, jede Position
**einzeln** anlegen. 50 Räume = 50 Round-Trips = 5+ Sekunden. Es gibt
einen einzigen Bulk-Endpoint (`/projects/{id}/rooms/bulk-calculate-walls`)
und der ist read-only.

**Lücke:** Batch-Create für Räume, Öffnungen, Positionen
(`POST /units/{id}/rooms/batch [{...}, {...}]` etc.), idealerweise
all-or-nothing in einer Transaktion.

### 4.9 Mengeneinheit (`einheit`) ist freitext

Position.einheit ist `String` ohne Constraint. Werte wie `"m²"`,
`"m^2"`, `"qm"`, `"sqm"` koexistieren potenziell. Der wand/decke-Sync
prüft hardcodiert auf `"m²"`. Ein Agent, der `"m^2"` schickt, bricht
die Berechnung still.

**Lücke:** Enum + Validierung (`m | m² | m³ | Stk | psch | h | kg | t`),
oder Normalisierung im Schema-Layer.

### 4.10 Kein expliziter Wand/Decke-Marker auf Position

Wandflächen-Sync funktioniert über **Keyword-Match auf `kurztext`** —
für Agenten unsichtbar. Ein Agent, der „Wandflächen aller m²-
Positionen aus Räumen ziehen" automatisieren soll, hat keine
direkte Abfrage.

**Lücke:** Optionales Feld `Position.kategorie`
(`wand | decke | boden | vorarbeit | sonstiges`) — existiert bereits
auf Template-Position-Level, fehlt aber auf der echten Position. Beim
Instanzieren eines LVs aus einem Template müsste die Kategorie
mitkopiert werden.

### 4.11 Kein durchgängiger `updated_at`-Standard

Manche Modelle haben `updated_at` (Project, LV, Template, User), die
meisten nicht (Building, Floor, Unit, Room, Opening, Position,
Leistungsgruppe). Agenten können daher **nicht inkrementell
syncen** — kein `?changed_since=2026-04-01T00:00:00Z`-Filter möglich.

**Lücke:** `created_at` + `updated_at` (auto-touch via SQLAlchemy
event) auf allen Modellen + `?changed_since=`-Query-Param auf den
List-Endpoints.

### 4.12 Keine OpenAPI-getriebenen TypeScript-Types

Frontend hat **manuell gepflegte** TypeScript-Types
(`frontend/src/types/lv.ts`, `template.ts`). Bei Backend-Änderungen
können beide Seiten driften. Für Agenten ist das nicht relevant, aber
es signalisiert: **die OpenAPI-Spec wird in der Praxis nicht
konsumiert** — also wird sie auch im Code-Review nicht gepflegt.

**Lücke:** OpenAPI als **Single Source of Truth**, Frontend-Types
generiert (z. B. über `openapi-typescript`), Agent-MCP-Manifest
ebenfalls.

### 4.13 Berechnungsnachweise sind nur eingebettet erreichbar

Agenten würden gerne fragen: „Wie ist die Menge in Position X
zustande gekommen?" — heute geht das nur über `GET /lv/{id}`, der
das ganze LV-Tree zurückgibt. Bei großen LVs ist das eine
Megabyte-Antwort.

**Lücke:** `GET /lv/positionen/{id}/berechnungsnachweise` als
schmaler Endpoint.

### 4.14 Kein „Dry-Run"-Modus für Mutationen

Ein Agent möchte oft prüfen: „Was würde passieren, wenn ich diese
LV neu kalkuliere?" — ohne tatsächlich zu schreiben. Aktuell schreibt
jeder Calculate-Call in die DB.

**Lücke:** `?dry_run=true`-Param auf
`POST /lv/{id}/calculate`, `POST /rooms/{id}/calculate-walls` und
`POST /lv/{id}/sync-wall-areas` — Server berechnet und gibt das
Ergebnis zurück, persistiert aber nichts.

### 4.15 Kein strukturiertes Error-Format

Errors sind `{ "detail": "Nicht authentifiziert" }` oder freier Text
in deutscher Sprache. Agenten haben keinen **stabilen `error_code`**,
auf den sie programmatisch reagieren können (`PROJECT_LIMIT_REACHED`,
`PRO_FEATURE_REQUIRED`, `OWNERSHIP_DENIED` …).

**Lücke:** RFC-7807 (Problem Details) oder ein eigenes
`{ code, message, hint, docs_url }`-Schema, durchgereicht von einem
zentralen `HTTPException`-Handler.

### 4.16 Plan-Analyse ist stateful und nur poll-bar

`POST /plans/{id}/analyze` triggert Claude Vision asynchron, schreibt
das Ergebnis in `Plan.analysis_status` (`pending | running |
completed | failed`). Agenten müssen pollen — kein Webhook, kein
SSE, kein Long-Poll.

**Lücke:** Eines von:
- Outbound-Webhook (siehe 4.7),
- SSE-Endpoint `GET /plans/{id}/analyze/events`,
- oder zumindest Plan-Analyse-Endpoint synchron mit längerem
  Server-Timeout (für Agenten oft akzeptabel).

### 4.17 LV-Templates: keine semantische Suche

Templates können nur über `GET /templates` (gesamte Liste) abgefragt
werden — kein Volltext-Filter, keine Embedding-Suche. Mit
`pgvector` ist die DB darauf vorbereitet, aber es wird nicht
genutzt. Agenten können „finde mir das passende Template für
EFH-Sanierung im Trockenbau" heute nicht ohne clientseitige Logik.

**Lücke:** `POST /templates/search { query: "..." }` mit
Embedding-Vector-Match auf Name + Description + Kurztext-Korpus.

---

## 5. Erste Empfehlung für die nächsten Schritte

> Nicht Teil von Schritt 1 — nur als Sortierhilfe für den weiteren
> Verlauf.

| Priorität | Lücke | Begründung |
|---|---|---|
| **1** | 4.1 PAT/API-Keys | ohne agent-taugliche Auth bringt alles andere nichts |
| **1** | 4.2 MCP-Server | ist das Ziel des Projekts |
| **2** | 4.3 OpenAPI-Hardening | direkt agent-relevant, kein DB-Eingriff |
| **2** | 4.6 Rate-Limits | Schutz, sobald Agenten zugreifen |
| **2** | 4.10 Position.kategorie | macht Wand/Decke-Routing für Agenten machbar |
| **3** | 4.4 Pagination + Filter | sobald Datenmengen das nötig machen |
| **3** | 4.7 Outbound-Webhooks | nice-to-have für Plan-Analyse-Flows |
| **4** | 4.5 Idempotency, 4.8 Batch, 4.11 updated_at, 4.13 BN-Endpoint, 4.14 Dry-Run, 4.15 Error-Codes, 4.16 Plan-Stream, 4.17 Template-Search | Reifegrad-Schritte |

---

## 6. Was **nicht** angefasst werden darf

- Bestehende **Pfade**, **Methoden**, **Response-Felder** der API.
- DB-Schemata (Migrationen nur additiv: neue Spalten/Tabellen, keine
  Renames, keine Drops).
- JWT-Format (Frontend-Login muss weiter funktionieren).
- Service-Worker-Cache-Strategie (würde stale UIs in Produktion
  hervorrufen).
- Stripe-Webhook-Endpoint (Subscription-State würde driften).

Jede Erweiterung muss **additiv** sein. Frontend v17 darf gegen den
neuen Backend-Stand weiter arbeiten, ohne neu deployt zu werden.

---

**Ende der Analyse — keine Code-Änderungen vorgenommen.**
**Bitte Freigabe geben mit „OK weiter mit Schritt 2", wenn diese
Bestandsaufnahme passt.**

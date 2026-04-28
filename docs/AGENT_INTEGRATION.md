# BauLV mit KI-Agenten verbinden

> Diese Anleitung richtet sich an BauLV-Kunden, die ihre Projekte und
> Leistungsverzeichnisse direkt aus einem KI-Agenten heraus bearbeiten
> möchten — ohne dauernd zwischen Browser und Chat hin- und herzu-
> wechseln.

BauLV stellt einen **Model-Context-Protocol-Server (MCP)** bereit. Das
ist das Standardprotokoll, mit dem Claude Desktop, n8n,
ChatGPT-Custom-Connectors und ähnliche Tools mit externen
Anwendungen sprechen. Einmal eingerichtet, kann der Agent in Ihrem
Auftrag z. B. ein neues Projekt anlegen, eine Vorlage in das Projekt
kopieren und Positionen mit Preisen befüllen.

**Inhalt**

1. [Was geht damit konkret?](#was-geht-damit-konkret)
2. [Voraussetzungen](#voraussetzungen)
3. [Schritt 1 — API-Key erstellen](#schritt-1--api-key-erstellen)
4. [Schritt 2 — Agent verbinden](#schritt-2--agent-verbinden)
   - [Claude Desktop](#claude-desktop)
   - [n8n](#n8n)
   - [ChatGPT (Custom Connector)](#chatgpt-custom-connector)
   - [Eigene Skripte / Anthropic API](#eigene-skripte--anthropic-api)
5. [Erste Beispiele](#erste-beispiele)
6. [Verfügbare Tools im Überblick](#verfügbare-tools-im-überblick)
7. [Limits und Sicherheit](#limits-und-sicherheit)
8. [Fehlerbehebung](#fehlerbehebung)
9. [Datenschutz](#datenschutz)
10. [Kontakt](#kontakt)

---

## Was geht damit konkret?

Drei typische Szenarien aus dem Alltag eines Maler- oder
Bodenleger-Betriebs:

**Szenario 1 — Schnell ein neues LV aus der Vorlage**
> *„Lege im Projekt 'EFH Schmidt' ein neues LV 'Malerarbeiten EG' aus
> der System-Vorlage 'Malerarbeiten Standard' an und zeige mir
> danach die Gruppen mit ihren Positions-Anzahlen."*

Der Agent ruft `create_lv_from_template` auf, danach `get_lv` — Sie
bekommen das Ergebnis sofort im Chat zurück und können in BauLV mit
den Mengen und Preisen weitermachen.

**Szenario 2 — Mengenermittlung erklären lassen**
> *„Erkläre mir, wie die Menge von Position 'Wandflächen streichen'
> in LV 12345 zustande kommt."*

Der Agent ruft `get_position_with_proof` auf und übersetzt die
Berechnungsnachweise (Raum, Wandfläche brutto, ÖNORM-Faktor,
Öffnungsabzüge) in eine Erklärung für den Auftraggeber.

**Szenario 3 — Preise tunen**
> *„Setze in LV 12345 alle Positionen mit Einheit 'm²' auf
> Einheitspreis 12,50 € und sperre die Positionen danach."*

Der Agent iteriert mit `update_position` über jede Position. Dank
des „erst entsperren, dann ändern"-Workflows kann er bereits gesperrte
Positionen nicht versehentlich überschreiben — das schützt Sie vor
Eingabefehlern.

---

## Voraussetzungen

- Ein aktiver BauLV-Account unter <https://baulv.at>.
- Ein **MCP-fähiger Client**. Empfohlene Optionen:
  - **Claude Desktop** (kostenlos, einfachste Einrichtung)
  - **n8n** (für Automatisierungen, Self-Hosted oder Cloud)
  - **ChatGPT** mit aktivem Custom-Connectors-Feature
  - Eigene Skripte mit dem Anthropic-SDK
- Eine Internetverbindung — der MCP-Server läuft in der Cloud, nicht
  lokal.

---

## Schritt 1 — API-Key erstellen

API-Keys (auch *Personal Access Tokens*, kurz **PAT**) sind die
Logindaten Ihres Agenten. Jeder Token ist persönlich, kann jederzeit
widerrufen werden und sieht ausschließlich die Daten **Ihres
Accounts** — niemand anders.

1. Im Browser unter <https://baulv.at/app/api-keys> einloggen.
2. Auf **„Neuer Schlüssel"** klicken.
3. Im Dialog ausfüllen:
   - **Name** — z. B. *„Claude Desktop Büro-Mac"* oder *„n8n
     Auto-Workflow"*. Hilft später, einzelne Tokens auseinander-
     zuhalten.
   - **Ablaufzeit** — *Unbegrenzt*, *30 Tage*, *90 Tage* oder
     *365 Tage*. Für produktive Setups empfehlen wir 365 Tage; für
     einmalige Tests reicht 30 Tage.
4. **Wichtig:** Den Token-Plaintext **sofort kopieren** — er wird
   nach dem Schließen des Dialogs nie wieder angezeigt. Wenn Sie ihn
   verlieren, müssen Sie einen neuen erstellen.

Der Token sieht so aus:

```
pat_3a2b1c0d9e8f7g6h5i4j3k2l1m0n9o8p7q6r5s4t3u2v
```

**Niemals weitergeben** — wer den Token hat, kann auf Ihre Projekte
zugreifen. Bei Verdacht auf Kompromittierung sofort über das
Mülleimer-Icon in der API-Key-Liste widerrufen.

---

## Schritt 2 — Agent verbinden

### Claude Desktop

1. Stellen Sie sicher, dass Sie die aktuelle Version von Claude
   Desktop installiert haben (<https://claude.ai/download>).
2. Öffnen Sie die Konfigurationsdatei:
   - **macOS**:
     `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**:
     `%APPDATA%\Claude\claude_desktop_config.json`
3. Fügen Sie folgenden Block unter `mcpServers` ein:

```json
{
  "mcpServers": {
    "baulv": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://baulv.at/mcp/sse",
        "--header",
        "Authorization: Bearer pat_IHR_TOKEN_HIER"
      ]
    }
  }
}
```

> **Warum `mcp-remote`?** Claude Desktop spricht von Haus aus
> stdio-MCP. Das `mcp-remote`-Hilfsprogramm (offizielles npm-Paket)
> brückt unseren SSE-HTTP-Endpoint auf stdio. Wird automatisch via
> `npx` heruntergeladen — Sie müssen nichts manuell installieren.

4. **Claude Desktop komplett neu starten** (nicht nur das Fenster
   schließen). Beim Start sehen Sie unten rechts ein
   Schraubenschlüssel-Symbol mit dem Hinweis, dass `baulv` verbunden
   ist.
5. Im Chat „**Listet meine BauLV-Projekte auf**" eingeben — Claude
   bestätigt einmalig die Tool-Nutzung und liefert die Liste.

### n8n

n8n hat einen nativen *MCP Client*-Node ab Version 1.78.

1. Im Workflow den Node **„MCP Client"** hinzufügen.
2. Konfiguration:
   - **Server URL**: `https://baulv.at/mcp/sse`
   - **Transport**: `sse`
   - **Authentication**: `Header Auth`
   - **Header Name**: `Authorization`
   - **Header Value**: `Bearer pat_IHR_TOKEN_HIER`
3. Im Operation-Feld:
   - **List Tools** — zeigt alle 15 verfügbaren BauLV-Tools.
   - **Execute Tool** — wählt ein Tool aus und führt es aus.
4. Beispiel-Workflow: *„Cron alle 24 h → MCP Client `list_projects`
   → Filter nach `status='aktiv'` → E-Mail an Auftraggeber"*.

### ChatGPT (Custom Connector)

Setzt einen Plus-/Team-/Enterprise-Account voraus, bei dem Custom
Connectors freigeschaltet sind.

1. In ChatGPT in den **Settings → Connectors → Add Connector**.
2. Format **„Model Context Protocol"** wählen.
3. Felder:
   - **Server URL**: `https://baulv.at/mcp/sse`
   - **Auth Type**: `Bearer Token`
   - **Token**: `pat_IHR_TOKEN_HIER`
4. Speichern und im Chat den Connector aktivieren.

Hinweis: Custom Connectors sind in einigen Regionen noch im Rollout.
Falls die Option fehlt, ist Ihr Account vermutlich noch nicht
freigeschaltet.

### Eigene Skripte / Anthropic API

Für Entwickler, die direkt mit dem Anthropic-Python-SDK arbeiten,
gibt es das offizielle MCP-Client-Paket:

```bash
pip install mcp anthropic
```

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

async def main():
    headers = {"Authorization": "Bearer pat_IHR_TOKEN_HIER"}
    async with sse_client(
        "https://baulv.at/mcp/sse", headers=headers
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

            result = await session.call_tool("list_projects", {})
            print(result.content[0].text)

asyncio.run(main())
```

Den `Anthropic-Messages-Loop` (Tool-Verwendung im LLM-Call) zeigen
wir in <https://baulv.at/llms.txt> nicht — dafür sind die
SDK-Beispiele in <https://docs.anthropic.com> die Referenz.

---

## Erste Beispiele

Beispiel-Prompts, mit denen Sie testen können, ob die Verbindung
steht. Alles, was in **fett** steht, kopieren Sie 1:1 in den Chat
Ihres Agenten:

- **„Listet meine BauLV-Projekte auf."**
  → Smoke-Test. Wenn Sie die Liste sehen, ist die Auth korrekt.
- **„Welche LV-Vorlagen für das Gewerk Malerarbeiten gibt es?"**
  → Ruft `list_templates` mit `gewerk='malerarbeiten'` auf.
- **„Lege ein neues Projekt 'Test-EFH 2026' an."**
  → Ruft `create_project` auf. Plan-Limit wird respektiert (Basis:
  3 Projekte; Pro/Enterprise: unbegrenzt).
- **„Zeige mir die Raumstruktur von Projekt {ID}."**
  → Ruft `get_project_structure` auf — vollständiger Gebäudebaum.
- **„Erkläre mir die Berechnung von Position {ID} im Detail."**
  → Ruft `get_position_with_proof` auf — Rohdaten mit allen
  Berechnungsnachweisen, die der Agent in eine kundentaugliche
  Erklärung übersetzt.

---

## Verfügbare Tools im Überblick

Insgesamt **15 Tools**: 8 lesend, 7 schreibend. Die vollständigen
Argument-Schemas liefert der `tools/list`-Aufruf zur Laufzeit — Ihr
Agent kennt sie automatisch.

### Lesend

| Tool                       | Zweck                                            |
|----------------------------|--------------------------------------------------|
| `list_projects`            | Alle Projekte des Users.                         |
| `get_project`              | Stammdaten eines Projekts.                       |
| `get_project_structure`    | Gebäudebaum: Buildings → Floors → Units → Rooms. |
| `list_rooms`               | Flache Raumliste mit Geometrie.                  |
| `list_lvs`                 | Alle LVs eines Projekts.                         |
| `get_lv`                   | LV komplett mit Gruppen + Positionen.            |
| `get_position_with_proof`  | Position + Berechnungsnachweise (ÖNORM B 2210).  |
| `list_templates`           | Vorlagen (System + eigene), filterbar.           |

### Schreibend

| Tool                         | Zweck                                              |
|------------------------------|----------------------------------------------------|
| `create_project`             | Neues Projekt (respektiert Plan-Limit).            |
| `update_project`             | Projekt-Metadaten patchen.                         |
| `create_lv`                  | Leeres LV anlegen.                                 |
| `create_lv_from_template`    | Standard-Flow: Vorlage in Projekt kopieren.        |
| `update_lv`                  | LV-Metadaten patchen.                              |
| `update_position`            | Einzelne Position bearbeiten + Sperr-Logik.        |
| `create_template_from_lv`    | LV als wiederverwendbare Vorlage speichern.        |

### Bewusst nicht verfügbar

- **Hard-Deletes** — Projekte/LVs/Räume können nicht über den Agenten
  gelöscht werden. Das ist Absicht: zu großer Schaden, falls der Agent
  etwas falsch versteht. Löschen geht weiterhin nur in der Web-UI.
- **Building/Floor/Unit/Room-CRUD** — Gebäudestruktur lässt sich
  besser in der UI mit Vorschau bearbeiten.
- **`calculate-lv` / `sync-wall-areas`** — laufen automatisch nach
  Mutationen, kein separater Trigger nötig.

---

## Limits und Sicherheit

### Rate-Limits

Pro Token gilt:

- **60 Anfragen pro Minute**
- **1.000 Anfragen pro Tag**

Das deckt selbst aggressive Automatisierungen problemlos ab. Wird ein
Limit erreicht, antwortet der Server mit einer deutschen
Fehlermeldung im Tool-Result inkl. „bitte in N Sekunden erneut
versuchen". Ihr Agent kann das selbst auswerten.

JWT-Logins aus der Web-UI fallen *nicht* unter dieses Limit — diese
Anleitung gilt nur für PAT-Tokens.

### Audit-Log

Jeder Tool-Aufruf wird protokolliert: Wann, welcher Token, welches
Tool, welche Argumente, welches Ergebnis, wie lange. Sichtbar pro
Token unter <https://baulv.at/app/api-keys> über das
Audit-Log-Symbol.

Das ist sowohl für Ihre eigene Nachvollziehbarkeit nützlich („was hat
mein n8n-Workflow heute Nacht gemacht?") als auch DSGVO-Art-32-konform.

### Token-Hygiene

- **Kurzlebige Tokens für Tests, langlebige für Produktion.**
  30 Tage für „mal schnell ausprobieren", 365 Tage oder unbegrenzt
  für laufende Automatisierungen.
- **Pro Anwendung ein eigener Token.** Wenn Sie sowohl Claude Desktop
  als auch einen n8n-Workflow betreiben, geben Sie beiden eigene
  Tokens — das macht spätere Rotation einfacher.
- **Bei Verdacht: sofort revoken.** In der API-Key-Liste auf das
  Mülleimer-Symbol klicken. Der Token ist ab dem nächsten Aufruf
  nicht mehr gültig.

---

## Fehlerbehebung

| Symptom                                              | Ursache & Lösung                                                                           |
|------------------------------------------------------|--------------------------------------------------------------------------------------------|
| **„401 Unauthorized" bei Verbindungsaufbau**         | Token falsch kopiert oder bereits widerrufen. Neuen Token erstellen.                       |
| **„Token ist abgelaufen"**                           | `expires_at` erreicht. Im API-Key-Dialog Ablaufzeit verlängern oder neuen Token anlegen.   |
| **„Rate-Limit erreicht (burst). Bitte in N Sekunden erneut versuchen."** | 60-RPM-Burst überschritten. Agent macht einen kurzen Pause-Modus, dann weiter.             |
| **„Rate-Limit erreicht (day). Bitte in N Sekunden erneut versuchen."**   | 1000/Tag erreicht. Reset um Mitternacht UTC. Bei Bedarf Kontakt aufnehmen.                 |
| **„Projekt-Limit für Plan basis erreicht (3 Projekte)"** | `create_project` über Plan-Quota. Pro-Plan zeichnen oder ein bestehendes Projekt archivieren.|
| **Claude Desktop zeigt keinen Schraubenschlüssel**   | `claude_desktop_config.json` Syntax-Fehler — JSON validieren (z. B. <https://jsonlint.com>). Logs unter Hilfe → Logs öffnen. |
| **n8n zeigt „SSE connection closed"**                | Veraltete n8n-Version; mind. v1.78 benötigt. Oder Bearer-Token ohne `Bearer `-Präfix.       |
| **Tool gibt „Fehler: Position ist gesperrt"**        | Beabsichtigte Sperr-Logik. Erst `update_position` mit `{is_locked: false}` allein, dann mit den Änderungen. |

---

## Datenschutz

- **Tenant-Isolation:** Ihr Token sieht ausschließlich die Daten Ihres
  BauLV-Accounts. Cross-Account-Zugriffe sind auf Datenbank-Ebene
  blockiert.
- **Hash, kein Klartext:** Wir speichern nur den SHA-256-Hash des
  Tokens, niemals den Plaintext. Selbst ein Datenbank-Leak könnte
  keinen Token rekonstruieren.
- **Audit-Trail:** Bei DSGVO-Auskunft oder -Löschung erhalten Sie
  bzw. der zuständige Datenschutzbeauftragte den vollständigen
  Audit-Log Ihres Accounts. Bei vollständiger Account-Löschung
  bleibt der Trail mit `NULL`-Verweis erhalten (DSGVO Art. 17 vs.
  Art. 32 — Compliance-Pflicht).
- **Keine Datenübertragung an Anthropic/OpenAI:** Ihr KI-Anbieter
  (Anthropic, OpenAI, …) sieht nur das, was *Ihr Agent* in der
  Konversation an ihn schickt. BauLV liefert die Daten *direkt an
  Ihren Client* — wir reichen nichts an Dritte weiter.

---

## Kontakt

Fragen, Feature-Wünsche oder Probleme:

- E-Mail: <kontakt@baulv.at>
- Status & Outages: <https://baulv.at>

Maschinen-lesbare Discovery-Files für andere Agenten:

- <https://baulv.at/llms.txt>
- <https://baulv.at/.well-known/mcp.json>

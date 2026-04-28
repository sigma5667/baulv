# BauLV MCP-Demo — 45-Sekunden-Video-Drehbuch

> **Zweck.** Ein Marketing- und Vertriebs-Video, das den
> MCP-Anschluss in Aktion zeigt: Claude Desktop legt auf Zuruf ein
> komplettes Maler-LV in BauLV an — der Zuschauer soll danach das
> Gefühl haben „das will ich auch".
>
> **Zielgruppe.** Maler- und Bodenleger-Betriebe in Österreich,
> Investoren, Messe-Zuschauer.
>
> **Format.** 45 Sekunden, Hochkant oder Querformat (16:9), mit oder
> ohne Voiceover. Kann auf Website, LinkedIn, YouTube-Pre-Roll und im
> Vertriebs-Pitch laufen.

---

## Bildschirmaufteilung

**Split-Screen 50/50** — der Zuschauer sieht die ganze Zeit beide
Welten gleichzeitig:

```
┌──────────────────────────┬──────────────────────────┐
│                          │                          │
│   Claude Desktop         │   BauLV im Browser       │
│   (linke Hälfte)         │   (rechte Hälfte)        │
│                          │                          │
│   Chat-Fenster, leer     │   Dashboard /            │
│   am Anfang              │   Projektliste           │
│                          │                          │
│   MCP-Status: 🟢 baulv   │   Eingeloggt als         │
│                          │   Demo-User              │
│                          │                          │
└──────────────────────────┴──────────────────────────┘
```

**Auflösung:** 1920 × 1080 (16:9). Beide Fenster jeweils 960 × 1080.

**Warum Split-Screen statt Tab-Switching?** Tempo. In 45 Sekunden ist
keine Zeit für „und jetzt schauen wir kurz drüben in BauLV" — die
Magie passiert sichtbar parallel.

---

## Vorbereitungs-Checkliste

### Demo-Account in BauLV
- [ ] Sauberen Test-Account anlegen (`demo-video@baulv.at`).
- [ ] Pro-Plan freischalten (entweder via Stripe-Test oder
      `docs/ADMIN.md`-Bypass), damit das Projekt-Limit kein Thema ist.
- [ ] **Dashboard muss leer sein** — keine Bestandsprojekte, sonst
      ist die „neue Karte ploppt auf"-Wirkung weg.
- [ ] Verifizieren: Es gibt eine LV-Vorlage *für Malerarbeiten* —
      sonst greift Claudes `list_templates`-Aufruf ins Leere.
      Empfehlung: System-Vorlage „Malerarbeiten Standard EFH"
      (Kategorie `einfamilienhaus`, Gewerk `malerarbeiten`).
      Falls die Vorlage anders heißt, im Drehbuch unten den Namen
      anpassen.

### PAT + Claude Desktop
- [ ] Unter `https://baulv.at/app/api-keys` einen PAT für „Demo
      Video" erstellen, 90 Tage Ablauf reicht.
- [ ] `~/Library/Application Support/Claude/claude_desktop_config.json`
      (macOS) bzw. `%APPDATA%\Claude\claude_desktop_config.json`
      (Windows) konfigurieren — siehe
      `docs/AGENT_INTEGRATION.md` Abschnitt *Claude Desktop*.
- [ ] Claude Desktop **vollständig neu starten**, nicht nur Fenster
      schließen.
- [ ] Smoke-Test: einmalig „Liste meine Projekte" — muss eine leere
      Liste zurückgeben. Das warm-up startet das `mcp-remote`-Backend
      und sorgt dafür, dass der Verbindungsindikator (🟢) im
      Aufnahme-Take grün ist.

### Aufnahme-Setup
- [ ] System-Benachrichtigungen aus (macOS *Do Not Disturb*,
      Windows *Fokussierungsassistent*).
- [ ] Browser-Tab-Bar leer räumen — nur der BauLV-Tab.
- [ ] Browser-Vollbild (Cmd/Strg + L → schmaler URL-Bar) oder mind.
      Lesezeichen-Leiste ausblenden.
- [ ] Cursor-Highlighting aktivieren (z. B. `Mouseposé`, `Cursor
      Highlighter`) — der Zuschauer soll sehen, was geklickt wird.
- [ ] Tipp-Geschwindigkeit: ruhig in normalem Tempo aufnehmen — das
      Tippen wird in der Post-Produktion **2–3× beschleunigt**, damit
      der Prompt in 5–6 Sekunden steht.
- [ ] Den genauen Prompt **vorher in eine Notiz-App kopieren**. Nicht
      live formulieren — Tippfehler kosten Takes.

### Cache & Daten
- [ ] BauLV-Tab eine Minute vorher öffnen, damit das Dashboard
      gerendert ist (kein React-Hydration-Flackern im Take).
- [ ] Eingeloggt sein — kein Login-Screen im Take.
- [ ] React-Query-Auto-Refresh: BauLV pollt die Projektliste **nicht**
      automatisch, wenn der Agent etwas anlegt. Du brauchst entweder
      einen unauffälligen `F5` zum richtigen Zeitpunkt oder du
      bleibst auf der Projektliste und navigierst zum neuen Projekt
      durch Klick auf die nach `F5` erscheinende Karte. Plan dafür
      die jeweils 1 Sekunde Pause nach Tool-Calls ein — siehe
      Drehbuch unten.

---

## Aufnahme-Software

| Tool | Plattform | Kosten | Geeignet für | Anmerkung |
|------|-----------|--------|--------------|-----------|
| **OBS Studio** | macOS / Windows / Linux | gratis | beste Qualität, mehrere Quellen + Voiceover | leichte Lernkurve, ~30 Min Setup |
| **Loom** | Web / Desktop-App | gratis (5 Min Limit reicht für 45 s) | schnellster Workflow, automatische Cloud-Links | weniger Editing-Optionen |
| **macOS-eigen** | macOS | gratis | spontaner Take, nur Bild | Cmd+Shift+5 → Bildschirmaufnahme; Voiceover muss separat aufgenommen + zusammengeschnitten werden |
| **Snipping Tool** | Windows 11 | gratis | wie macOS-eigen | Win+Shift+R |

**Empfehlung:** **OBS Studio** für die finale Marketing-Version (hohe
Qualität, exporte als MP4 mit H.264), **Loom** als schneller
Iterations-Take während die Choreografie noch nicht sitzt.

---

## Sekunden-Drehbuch

> Spalte „🟢 Tool" zeigt, was Claude im Hintergrund über MCP aufruft.
> Spalte „🎬 Animation" zeigt, was im BauLV-Browser sichtbar passiert.

### 00:00 – 00:05 ‣ Setup-Shot

| Sekunde | Linke Hälfte (Claude Desktop) | Rechte Hälfte (BauLV) | 🟢 Tool | 🎬 Animation | Voiceover |
|---------|-------------------------------|-----------------------|---------|--------------|-----------|
| 00:00 | Leeres Chat-Fenster, Cursor blinkt im Eingabefeld. Unten rechts kleiner 🟢 mit „baulv" — der MCP-Indikator. | Dashboard / Projektliste, **leer**. Großer „+ Neues Projekt"-Button. | — | statisch | *(Stille; oder Claim-Karte, siehe Variante B)* |
| 00:05 | unverändert | unverändert | — | statisch | — |

**[SCREENSHOT: Setup-Shot mit MCP-Indikator 🟢 grün und leerer
BauLV-Projektliste — das ist die Eröffnungs-Frame.]**

### 00:05 – 00:15 ‣ Prompt eingeben & Projekt anlegen

| Sekunde | Linke Hälfte | Rechte Hälfte | 🟢 Tool | 🎬 Animation | Voiceover |
|---------|--------------|---------------|---------|--------------|-----------|
| 00:05 – 00:10 | User tippt den Prompt (siehe unten). Tippen in der Post-Production **2–3× beschleunigen**, damit der ganze Text in ~5 s steht. | unverändert leer | — | — | *„Erstell mir ein Rohbau-LV — sagen Sie es einfach."* |
| 00:10 | Prompt komplett, User drückt **Enter**. | unverändert | — | — | — |
| 00:11 – 00:13 | Claude antwortet im Stream: *„Verstanden — ich lege das Projekt 'EFH Beispielstraße' für Sie an…"* | leer (kurze Latenz) | `create_project` | — | — |
| 00:13 – 00:15 | Claude-Antwort wächst weiter | **Neue Projekt-Karte ploppt auf** mit slide-in-from-top-Animation. Karte zeigt: Name, Adresse „Beispielstraße 1, 5020 Salzburg", Status „aktiv". | — | Karte fadet ein (200 ms) | *„… und das Projekt steht."* |

**Genauer Prompt (zum 1:1-Kopieren in die Notiz-App):**

```
Erstell mir ein Rohbau-LV für mein neues EFH-Projekt, Adresse
Beispielstraße 1, 5020 Salzburg, mit Standard-Malerarbeiten-Gewerk.
```

**[SCREENSHOT: Frame bei 00:14 — Claude links mit „Projekt angelegt",
rechts die frisch eingeflogene Projekt-Karte.]**

> ⚠️ **Operator-Hinweis:** Bei 00:13 unauffälliger `F5` auf den
> BauLV-Tab. Der Browser zeigt kurz einen Reload-Spinner — in der
> Post-Production diesen einen Frame entweder akzeptieren oder durch
> Crossfade glätten.

### 00:15 – 00:30 ‣ Vorlage suchen & LV befüllen

| Sekunde | Linke Hälfte | Rechte Hälfte | 🟢 Tool | 🎬 Animation | Voiceover |
|---------|--------------|---------------|---------|--------------|-----------|
| 00:15 – 00:18 | Claude tippt weiter: *„Ich suche eine passende LV-Vorlage für Malerarbeiten…"* | Projekt-Karte sichtbar | `list_templates` mit `gewerk: "malerarbeiten"` | — | — |
| 00:18 – 00:21 | *„Ich verwende die Vorlage 'Malerarbeiten Standard EFH'."* | unverändert | — | — | *„Standard-Vorlage gefunden."* |
| 00:21 – 00:24 | Claude: *„Kopiere Vorlage in das neue Projekt…"* | Operator klickt 1× auf die Projekt-Karte → wechselt in die LV-Übersicht des Projekts (noch leer). | `create_lv_from_template` | LV-Übersicht zeigt sich, leer | — |
| 00:24 – 00:30 | Claude streamt die Bestätigung | **LV-Karte „Malerarbeiten" erscheint mit slide-in.** Beim Hinein-Klick: Liste mit ~12 Positionen wird **zeilenweise gerendert** (stagger 60 ms pro Zeile). | — | LV-Karte fade-in 200 ms; Positionsliste stagger-in 60 ms × 12 | *„Aus der Vorlage werden zwölf Positionen kopiert."* |

**[SCREENSHOT: Frame bei 00:28 — Mitten im Stagger-Effekt, drei
Zeilen schon sichtbar, drei am Reinrutschen.]**

> ⚠️ **Operator-Hinweis:** Zwei Refreshes nötig. Erster bei 00:21
> (LV-Übersicht des Projekts laden), zweiter bei 00:24 (neues LV
> abholen). Plan beide Refreshs in den Take-Pausen ein — wir nutzen
> die jeweils 0,5 s zwischen Claudes Antworten.

### 00:30 – 00:40 ‣ Inspektion & Berechnung

| Sekunde | Linke Hälfte | Rechte Hälfte | 🟢 Tool | 🎬 Animation | Voiceover |
|---------|--------------|---------------|---------|--------------|-----------|
| 00:30 – 00:33 | Claude (kleiner): *„Fertig — 12 Positionen kopiert."* | Operator wechselt mit dem Mauszeiger ostentativ in die rechte Hälfte. Der Cursor-Highlighter macht den Sprung sichtbar. | — | — | *„Der Maler schaut nur noch drüber."* |
| 00:33 – 00:36 | Claude-Antwort steht still im Hintergrund | User klickt auf Position 3 *(„Wandflächen streichen, weiß, 2 Anstriche")*. Detail-Panel klappt auf — Mengen-Spalte leer (Räume noch nicht modelliert), aber Kurztext, Langtext, Einheit „m²" und Einheitspreis-Default sind da. | — | Position-Detail expand-Animation | — |
| 00:36 – 00:40 | unverändert | User scrollt einmal kurz durch die Positionsliste — der Zuschauer sieht: das ist eine richtige LV, nicht ein Mockup. | — | scroll | *„Eine vollständige Ausschreibung — bereit für Mengen und Preise."* |

**[SCREENSHOT: Frame bei 00:35 — Position-Detail offen, mit echten
Texten aus der Vorlage.]**

### 00:40 – 00:45 ‣ Claim-Frame

| Sekunde | Vollbild (kein Split-Screen mehr) | Voiceover |
|---------|-----------------------------------|-----------|
| 00:40 – 00:45 | Hartes Cut auf eine **Vollbild-Claim-Karte**. Schwarzer Hintergrund, weißer Text, BauLV-Logo zentriert. Drei Zeilen, jede mit kurzer Fade-In-Verzögerung (300 ms / 700 ms / 1000 ms). | *„BauLV — Ihre Bau-Ausschreibung. Mit oder ohne Agent."* |

**Claim-Karten-Inhalt:**

```
        ┌─────────────────────────────────────────┐
        │                                         │
        │              [BauLV-Logo]               │
        │                                         │
        │   BauLV — Ihre Bau-Ausschreibung.       │
        │   Mit oder ohne Agent.                  │
        │                                         │
        │              baulv.at                   │
        │                                         │
        └─────────────────────────────────────────┘
```

**[SCREENSHOT: Final-Frame Claim-Karte — wird in jedem Pitch-Deck
auch als Standbild verwendet.]**

---

## Voiceover

### Variante A — Technisch (für Investoren / Branchenmessen)

> *„Mit BauLV können KI-Agenten direkt für Sie arbeiten — Projekt
> anlegen, LV aus Vorlage befüllen, alles in unter 30 Sekunden.
> Standardprotokoll, ein Token, fertig."*

Verteilung über das Video:

- **00:05** *„Mit BauLV können KI-Agenten direkt für Sie arbeiten —"*
- **00:15** *„— Projekt anlegen, LV aus Vorlage befüllen,"*
- **00:25** *„alles in unter 30 Sekunden."*
- **00:38** *„Standardprotokoll. Ein Token. Fertig."*
- **00:42** *„BauLV. Mit oder ohne Agent."*

### Variante B — Emotional (für Endkunden / Social Media)

> *„Was früher Stunden dauerte, geht jetzt in Sekunden — sagen Sie
> einfach, was Sie brauchen."*

Verteilung über das Video:

- **00:00** *(Stille — die Anspannung „leeres Chat-Fenster" wirkt für sich)*
- **00:08** *„Was früher Stunden dauerte —"*
- **00:18** *„— geht jetzt in Sekunden."*
- **00:30** *„Sagen Sie einfach, was Sie brauchen."*
- **00:42** *„BauLV. Mit oder ohne Agent."*

**Empfehlung:** Variante B für die öffentliche Erstveröffentlichung
(LinkedIn, Website-Hero), Variante A für Investoren-Decks.

**Stimme:** Männlich oder weiblich, österreichisches Standard-Deutsch
(kein hartes Wienerisch, kein Hochdeutsch). Empfehlung: über
**ElevenLabs** in deutscher Stimme synthetisieren — kostet weniger
als 2 € pro Take und klingt 2026 indistinguishable von einem
Profi-Sprecher.

---

## Post-Production-Checkliste

- [ ] Tipp-Sequenz bei 00:05 – 00:10 auf 2–3× beschleunigen.
- [ ] Refresh-Frames bei 00:13, 00:21, 00:24 mit Crossfade glätten
      (200 ms reicht).
- [ ] Cursor-Highlight komplett rausrendern, falls die Aufnahme das
      System-Cursor-Bild zeigt — dann die Highlight-Spur in der
      Post-Production drüberlegen, sonst sieht der Cursor billig aus.
- [ ] Voiceover als separate Spur, **nicht** als On-Set-Mikrofon-Take —
      das gibt sauberere Pegel und du kannst den deutschen Voiceover
      gegen einen englischen Take austauschen, wenn das Video später
      international laufen soll.
- [ ] Hintergrund-Musik dezent (–30 dB), instrumental, kein Gesang.
      Empfehlung: **Epidemic Sound** unter „Corporate / Tech / Soft
      Lo-Fi".
- [ ] Export-Setting: 1920 × 1080, 30 fps, H.264, ~6 Mbit/s.
      Größe ~30 MB für 45 s — geht überall durch.

---

## Bekannte Stolperfallen

| Stolperfalle | Symptom | Fix |
|--------------|---------|-----|
| **Tool-Call dauert > 2 s** | Claudes Stream stockt, Take wirkt zäh. | Demo-Account regional nahe am Railway-Cluster verwenden (EU-West). PAT vor dem Take einmal warm-laufen lassen. |
| **Vorlage existiert nicht** | `list_templates` liefert leeres Array, Claude bittet um Klärung — der Take bricht. | Vor dem Take im UI sicherstellen, dass „Malerarbeiten Standard EFH" als System-Vorlage vorhanden ist. |
| **Plan-Limit greift** | `create_project` antwortet mit „Projekt-Limit erreicht". | Demo-Account auf Pro-Plan oder Beta-Unlock setzen (siehe `docs/ADMIN.md`). |
| **MCP-Indikator nicht grün** | 🟢 fehlt in der Setup-Frame. | Claude Desktop neu starten. Beim ersten Tool-Call (Smoke-Test) wartet `mcp-remote` darauf, dass `npx` das Paket lädt — dieser Cold-Start kostet 5–8 s. Vor dem Take einmal triggern. |
| **Browser fragt nach Cookies/Tracking** | Hässlicher Banner verdeckt das Dashboard. | In der Demo-Browser-Profil eine Banner-Blocker-Extension (uBlock Origin) installieren oder das Cookie-Consent vorab wegklicken. |
| **Reload zeigt Loading-Spinner** | Der eine Frame zwischen `F5` und „Daten da" ruckelt. | In der Post-Production: 1 Frame rausschneiden oder mit 200 ms Crossfade glätten. |

---

## Liefer-Variante

Aus diesem 45-Sekunden-Take lassen sich **drei Cuts** ableiten:

1. **Hauptvideo (45 s)** — wie oben, für Website-Hero, LinkedIn,
   YouTube-Pre-Roll.
2. **Kurz-Cut (15 s)** — nur 00:05–00:20 (Prompt → Projekt-Karte) +
   Claim-Frame. Für Stories, Reels, Twitter/X.
3. **Stand-Frame (Sekunde 00:30, statisch)** — die Frame mit
   Position-Detail offen + LV vollständig. Für Pitch-Decks, das
   Hero-Bild der Landing-Page, Presse-Kit.

Alle drei aus einer einzigen guten Aufnahme — also: **bei einem Take
wirklich Mühe geben**, nicht bei drei mittelmäßigen.

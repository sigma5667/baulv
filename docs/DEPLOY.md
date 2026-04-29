# Deploy-Checkliste — Frontend Build-Tag bumpen

> **Pflicht-Lektüre vor jedem Frontend-Push.** Wer die Checkliste
> überspringt, riskiert dass Deploys auf Railway zwar grün durchlaufen,
> aber an User-Browsern nicht ankommen — der Service Worker und der
> Kill-Switch greifen nicht, und der User sieht weiter den alten Stand.

## Was ist der Build-Tag?

Eine Versionszeichenkette, die exakt **zweimal** im Repo vorkommt und
**bei jedem Frontend-Commit synchron gebumped werden muss**:

| Datei | Zeile | Konstante |
|-------|-------|-----------|
| `frontend/src/main.tsx` | ~68 | `APP_BUILD_TAG` |
| `frontend/public/sw.js` | ~37 | `CACHE_NAME` |

**Beide Strings müssen byte-identisch sein.** Eine Differenz von einem
einzigen Zeichen reicht, damit der Cache-Bust nur halb greift — der
Browser bleibt mit einer Mischung aus alter index.html und neuem Bundle
hängen, und das Verhalten ist nicht reproduzierbar debuggbar.

## Format

```
baulv-vNN-YYYY-MM-DD-stichwort
```

| Segment | Beispiel | Erklärung |
|---------|----------|-----------|
| `baulv` | `baulv` | fest, kein anderer Cache-Namespace darf kollidieren |
| `vNN` | `v21`, `v21.1` | passt zur v-Nummer im Commit-Subject |
| `YYYY-MM-DD` | `2026-04-29` | Datum des Pushes (nicht der ersten Code-Änderung) |
| `stichwort` | `inline-edit`, `mcp-mutations`, `hotfix-cache` | knappes Kürzel, kleinbuchstaben, kein Umlaut |

Beispiele aus dem Repo:

- `baulv-v18-2026-04-27-stable-ids`
- `baulv-v21-2026-04-29-inline-edit`
- `baulv-v21-2026-04-29-hotfix-cache` *(falls am selben Tag zwei Pushes)*

## Wann bumpen?

### Pflicht bumpen
- Irgendeine Änderung in `frontend/src/**`
- Irgendeine Änderung in `frontend/public/**` (außer `llms.txt` /
  `.well-known/` — die sind reine Discovery-Files und nicht im
  Cache-Pfad des SWs)
- Änderungen an `frontend/index.html`, `frontend/vite.config.ts`,
  `frontend/package.json` (anything that changes the bundle output)

### Nicht bumpen
- Nur Backend (`backend/**`)
- Nur Doku (`docs/**`, README, Markdown)
- Nur Tests (`backend/tests/**`, falls die das Bundle nicht beeinflussen)
- Nur CI / Infra (`.github/`, `railway.toml`, Dockerfile)

Faustregel: hat sich irgendwas geändert, das ins `dist/`-Build-Output
fließt? Dann bumpen.

## Checkliste vor jedem Push

Vor `git push`, manuell oder als Mental-Loop:

- [ ] Hat dieser Commit Frontend-Code/-Assets geändert?
  → Wenn ja, weiter. Wenn nein, fertig.
- [ ] Neuen Tag-String festgelegt im Format `baulv-vNN-YYYY-MM-DD-stichwort`?
- [ ] **`frontend/src/main.tsx`**: `APP_BUILD_TAG` auf den neuen
      Wert gesetzt?
- [ ] **`frontend/public/sw.js`**: `CACHE_NAME` auf den **identischen**
      Wert gesetzt?
- [ ] Schnell-Diff: stehen beide Werte byte-identisch da?

  ```bash
  grep -E "APP_BUILD_TAG|CACHE_NAME" \
    frontend/src/main.tsx \
    frontend/public/sw.js
  ```

  Beide Zeilen müssen denselben Suffix tragen.
- [ ] Beide Dateien im selben Commit?
  → Sonst gibt es zwischen den beiden Commits ein Build-Fenster, in dem
    die Zwischenstand-Deploy-Lieferung kaputt ist. Lieber zusammen.

## Verifikation nach dem Deploy

1. Nach Railway-Deploy einen Hard-Reload im normalen Browser:

   ```
   curl https://baulv.at/sw.js | grep CACHE_NAME
   ```

   muss den **neuen** Tag liefern.

2. In der Browser-Konsole:

   ```js
   localStorage.baulv_build_tag
   ```

   muss nach genau einem zusätzlichen Reload ebenfalls auf den neuen
   Tag stehen. Der erste Reload triggert den Kill-Switch
   (`purgeStaleCaches` + `window.location.reload`); ab dem zweiten
   Reload sieht der User die neue Version.

3. Inkognito-Test: Eine fresh window auf dieselbe URL muss die neue
   UI zeigen, ohne erst zu reloaden — weil dort weder SW noch
   Local-Storage existieren.

## Was passiert technisch beim Bump?

`frontend/src/main.tsx` Zeile 89-105 ist der **Kill-Switch**:

```tsx
const KEY = "baulv_build_tag";
const stored = localStorage.getItem(KEY);
if (stored !== APP_BUILD_TAG) {
  localStorage.setItem(KEY, APP_BUILD_TAG);
  if (stored !== null) {
    void purgeStaleCaches().then(() => {
      window.location.reload();
    });
  }
}
```

→ Bei **jedem** Mismatch zwischen `localStorage.baulv_build_tag` und
dem hartkodierten `APP_BUILD_TAG` purged er **alle** Cache-Buckets +
deinstalliert **alle** Service Worker und reloaded **einmal**.

`frontend/public/sw.js` Zeile ~62-72 ist die **Cache-Eviction** im
SW-`activate`-Handler:

```js
caches.keys().then((keys) =>
  Promise.all(
    keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
  )
);
```

→ Beim Aktivieren des neuen SWs werden alle Caches mit dem alten Namen
gelöscht.

**Beide Mechanismen müssen gleichzeitig feuern.** Wenn nur einer
greift, bleibt der Browser in einem inkonsistenten Zwischenzustand.
Daher: beide Strings synchron, im selben Commit.

## Häufige Fehler

| Symptom | Ursache | Fix |
|---------|---------|-----|
| Deploy ist auf Railway grün, User sieht alte Version | Build-Tag nicht gebumpt | Beide Strings bumpen, neuer Commit, push |
| Nur `main.tsx` gebumpt, `sw.js` vergessen | Inkonsistente Tags | sw.js nachziehen, neuer Commit |
| Nur `sw.js` gebumpt, `main.tsx` vergessen | Inkonsistente Tags | main.tsx nachziehen, neuer Commit |
| Beide Tags identisch aber dennoch keine Änderung | Browser hängt im SW-Update-Cycle | DevTools → Application → Service Workers → "Unregister" → reload |
| Inkognito zeigt neue Version, normaler Browser nicht | Kill-Switch hat einmal sauber gefeuert, aber der User hat eine zweite Tab offen | Alle BauLV-Tabs schließen + neu öffnen |

## Geplant: Auto-Bump-Tooling (Stage später)

Manueller Bump ist fehleranfällig — ein Pre-Build-Skript, das den Tag
aus git-rev oder Datum generiert und in beide Dateien injiziert,
würde das Problem strukturell lösen. Bewusst aufgeschoben weil:

- Aktueller manueller Pfad ist mit dieser Doku jetzt sichtbar
  dokumentiert.
- Auto-Bump berührt Vite-Build-Pipeline + möglicherweise einen
  `sw.template.js` → `sw.js`-Generator, weil `public/sw.js` nicht
  durch Vite läuft. Eigener Refactor-Schritt, kein Hotfix-Material.

Wenn du das angehen willst, siehe Investigation-Notes im Hotfix-Commit
`v21.1`.

# B2B-AGB-Klausel-Entwurf (Fassung 1.0)

> ⚠ **WICHTIG:** Dieser Text ist ein **Entwurf** für die Einarbeitung in
> `frontend/src/pages/AGBPage.tsx` und MUSS vor Produktiv-Schaltung von
> einem Anwalt (WKO-Erstberatung oder Fachanwalt für IT-Recht) geprüft
> und freigezeichnet werden. Der Code referenziert ihn als
> `BUSINESS_TERMS_VERSION = "1.0"` mit Datum `2026-06-09`.

## Hintergrund

BauLV ist ein **B2B-SaaS** ausschließlich für Unternehmer (Bauträger,
Baufirmen, Architekten, Ziviltechniker, Bau-Sachverständige). Die
folgenden AGB-Klauseln stellen die rechtliche Grundlage für den
Ausschluss von Verbrauchern dar, sodass FAGG (Fern- und Auswärts-
geschäfte-Gesetz) und KSchG-Verbraucherschutz nicht greifen.

Die Klausel wird flankiert durch:

* Pflicht-Checkbox "Unternehmer-Bestätigung iSd § 1 UGB" bei
  Registrierung UND vor jedem Stripe-Checkout (siehe
  `RegisterPage.tsx`, `SubscriptionPage.tsx`).
* Pflichtfeld `company_name` bei Registrierung.
* Server-side Enforcement in `backend/app/api/stripe_api.py` —
  Checkout-Endpoint lehnt 400 ab, wenn der User-State die aktuelle
  Bestätigung nicht trägt.
* DSGVO-Art-7-konforme Snapshot-Beweiskette in der Tabelle
  `consent_snapshots` mit IP, UserAgent, Zeitstempel und Versions-
  String pro Bestätigungs-Akt.

## Klausel-Entwurf

Einzuarbeiten in `AGBPage.tsx`, **§ 1 Geltungsbereich** erweitern um
folgende Unterpunkte (heute hat § 1 nur einen Satz zu abweichenden
Bedingungen des Kunden):

---

### § 1 Geltungsbereich

**§ 1.1** Diese Allgemeinen Geschäftsbedingungen (im Folgenden „AGB")
gelten für sämtliche Verträge zwischen [FIRMENNAME] (im Folgenden
„Anbieter") und dem Kunden über die Nutzung der webbasierten Software
BauLV (im Folgenden „Software"). Abweichende oder ergänzende
Bedingungen des Kunden werden nur dann Vertragsbestandteil, wenn der
Anbieter ihnen ausdrücklich schriftlich zustimmt.

**§ 1.2 Ausschluss von Verbrauchern.** Das Angebot des Anbieters
richtet sich **ausschließlich an Unternehmer** im Sinne des § 1
Abs. 1 Unternehmensgesetzbuch (UGB) — also an natürliche oder
juristische Personen, die ein Unternehmen (eine auf Dauer angelegte
Organisation selbständiger wirtschaftlicher Tätigkeit) betreiben.
Insbesondere richtet sich das Angebot an Bauträger, Baufirmen,
Architekten, Ziviltechniker, Bau-Sachverständige und vergleichbare
gewerbliche oder freiberufliche Nutzer.

Mit **Verbrauchern** im Sinne des § 1 Abs. 1 Z 2 Konsumentenschutz-
gesetz (KSchG) kommen ausdrücklich **keine Vertragsverhältnisse**
zustande. Das Verbot der Nutzung durch Verbraucher wird durch die
im Registrierungs- und Bestellprozess abgefragte Unternehmer-
Bestätigung (§ 1 UGB) abgesichert.

Die Bestimmungen des Konsumentenschutzgesetzes (KSchG), des Fern-
und Auswärtsgeschäfte-Gesetzes (FAGG) sowie sonstige verbraucher-
schützende Regelungen finden auf dieses Vertragsverhältnis
**keine Anwendung**, soweit dies nach österreichischem Recht zulässig
ist.

**§ 1.3 Folgen unzutreffender Bestätigung.** Sollte sich nach
Vertragsschluss herausstellen, dass der Nutzer abweichend von seiner
Unternehmer-Bestätigung nicht als Unternehmer gehandelt hat, ist der
Anbieter berechtigt, den Vertrag mit sofortiger Wirkung außer-
ordentlich zu kündigen. Bereits gezahlte Entgelte für vor der
Kündigung erbrachte Leistungen werden nicht zurückerstattet.

---

### Neuer § am Ende der AGB — Gerichtsstand und anwendbares Recht

**§ N.1** Für alle Streitigkeiten aus oder im Zusammenhang mit diesem
Vertrag wird die ausschließliche Zuständigkeit des sachlich
zuständigen Gerichts am Sitz des Anbieters (**[GERICHTSSTAND]**)
vereinbart.

**§ N.2** Es gilt **österreichisches Recht** unter Ausschluss des
UN-Kaufrechts (CISG) und der Verweisungsnormen des internationalen
Privatrechts.

---

## Frontend-Verweis (bestehende Stellen)

Diese Klausel wird vom Code an drei Stellen verlinkt — der Anwalt
sollte den Wortlaut der **AGB-Linktexte** entsprechend kennen:

1. **RegisterPage.tsx** — Checkbox-Text: "Ich nutze BauLV als
   Unternehmer im Sinne des § 1 UGB … Siehe **AGB** (Fassung 1.0
   vom 09.06.2026)."
2. **SubscriptionPage.tsx** (pro Plan-Karte) — Checkbox-Text: "Ich
   schließe diesen Vertrag als Unternehmer (§ 1 UGB) und nicht als
   Verbraucher ab."
3. **ConsentRefreshModal.tsx** (für Bestandsuser nach Klausel-Bump
   bzw. grandfathered-User beim nächsten Login) — selber Wortlaut
   wie RegisterPage.

## Offene Punkte vor Go-Live

* `[FIRMENNAME]` ersetzen (Pflichtfeld im bestehenden AGB-Text).
* `[GERICHTSSTAND]` festlegen (Salzburg? Wels?) — hängt vom
  Firmensitz ab; das ist die Frage, die Tobi mit seinen Eltern
  klären muss.
* WKO-Erstberatung mit:
  - Diesen drei Klausel-Texten
  - Frage: UID-Pflicht bei Registrierung sinnvoll?
  - Frage: Bestätigungs-Email nach Kauf mit B2B-Wortlaut nötig?
  - Frage: Wo genau muss `[GERICHTSSTAND]` enger eingegrenzt werden?

## Version

* `BUSINESS_TERMS_VERSION = "1.0"`
* `BUSINESS_TERMS_DATE = "2026-06-09"`

Bei Anwalts-Änderungen am Wortlaut: Konstanten in
`backend/app/legal_versions.py` bumpen (z.B. auf `"1.1"`), Datum
aktualisieren. Bestandsuser bekommen dann automatisch den
`ConsentRefreshModal` beim nächsten Login angezeigt und müssen
erneut zustimmen.

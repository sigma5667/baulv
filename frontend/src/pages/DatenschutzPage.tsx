import { Link } from "react-router-dom";
import { Building2, ArrowLeft } from "lucide-react";
import { Footer } from "../components/layout/Footer";

/*
  TODO: Vor Go-Live durch Rechtsanwalt / Datenschutzbeauftragten prüfen lassen.
  Platzhalter: [FIRMENNAME], [ADRESSE], [EMAIL], [DSB_EMAIL].
*/

export function DatenschutzPage() {
  return (
    <div className="min-h-screen bg-white">
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-4xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-bold text-primary">
            <Building2 className="h-6 w-6" />
            <span className="text-lg">BauLV</span>
          </Link>
          <Link
            to="/"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Zur Startseite
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <h1 className="mb-2 text-3xl font-bold">Datenschutzerklärung</h1>
        <p className="mb-8 text-sm text-muted-foreground">
          Gemäß Datenschutz-Grundverordnung (DSGVO/GDPR) und
          Datenschutzgesetz (DSG Österreich)
        </p>

        <section className="space-y-6 text-sm leading-relaxed">
          <div>
            <h2 className="mb-2 text-lg font-semibold">
              1. Verantwortlicher im Sinne der DSGVO
            </h2>
            <p>
              [FIRMENNAME]
              <br />
              [ADRESSE]
              <br />
              E-Mail: [EMAIL]
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              2. Datenschutzbeauftragter
            </h2>
            <p>
              Sofern gesetzlich erforderlich, erreichen Sie unseren
              Datenschutzbeauftragten unter: [DSB_EMAIL]
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              3. Erhebung und Verarbeitung personenbezogener Daten
            </h2>
            <p className="mb-2">
              Wir verarbeiten personenbezogene Daten nur, soweit dies zur
              Bereitstellung der Software und ihrer Funktionen erforderlich
              ist. Im Einzelnen:
            </p>
            <ul className="ml-5 list-disc space-y-2">
              <li>
                <strong>Registrierung / Nutzerkonto:</strong> Name, E-Mail,
                Firmenname (optional), Passwort (gehasht). Rechtsgrundlage:
                Vertragserfüllung (Art. 6 Abs. 1 lit. b DSGVO).
              </li>
              <li>
                <strong>Login und Session:</strong> Authentifizierungs-Token,
                IP-Adresse, Browser-User-Agent, Zeitstempel. Rechtsgrundlage:
                berechtigtes Interesse an IT-Sicherheit (Art. 6 Abs. 1 lit. f
                DSGVO).
              </li>
              <li>
                <strong>Projekt- und Bauplandaten:</strong> Von Ihnen
                hochgeladene Pläne, Raumdaten, Leistungsverzeichnisse.
                Rechtsgrundlage: Vertragserfüllung.
              </li>
              <li>
                <strong>Abrechnung und Zahlungsabwicklung:</strong> Plan-Stufe,
                Rechnungsdaten, Zahlungsstatus. Rechtsgrundlage:
                Vertragserfüllung und gesetzliche Aufbewahrungspflichten
                (§ 132 BAO).
              </li>
              <li>
                <strong>Support-Kommunikation:</strong> E-Mails, die Sie an uns
                richten. Rechtsgrundlage: berechtigtes Interesse an der
                Beantwortung Ihrer Anfrage.
              </li>
            </ul>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              4. Cookies und lokale Speicher
            </h2>
            <p className="mb-2">
              Wir verwenden ausschließlich technisch notwendige Cookies bzw.
              Browser-Speicher (localStorage) zur Aufrechterhaltung Ihrer
              Sitzung und zur Speicherung Ihrer Einstellungen
              (z.&nbsp;B. Cookie-Zustimmung, Beta-Hinweis-Ausblendung).
            </p>
            <p className="mb-2">
              <strong>Rechtsgrundlage:</strong> Art. 6 Abs. 1 lit. f DSGVO
              (berechtigtes Interesse am ordnungsgemäßen Betrieb der Website)
              bzw. § 165 Abs. 3 TKG 2021 für unbedingt erforderliche
              Speichervorgänge.
            </p>
            <p>
              Marketing-, Tracking- oder Analyse-Cookies setzen wir derzeit
              <strong> nicht </strong>
              ein. Sollten wir in Zukunft solche Dienste einsetzen, werden wir
              vorher Ihre Einwilligung über den Cookie-Banner einholen.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              5. Weitergabe an Auftragsverarbeiter
            </h2>
            <p className="mb-2">
              Zur Bereitstellung unseres Dienstes arbeiten wir mit sorgfältig
              ausgewählten Auftragsverarbeitern im Sinne des Art. 28 DSGVO
              zusammen. Mit allen Sub-Auftragsverarbeitern bestehen
              Auftragsverarbeitungsverträge (AVV / DPA):
            </p>
            <ul className="ml-5 list-disc space-y-2">
              <li>
                <strong>Hosting — Railway Corp. (USA):</strong> Betrieb von
                Anwendungsservern und Datenbank. Datenübermittlung in die USA
                erfolgt auf Grundlage der EU-Standardvertragsklauseln (SCCs)
                und des EU-US Data Privacy Framework.
              </li>
              <li>
                <strong>Zahlungsabwicklung — Stripe Payments Europe Ltd.
                (Irland):</strong> Verarbeitung von Zahlungen und
                Abonnementverwaltung. Stripe ist selbst für die Verarbeitung
                von Zahlungsdaten gemäß PCI-DSS verantwortlich.{" "}
                <a
                  href="https://stripe.com/privacy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  Datenschutzerklärung von Stripe
                </a>
                .
              </li>
              <li>
                <strong>KI-Verarbeitung — Anthropic PBC (USA):</strong>{" "}
                Ausschließlich für die vom Nutzer ausgelöste Plan-Analyse,
                LV-Textgenerierung und Chat-Funktion. Übermittelt werden nur
                die zur Anfrage notwendigen Inhalte (z.&nbsp;B. Bauplan-Bild,
                Positionsbeschreibung). Anthropic verwendet laut eigener
                Zusicherung API-Daten nicht zum Modelltraining.
                Datenübermittlung in die USA auf Grundlage der SCCs und des
                EU-US Data Privacy Framework.{" "}
                <a
                  href="https://www.anthropic.com/legal/privacy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  Datenschutzerklärung von Anthropic
                </a>
                .
              </li>
            </ul>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">6. Speicherdauer</h2>
            <p>
              Wir speichern personenbezogene Daten nur so lange, wie es für den
              jeweiligen Zweck erforderlich ist. Kontodaten werden bis zur
              Löschung Ihres Kontos aufbewahrt. Rechnungsdaten werden gemäß
              § 132 BAO für sieben Jahre aufbewahrt. Log-Daten werden in der
              Regel nach 30 Tagen gelöscht.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              7. Ihre Rechte als betroffene Person
            </h2>
            <p className="mb-2">
              Nach Art. 15–22 DSGVO stehen Ihnen folgende Rechte zu:
            </p>
            <ul className="ml-5 list-disc space-y-1">
              <li>Recht auf Auskunft (Art. 15 DSGVO)</li>
              <li>Recht auf Berichtigung (Art. 16 DSGVO)</li>
              <li>Recht auf Löschung („Recht auf Vergessenwerden“, Art. 17 DSGVO)</li>
              <li>Recht auf Einschränkung der Verarbeitung (Art. 18 DSGVO)</li>
              <li>Recht auf Datenübertragbarkeit (Art. 20 DSGVO)</li>
              <li>Recht auf Widerspruch gegen die Verarbeitung (Art. 21 DSGVO)</li>
              <li>
                Recht auf Widerruf einer erteilten Einwilligung mit Wirkung für
                die Zukunft (Art. 7 Abs. 3 DSGVO)
              </li>
            </ul>
            <p className="mt-2">
              Zur Ausübung Ihrer Rechte genügt eine formlose Nachricht an{" "}
              [EMAIL].
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">8. Beschwerderecht</h2>
            <p>
              Unbeschadet eines anderweitigen verwaltungsrechtlichen oder
              gerichtlichen Rechtsbehelfs steht Ihnen das Recht auf Beschwerde
              bei einer Aufsichtsbehörde zu. Zuständige Aufsichtsbehörde in
              Österreich:
            </p>
            <p className="mt-2">
              <strong>Österreichische Datenschutzbehörde (DSB)</strong>
              <br />
              Barichgasse 40–42, 1030 Wien
              <br />
              Telefon: +43 1 52 152-0
              <br />
              E-Mail: dsb@dsb.gv.at
              <br />
              Web:{" "}
              <a
                href="https://www.dsb.gv.at"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                www.dsb.gv.at
              </a>
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              9. Sicherheit der Datenverarbeitung
            </h2>
            <p>
              Wir setzen technische und organisatorische Maßnahmen ein, um
              Ihre Daten gegen unbefugte Zugriffe, Verlust und Manipulation zu
              schützen. Die Übertragung zwischen Ihrem Browser und unseren
              Servern erfolgt stets verschlüsselt (TLS). Passwörter werden
              ausschließlich als Hash gespeichert.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              10. Änderungen dieser Datenschutzerklärung
            </h2>
            <p>
              Wir behalten uns vor, diese Datenschutzerklärung anzupassen, um
              sie stets geltenden rechtlichen Anforderungen entsprechend zu
              halten oder Änderungen unserer Leistungen abzubilden. Für Ihren
              erneuten Besuch gilt dann die neue Datenschutzerklärung.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Stand</h2>
            <p>Diese Datenschutzerklärung ist gültig ab April 2026.</p>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

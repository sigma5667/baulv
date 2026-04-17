import { Link } from "react-router-dom";
import { Building2, ArrowLeft } from "lucide-react";
import { Footer } from "../components/layout/Footer";

/*
  TODO: Vor Go-Live durch Rechtsanwalt prüfen lassen.
  Platzhalter: [FIRMENNAME], [GERICHTSSTAND].
*/

export function AGBPage() {
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
        <h1 className="mb-2 text-3xl font-bold">
          Allgemeine Geschäftsbedingungen (AGB)
        </h1>
        <p className="mb-8 text-sm text-muted-foreground">
          von [FIRMENNAME] für die Nutzung der Software „BauLV"
        </p>

        <section className="space-y-6 text-sm leading-relaxed">
          <div>
            <h2 className="mb-2 text-lg font-semibold">1. Geltungsbereich</h2>
            <p>
              Diese Allgemeinen Geschäftsbedingungen (im Folgenden „AGB")
              gelten für sämtliche Verträge zwischen [FIRMENNAME]
              (im Folgenden „Anbieter") und dem Kunden über die Nutzung der
              webbasierten Software BauLV (im Folgenden „Software"). Abweichende
              oder ergänzende Bedingungen des Kunden werden nur dann
              Vertragsbestandteil, wenn der Anbieter ihnen ausdrücklich
              schriftlich zustimmt.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              2. Vertragsgegenstand
            </h2>
            <p>
              Der Anbieter stellt dem Kunden eine Software-as-a-Service-Lösung
              (SaaS) zur Erstellung von Leistungsverzeichnissen und zur
              automatisierten Mengenermittlung nach österreichischen
              Baustandards zur Verfügung. Der Funktionsumfang richtet sich nach
              dem vom Kunden gewählten Abonnement.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              3. Berechnungsregeln und Urheberrecht
            </h2>
            <p>
              Die in der Software hinterlegten Berechnungsregeln bestehen aus
              mathematischen Formeln und Algorithmen, die sich an den in
              Österreich üblichen Baustandards und Abrechnungsgewohnheiten
              orientieren. Die Software speichert oder verbreitet keine
              urheberrechtlich geschützten Normtexte Dritter. Die Ergebnisse
              der Berechnungen sind ohne Gewähr; siehe Abschnitt 10.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              4. Vertragsschluss und Kontoerstellung
            </h2>
            <p>
              Der Vertrag kommt mit der erfolgreichen Registrierung des Kunden
              und dem Klick auf die Schaltfläche „Konto erstellen" zustande.
              Voraussetzung ist die Annahme dieser AGB sowie die Kenntnisnahme
              der Datenschutzerklärung. Der Kunde sichert zu, dass die bei der
              Registrierung angegebenen Daten wahrheitsgemäß und vollständig
              sind.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              5. Abonnementpläne und Zahlungsbedingungen
            </h2>
            <p className="mb-2">
              Die Software wird in den Plänen <strong>Basis</strong>,{" "}
              <strong>Pro</strong> und <strong>Enterprise</strong> angeboten.
              Die Leistungsinhalte und Preise der Pläne sind auf der Website
              abrufbar und zum Zeitpunkt der Bestellung verbindlich.
            </p>
            <p className="mb-2">
              Die Abrechnung erfolgt im Voraus monatlich über den
              Zahlungsdienstleister Stripe. Mit der Bestellung eines
              kostenpflichtigen Abonnements ermächtigt der Kunde den Anbieter
              bzw. Stripe, den fälligen Betrag zum jeweiligen Fälligkeitstag
              einzuziehen.
            </p>
            <p>
              Alle angegebenen Preise verstehen sich in Euro zuzüglich der
              gesetzlichen Umsatzsteuer, sofern nicht anders angegeben.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              6. Vertragslaufzeit und Kündigung
            </h2>
            <p>
              Abonnements laufen auf monatlicher Basis und verlängern sich
              automatisch um einen weiteren Monat, sofern nicht spätestens zum
              Ende des laufenden Abrechnungszeitraums gekündigt wird. Die
              Kündigung erfolgt jederzeit über das Kundenportal oder formlos
              per E-Mail. Der Zugang zu kostenpflichtigen Funktionen endet mit
              Ablauf des bezahlten Zeitraums; bereits entrichtete Entgelte
              werden nicht anteilig erstattet, soweit gesetzlich zulässig.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              7. Widerrufsrecht für Verbraucher
            </h2>
            <p className="mb-2">
              Ist der Kunde Verbraucher im Sinne des Konsumentenschutzgesetzes
              (KSchG) bzw. Fern- und Auswärtsgeschäfte-Gesetz (FAGG), so steht
              ihm ein <strong>Widerrufsrecht von 14 Tagen</strong> ab
              Vertragsschluss zu, ohne Angabe von Gründen.
            </p>
            <p className="mb-2">
              Der Widerruf ist durch eine eindeutige Erklärung (z.&nbsp;B.
              E-Mail) gegenüber dem Anbieter zu erklären.
            </p>
            <p>
              <strong>Erlöschen des Widerrufsrechts:</strong> Das Widerrufsrecht
              erlischt bei Verträgen über die Erbringung von Dienstleistungen,
              wenn der Anbieter die Dienstleistung vollständig erbracht hat und
              mit der Ausführung der Dienstleistung erst begonnen hat, nachdem
              der Verbraucher dazu seine ausdrückliche Zustimmung gegeben und
              gleichzeitig seine Kenntnis davon bestätigt hat, dass er sein
              Widerrufsrecht bei vollständiger Vertragserfüllung durch den
              Anbieter verliert (§ 18 FAGG).
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              8. Nutzungsrechte
            </h2>
            <p>
              Der Anbieter räumt dem Kunden für die Dauer des Vertrags ein
              nicht ausschließliches, nicht übertragbares Recht zur Nutzung der
              Software im vereinbarten Umfang ein. Eine Weitergabe der
              Zugangsdaten an Dritte ist nicht gestattet. Die vom Kunden
              erstellten Projekte und Leistungsverzeichnisse bleiben sein
              geistiges Eigentum.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              9. Verfügbarkeit und Beta-Hinweis
            </h2>
            <p>
              Die Software befindet sich derzeit in der <strong>Beta-Phase</strong>
              . Sie ist ausdrücklich nicht für den produktiven Einsatz
              freigegeben. Der Anbieter bemüht sich um eine möglichst hohe
              Verfügbarkeit, übernimmt jedoch keine Garantie für eine
              bestimmte Verfügbarkeit oder unterbrechungsfreie Nutzbarkeit.
              Wartungsfenster und Ausfälle können jederzeit auftreten.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              10. Gewährleistung und Haftung
            </h2>
            <p className="mb-2">
              Die Software wird „wie besehen" („as is") zur Verfügung gestellt.
              Der Anbieter übernimmt keine Gewähr für die Richtigkeit der von
              der KI generierten Inhalte, insbesondere nicht für die
              Vollständigkeit, Normkonformität oder Genauigkeit der aus Plänen
              extrahierten Raumdaten und der vorgeschlagenen LV-Positionen.
              Der Kunde ist verpflichtet, alle Ergebnisse vor der Verwendung
              sorgfältig zu prüfen und gegebenenfalls zu korrigieren.
            </p>
            <p className="mb-2">
              Der Anbieter haftet nur für Schäden, die auf Vorsatz oder grober
              Fahrlässigkeit beruhen. Eine Haftung für leichte Fahrlässigkeit
              — mit Ausnahme von Personenschäden — ist ausgeschlossen. Die
              Haftung für mittelbare Schäden, entgangenen Gewinn, Folgeschäden
              und Datenverlust ist, soweit gesetzlich zulässig, ausgeschlossen.
            </p>
            <p>
              Zwingende gesetzliche Haftungsregelungen — insbesondere nach dem
              Produkthaftungsgesetz (PHG) und dem Konsumentenschutzgesetz
              (KSchG) — bleiben unberührt.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              11. Datenschutz
            </h2>
            <p>
              Einzelheiten zur Verarbeitung personenbezogener Daten finden
              sich in unserer{" "}
              <Link
                to="/datenschutz"
                className="text-primary hover:underline"
              >
                Datenschutzerklärung
              </Link>
              .
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              12. Änderungen der AGB
            </h2>
            <p>
              Der Anbieter ist berechtigt, diese AGB mit einer Ankündigungsfrist
              von mindestens vier Wochen zu ändern. Der Kunde kann den
              geänderten AGB widersprechen; widerspricht er nicht innerhalb
              der Frist, gelten die geänderten AGB als angenommen. Der Anbieter
              wird den Kunden in der Änderungsmitteilung auf diese Folge
              ausdrücklich hinweisen.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              13. Anwendbares Recht und Gerichtsstand
            </h2>
            <p className="mb-2">
              Es gilt österreichisches Recht unter Ausschluss der
              Verweisungsnormen des internationalen Privatrechts und des
              UN-Kaufrechts (CISG).
            </p>
            <p>
              Gerichtsstand für alle Streitigkeiten aus oder im Zusammenhang
              mit diesem Vertrag ist — soweit gesetzlich zulässig —{" "}
              [GERICHTSSTAND EINTRAGEN]. Für Verbraucher im Sinne des KSchG
              verbleibt es bei den gesetzlich zwingenden Gerichtsständen.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              14. Salvatorische Klausel
            </h2>
            <p>
              Sollten einzelne Bestimmungen dieser AGB ganz oder teilweise
              unwirksam sein oder werden, so wird dadurch die Wirksamkeit der
              übrigen Bestimmungen nicht berührt. Anstelle der unwirksamen
              Bestimmung gilt diejenige wirksame Regelung als vereinbart, die
              dem wirtschaftlichen Zweck der unwirksamen Bestimmung am
              nächsten kommt.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Stand</h2>
            <p>Diese AGB sind gültig ab April 2026.</p>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

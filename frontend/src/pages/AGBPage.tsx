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
            <p className="mt-3 rounded-md border-l-2 border-amber-500 bg-amber-50/40 p-3">
              <strong>1.2 Ausschließlich Unternehmer (B2B).</strong> Verträge
              über die Nutzung der Software kommen ausschließlich mit
              Unternehmern im Sinne des § 1 UGB zustande — also mit Personen,
              die das Geschäft im Rahmen ihrer gewerblichen, geschäftlichen
              oder beruflichen Tätigkeit abschließen. Verbraucher im Sinne des
              § 1 KSchG sind von der Nutzung ausgeschlossen; mit ihnen kommt
              kein Vertrag zustande. Die Unternehmereigenschaft wird im
              Registrierungs- und im Bestellvorgang technisch abgefragt und ist
              ausdrücklich zu bestätigen; ohne diese Bestätigung sind weder eine
              Registrierung noch ein kostenpflichtiger Vertragsschluss möglich.
              Auf dieses Vertragsverhältnis finden die besonderen
              Verbraucherschutzbestimmungen (insbesondere KSchG und FAGG) daher
              keine Anwendung.
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
              Alle angegebenen Preise sind Nettopreise in Euro und verstehen
              sich zuzüglich der gesetzlichen Umsatzsteuer. Da der Vertrag
              ausschließlich mit Unternehmern zustande kommt (Abschnitt 1.2),
              erfolgt die Preisauszeichnung netto.
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
              7. Kein Verbraucher-Widerrufsrecht
            </h2>
            <p>
              Da Verträge ausschließlich mit Unternehmern (§ 1 UGB) zustande
              kommen (siehe Abschnitt 1.2), besteht kein Rücktritts- bzw.
              Widerrufsrecht nach dem Fern- und Auswärtsgeschäfte-Gesetz
              (FAGG) — dieses gilt nur für Verbraucher. Die
              Kündigungsregelungen nach Abschnitt 6 bleiben hiervon unberührt.
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
              <strong>(1)</strong> Der Anbieter übernimmt keine Gewähr für die
              Richtigkeit, Vollständigkeit, Normkonformität oder Genauigkeit der
              von der KI erzeugten Inhalte — insbesondere nicht für die aus
              Plänen extrahierten Raumdaten und die vorgeschlagenen
              LV-Positionen. Der Kunde ist verpflichtet, alle Ergebnisse vor
              ihrer Verwendung eigenverantwortlich zu prüfen und
              erforderlichenfalls zu korrigieren; sie ersetzen keine fachliche
              Prüfung durch den Kunden.
            </p>
            <p className="mb-2">
              <strong>(2)</strong> Der Anbieter haftet unbeschränkt für Schäden
              aus der Verletzung des Lebens, des Körpers oder der Gesundheit
              sowie für Schäden, die auf Vorsatz oder grober Fahrlässigkeit
              beruhen.
            </p>
            <p className="mb-2">
              <strong>(3)</strong> Für leichte Fahrlässigkeit haftet der Anbieter
              nur bei der Verletzung einer wesentlichen Vertragspflicht (einer
              Pflicht, deren Erfüllung die ordnungsgemäße Durchführung des
              Vertrags überhaupt erst ermöglicht und auf deren Einhaltung der
              Kunde regelmäßig vertrauen darf), und der Höhe nach begrenzt auf
              den bei Vertragsschluss typischerweise vorhersehbaren Schaden. Im
              Übrigen ist die Haftung für leichte Fahrlässigkeit ausgeschlossen.
            </p>
            <p className="mb-2">
              <strong>(4)</strong> Die Haftung für mittelbare Schäden,
              entgangenen Gewinn, Folgeschäden und Datenverlust ist im
              gesetzlich zulässigen Umfang ausgeschlossen; dies gilt nicht in
              den Fällen der Absätze (2) und (3).
            </p>
            <p>
              <strong>(5)</strong> Zwingende gesetzliche Haftungsbestimmungen,
              insbesondere nach dem Produkthaftungsgesetz (PHG), bleiben
              unberührt.
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
              Als Gerichtsstand für alle Streitigkeiten aus oder im
              Zusammenhang mit diesem Vertrag wird das sachlich zuständige
              Gericht am Sitz des Anbieters ([GERICHTSSTAND EINTRAGEN])
              vereinbart. Diese Gerichtsstandsvereinbarung ist zulässig, da der
              Vertrag ausschließlich mit Unternehmern geschlossen wird
              (§ 104 JN).
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
            <p>
              Fassung Version 1.1 (Stand: Juni 2026). Wesentliche Änderung
              gegenüber Version 1.0: durchgehende Ausrichtung als B2B-Angebot
              ausschließlich für Unternehmer (§ 1 UGB), Wegfall des
              Verbraucher-Widerrufsrechts, differenzierte Haftungsregelung.
            </p>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

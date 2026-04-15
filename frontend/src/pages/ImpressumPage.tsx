import { Link } from "react-router-dom";
import { Building2, ArrowLeft } from "lucide-react";
import { Footer } from "../components/layout/Footer";

/*
  TODO: Alle Platzhalter mit echten Firmendaten ersetzen vor dem Go-Live.
  Erforderliche Angaben nach § 5 ECG und § 25 MedienG (Österreich):
    - Firmenwortlaut, Rechtsform, Sitz
    - Firmenbuchnummer und Firmenbuchgericht
    - UID-Nummer (sofern vorhanden)
    - Unternehmensgegenstand
    - Zuständige Aufsichtsbehörde bzw. anwendbare berufsrechtliche Vorschriften
    - Kontakt (E-Mail, Telefon)
    - Geschäftsführer / vertretungsbefugte Personen
    - Offenlegung der grundlegenden Richtung (Blattlinie) für Online-Dienste
  Vor Veröffentlichung zwingend durch Rechtsanwalt / WKO Österreich prüfen lassen.
*/

export function ImpressumPage() {
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
        <h1 className="mb-2 text-3xl font-bold">Impressum</h1>
        <p className="mb-8 text-sm text-muted-foreground">
          Offenlegung gemäß § 5 E-Commerce-Gesetz (ECG) und § 25
          Mediengesetz (MedienG)
        </p>

        <section className="space-y-6 text-sm leading-relaxed">
          <div>
            <h2 className="mb-2 text-lg font-semibold">Medieninhaber und Betreiber</h2>
            <p>
              [FIRMENNAME EINTRAGEN]
              <br />
              [ADRESSE EINTRAGEN]
              <br />
              Österreich
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Kontakt</h2>
            <p>
              E-Mail: [EMAIL EINTRAGEN]
              <br />
              Telefon: [TELEFONNUMMER EINTRAGEN]
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Vertretungsbefugte Personen</h2>
            <p>Geschäftsführer: [GESCHÄFTSFÜHRER EINTRAGEN]</p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Unternehmensdaten</h2>
            <p>
              Firmenbuchnummer: [FIRMENBUCHNUMMER EINTRAGEN]
              <br />
              Firmenbuchgericht: [FIRMENBUCHGERICHT EINTRAGEN]
              <br />
              UID-Nummer: [UID-NUMMER EINTRAGEN]
              <br />
              Unternehmensgegenstand: Entwicklung und Betrieb einer webbasierten
              Software zur Erstellung von Leistungsverzeichnissen
              (Software-as-a-Service)
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              Anwendbare Rechtsvorschriften und Aufsichtsbehörde
            </h2>
            <p>
              Gewerbeordnung (GewO), abrufbar unter{" "}
              <a
                href="https://www.ris.bka.gv.at"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                www.ris.bka.gv.at
              </a>
              .
              <br />
              Aufsichtsbehörde/Gewerbebehörde:
              [ZUSTÄNDIGE BEZIRKSHAUPTMANNSCHAFT / MAGISTRAT EINTRAGEN]
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              Berufsbezeichnung und berufsrechtliche Vorschriften
            </h2>
            <p>
              Berufsbezeichnung: [BERUFSBEZEICHNUNG EINTRAGEN, z. B.
              IT-Dienstleistung — Dienstleistung in der automatischen
              Datenverarbeitung und Informationstechnik]
              <br />
              Verliehen in: Österreich
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              Offenlegung gemäß § 25 MedienG
            </h2>
            <p>
              Grundlegende Richtung: Information über die Software-Plattform
              BauLV zur KI-gestützten Erstellung von Leistungsverzeichnissen
              für den österreichischen Baumarkt.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">
              Online-Streitbeilegung
            </h2>
            <p>
              Die Europäische Kommission stellt eine Plattform zur
              Online-Streitbeilegung (OS) bereit:{" "}
              <a
                href="https://ec.europa.eu/consumers/odr"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                https://ec.europa.eu/consumers/odr
              </a>
              . Wir sind nicht bereit oder verpflichtet, an
              Streitbeilegungsverfahren vor einer
              Verbraucherschlichtungsstelle teilzunehmen.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Haftungsausschluss</h2>
            <p>
              Die Inhalte dieser Website wurden mit größtmöglicher Sorgfalt
              erstellt. Für die Richtigkeit, Vollständigkeit und Aktualität der
              Inhalte können wir jedoch keine Gewähr übernehmen. BauLV befindet
              sich in der Beta-Phase und ist nicht für den produktiven Einsatz
              freigegeben.
            </p>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Urheberrecht</h2>
            <p>
              Die durch die Betreiber erstellten Inhalte und Werke auf dieser
              Website unterliegen dem österreichischen Urheberrecht.
              Vervielfältigung, Bearbeitung, Verbreitung und jede Art der
              Verwertung außerhalb der Grenzen des Urheberrechts bedürfen der
              schriftlichen Zustimmung des jeweiligen Autors bzw. Erstellers.
            </p>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

import { Link } from "react-router-dom";
import {
  Building2,
  FileText,
  Calculator,
  Upload,
  Shield,
  Check,
  ArrowRight,
  BookOpen,
  BarChart3,
  MessageSquare,
} from "lucide-react";

const FEATURES = [
  {
    icon: Upload,
    title: "Plananalyse mit KI",
    desc: "Laden Sie Ihre Baupläne als PDF hoch — unsere KI erkennt automatisch Räume, Flächen und Maße.",
  },
  {
    icon: BookOpen,
    title: "ÖNORM-Bibliothek",
    desc: "Verwalten Sie Ihre ÖNORM-Standards zentral. Die KI nutzt die relevanten Normen als Wissensbasis.",
  },
  {
    icon: Calculator,
    title: "Automatische Mengenermittlung",
    desc: "Deterministische Berechnung nach ÖNORM-Regeln mit nachvollziehbaren Berechnungsnachweisen.",
  },
  {
    icon: FileText,
    title: "LV-Erstellung",
    desc: "Erstellen Sie professionelle Leistungsverzeichnisse — manuell oder KI-gestützt mit ÖNORM-konformen Texten.",
  },
  {
    icon: MessageSquare,
    title: "KI-Chatassistent",
    desc: "Fragen Sie den Bauexperten-Chat zu ÖNORM-Regeln, Abrechnungsvorschriften oder Ihrem Projekt.",
  },
  {
    icon: BarChart3,
    title: "Export & Vergleich",
    desc: "Exportieren Sie LVs als PDF oder Excel. Vergleichen Sie Angebote im Preisspiegel (Enterprise).",
  },
];

const PLANS = [
  {
    name: "Basis",
    price: "49",
    interval: "/Monat",
    features: ["3 aktive Projekte", "ÖNORM-Bibliothek", "Manueller LV-Editor", "PDF-Export"],
    cta: "14 Tage kostenlos testen",
    href: "/register",
    popular: false,
  },
  {
    name: "Pro",
    price: "149",
    interval: "/Monat",
    features: [
      "Unbegrenzte Projekte",
      "KI-Plananalyse",
      "KI-generierte Positionen",
      "KI-Chatassistent",
      "Excel + PDF Export",
      "Prioritäts-Support",
    ],
    cta: "14 Tage kostenlos testen",
    href: "/register",
    popular: true,
  },
  {
    name: "Enterprise",
    price: "Auf Anfrage",
    interval: "",
    features: [
      "Alles aus Pro",
      "Angebotsvergleich",
      "Team / Multi-User",
      "API-Zugang",
      "Individuelle Konfiguration",
      "Dedizierter Support",
    ],
    cta: "Kontakt aufnehmen",
    href: "mailto:kontakt@baulv.at?subject=Enterprise-Plan%20Anfrage",
    popular: false,
  },
];

export function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      {/* Navbar */}
      <nav className="sticky top-0 z-50 border-b bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 font-bold text-primary">
            <Building2 className="h-7 w-7" />
            <span className="text-xl">BauLV</span>
          </Link>
          <div className="hidden items-center gap-6 md:flex">
            <a href="#features" className="text-sm text-muted-foreground hover:text-foreground">
              Funktionen
            </a>
            <a href="#pricing" className="text-sm text-muted-foreground hover:text-foreground">
              Preise
            </a>
            <Link
              to="/login"
              className="text-sm font-medium text-primary hover:text-primary/80"
            >
              Anmelden
            </Link>
            <Link
              to="/register"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Kostenlos starten
            </Link>
          </div>
          {/* Mobile menu items */}
          <div className="flex items-center gap-3 md:hidden">
            <Link to="/login" className="text-sm font-medium text-primary">
              Login
            </Link>
            <Link
              to="/register"
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
            >
              Starten
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden bg-gradient-to-br from-blue-50 via-white to-orange-50">
        <div className="mx-auto max-w-6xl px-6 py-20 md:py-32">
          <div className="mx-auto max-w-3xl text-center">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border bg-white px-4 py-1.5 text-sm text-muted-foreground shadow-sm">
              <Shield className="h-4 w-4 text-primary" />
              ÖNORM-konform nach österreichischem Standard
            </div>
            <h1 className="text-4xl font-extrabold tracking-tight text-foreground md:text-6xl">
              KI-gestützte{" "}
              <span className="bg-gradient-to-r from-blue-600 to-blue-400 bg-clip-text text-transparent">
                Ausschreibungssoftware
              </span>{" "}
              für den Bau
            </h1>
            <p className="mt-6 text-lg text-muted-foreground md:text-xl">
              Von der Plananalyse zum fertigen Leistungsverzeichnis — automatisch, ÖNORM-konform
              und nachvollziehbar. Sparen Sie bis zu 80% der Zeit bei der Erstellung Ihrer
              Ausschreibungen.
            </p>
            <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
              <Link
                to="/register"
                className="flex items-center gap-2 rounded-lg bg-primary px-6 py-3 text-base font-medium text-primary-foreground shadow-lg hover:bg-primary/90"
              >
                Jetzt kostenlos starten
                <ArrowRight className="h-5 w-5" />
              </Link>
              <a
                href="#features"
                className="flex items-center gap-2 rounded-lg border px-6 py-3 text-base font-medium hover:bg-accent"
              >
                Funktionen entdecken
              </a>
            </div>
          </div>
        </div>
        {/* Decorative gradient blob */}
        <div className="absolute -bottom-40 left-1/2 h-80 w-80 -translate-x-1/2 rounded-full bg-blue-200/30 blur-3xl" />
      </section>

      {/* Trust bar */}
      <section className="border-y bg-muted/30 py-6">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-center gap-8 px-6 text-sm text-muted-foreground">
          <span className="flex items-center gap-2">
            <Check className="h-4 w-4 text-green-600" /> ÖNORM B 2061/B 2063 konform
          </span>
          <span className="flex items-center gap-2">
            <Check className="h-4 w-4 text-green-600" /> Österreichische Baustandards
          </span>
          <span className="flex items-center gap-2">
            <Check className="h-4 w-4 text-green-600" /> DSGVO-konform
          </span>
          <span className="flex items-center gap-2">
            <Check className="h-4 w-4 text-green-600" /> Made in Austria
          </span>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold">Alles, was Sie für Ihre Ausschreibung brauchen</h2>
            <p className="mt-3 text-muted-foreground">
              Von der PDF-Plananalyse bis zum fertigen LV — alles in einer Plattform.
            </p>
          </div>
          <div className="grid gap-8 md:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="rounded-xl border bg-card p-6 transition-shadow hover:shadow-md"
              >
                <div className="mb-4 inline-flex rounded-lg bg-primary/10 p-3">
                  <f.icon className="h-6 w-6 text-primary" />
                </div>
                <h3 className="mb-2 text-lg font-semibold">{f.title}</h3>
                <p className="text-sm text-muted-foreground">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="border-y bg-muted/20 py-20">
        <div className="mx-auto max-w-4xl px-6">
          <h2 className="mb-12 text-center text-3xl font-bold">So funktioniert's</h2>
          <div className="space-y-8">
            {[
              {
                step: "1",
                title: "Pläne hochladen",
                desc: "Laden Sie Ihre Baupläne als PDF hoch. Die KI analysiert Grundrisse und erkennt Räume, Flächen und Öffnungen.",
              },
              {
                step: "2",
                title: "ÖNORMs auswählen",
                desc: "Wählen Sie die relevanten ÖNORM-Standards aus Ihrer Bibliothek. Die KI nutzt diese als Wissensbasis für korrekte Abrechnungsregeln.",
              },
              {
                step: "3",
                title: "LV generieren",
                desc: "Erstellen Sie Leistungsverzeichnisse mit automatischer Mengenermittlung und ÖNORM-konformen Positionstexten.",
              },
              {
                step: "4",
                title: "Exportieren & Ausschreiben",
                desc: "Exportieren Sie das fertige LV als PDF oder Excel und versenden Sie Ihre Ausschreibung.",
              },
            ].map((s) => (
              <div key={s.step} className="flex gap-6">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-bold text-primary-foreground">
                  {s.step}
                </div>
                <div>
                  <h3 className="font-semibold">{s.title}</h3>
                  <p className="mt-1 text-sm text-muted-foreground">{s.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold">Transparente Preise</h2>
            <p className="mt-3 text-muted-foreground">
              Wählen Sie den Plan, der zu Ihrem Unternehmen passt. Jederzeit kündbar.
            </p>
          </div>

          <div className="grid gap-8 md:grid-cols-3">
            {PLANS.map((plan) => (
              <div
                key={plan.name}
                className={`relative rounded-xl border p-8 ${
                  plan.popular ? "border-primary shadow-xl ring-2 ring-primary/20" : ""
                }`}
              >
                {plan.popular && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-4 py-1 text-xs font-medium text-primary-foreground">
                    Am beliebtesten
                  </span>
                )}
                <h3 className="text-xl font-bold">{plan.name}</h3>
                <div className="mt-3 mb-6">
                  {plan.price === "Auf Anfrage" ? (
                    <span className="text-2xl font-bold">Auf Anfrage</span>
                  ) : (
                    <>
                      <span className="text-4xl font-bold">&euro;{plan.price}</span>
                      <span className="text-muted-foreground">{plan.interval}</span>
                    </>
                  )}
                </div>
                <ul className="mb-8 space-y-3">
                  {plan.features.map((f) => (
                    <li key={f} className="flex items-start gap-2 text-sm">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                      {f}
                    </li>
                  ))}
                </ul>
                {plan.href.startsWith("mailto:") ? (
                  <a
                    href={plan.href}
                    className="flex w-full items-center justify-center gap-2 rounded-md border px-4 py-3 text-sm font-medium hover:bg-accent"
                  >
                    {plan.cta}
                    <ArrowRight className="h-4 w-4" />
                  </a>
                ) : (
                  <Link
                    to={plan.href}
                    className={`flex w-full items-center justify-center gap-2 rounded-md px-4 py-3 text-sm font-medium ${
                      plan.popular
                        ? "bg-primary text-primary-foreground hover:bg-primary/90"
                        : "border hover:bg-accent"
                    }`}
                  >
                    {plan.cta}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t bg-gradient-to-r from-blue-600 to-blue-700 py-16 text-white">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h2 className="text-3xl font-bold">Bereit, Ihre Ausschreibungen zu revolutionieren?</h2>
          <p className="mt-4 text-blue-100">
            Testen Sie BauLV 14 Tage kostenlos und upgraden Sie jederzeit.
          </p>
          <Link
            to="/register"
            className="mt-8 inline-flex items-center gap-2 rounded-lg bg-white px-8 py-3 text-base font-semibold text-blue-700 shadow-lg hover:bg-blue-50"
          >
            14 Tage kostenlos testen
            <ArrowRight className="h-5 w-5" />
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t bg-muted/30 py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 text-sm text-muted-foreground md:flex-row">
          <div className="flex items-center gap-2">
            <Building2 className="h-5 w-5 text-primary" />
            <span className="font-semibold text-foreground">BauLV</span>
          </div>
          <p>&copy; {new Date().getFullYear()} BauLV. Alle Rechte vorbehalten.</p>
          <div className="flex gap-4">
            <a href="#" className="hover:text-foreground">Impressum</a>
            <a href="#" className="hover:text-foreground">Datenschutz</a>
            <a href="#" className="hover:text-foreground">AGB</a>
          </div>
        </div>
      </footer>
    </div>
  );
}

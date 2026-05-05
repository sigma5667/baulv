/**
 * /api-pricing — public marketing page for the BauLV API tiers
 * (v23.7).
 *
 * BauLV has been a SaaS LV-Editor first; the v23.7 repositioning
 * makes the MCP API a first-class product in its own right —
 * targeted at AI consultants ("KI-Berater"), workflow-automation
 * users (n8n / Make / Zapier), and developers who want to
 * integrate construction-quantity calculation into their stack.
 *
 * Page anatomy
 * ------------
 *
 * 1. Sticky public navbar (matches LandingPage so the brand is
 *    consistent across the funnel).
 * 2. Hero — single sentence positioning + primary CTA pointing
 *    at the existing API-key UI.
 * 3. Tier cards (4 cards: Free / Developer / Pro / Enterprise) —
 *    "Jetzt buchen" buttons currently fire the ``ComingSoonModal``
 *    because Stripe checkout for these tiers isn't live yet
 *    (separate ticket). Free signup keeps its existing /register
 *    path; Enterprise has a mailto.
 * 4. Use-cases — three concrete personas the API targets.
 * 5. About-the-API — short technical fact-sheet (15 tools, MCP,
 *    rate-limits, auth, region) with a deep-link to /developers.
 * 6. FAQ — five common questions captured from sales conversations.
 * 7. Footer.
 *
 * Public route, no auth required. Mounted in App.tsx.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Building2,
  Code2,
  Workflow,
  Bot,
  Server,
  Shield,
  ArrowRight,
  Check,
  Sparkles,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { Footer } from "../components/layout/Footer";
import { ComingSoonModal } from "../components/ComingSoonModal";

interface ApiTier {
  /** Internal identifier (also used as the React key). */
  id: "free" | "developer" | "pro" | "enterprise";
  name: string;
  /** Human-readable price. ``null`` = "Auf Anfrage". */
  priceLabel: string;
  /** Trailing slash-suffix (e.g. "/Monat"). */
  interval: string;
  blurb: string;
  features: string[];
  ctaLabel: string;
  /** When set: button calls onClick (opens ComingSoonModal). When
   * unset: button is a regular Link/anchor. */
  ctaKind: "coming-soon" | "register" | "contact";
  highlighted?: boolean;
}

const TIERS: ApiTier[] = [
  {
    id: "free",
    name: "Free",
    priceLabel: "0 €",
    interval: "/Monat",
    blurb: "Für Experimente und Prototypen.",
    features: [
      "50 API-Calls/Monat",
      "Nur Read-Tools (8 von 15)",
      "Keine Plan-Analyse",
      "Community-Support",
    ],
    ctaLabel: "Kostenlos starten",
    ctaKind: "register",
  },
  {
    id: "developer",
    name: "Developer",
    priceLabel: "49 €",
    interval: "/Monat",
    blurb: "Für KI-Berater und Workflow-Builder.",
    features: [
      "1.000 API-Calls/Monat",
      "Inkl. 50 Plan-Analysen mit KI",
      "Alle 15 Tools (Read + Write)",
      "Email-Support",
    ],
    ctaLabel: "Jetzt buchen",
    ctaKind: "coming-soon",
    highlighted: true,
  },
  {
    id: "pro",
    name: "Pro",
    priceLabel: "199 €",
    interval: "/Monat",
    blurb: "Für produktive Integrationen.",
    features: [
      "5.000 API-Calls/Monat",
      "Inkl. 200 Plan-Analysen mit KI",
      "Priority-Support",
      "Webhook-Integration",
      "API-Logs Export",
    ],
    ctaLabel: "Jetzt buchen",
    ctaKind: "coming-soon",
  },
  {
    id: "enterprise",
    name: "Enterprise",
    priceLabel: "ab 999 €",
    interval: "/Monat (custom)",
    blurb: "Für Plattform-Integrationen mit SLA.",
    features: [
      "Unbegrenzte API-Calls",
      "SLA-Garantie 99.9 %",
      "Dedicated Support",
      "Custom-Integrationen",
      "Whitelabel-Option",
    ],
    ctaLabel: "Kontakt aufnehmen",
    ctaKind: "contact",
  },
];

const USE_CASES = [
  {
    icon: Bot,
    title: "AI-Berater & Custom-Workflows",
    desc: "Bau-Beratungs-Agenten, die direkt LVs kalkulieren, Mengen begründen und Vorlagen ziehen — komplett über die API. Kein UI-Handover nötig.",
  },
  {
    icon: Workflow,
    title: "Workflow-Automation",
    desc: "n8n, Zapier, Make: Plan kommt rein → Mengen werden ermittelt → LV als Excel landet im Sharepoint des Kunden. Vollautomatisch über MCP.",
  },
  {
    icon: Sparkles,
    title: "Direkt aus Claude Desktop / ChatGPT",
    desc: "MCP-Server-Discovery via /.well-known/mcp.json. Verbinde Claude Desktop oder ChatGPT mit deinem BauLV-Account und arbeite per Konversation.",
  },
];

const FAQS = [
  {
    q: "Was sind Plan-Analysen?",
    a: "Eine Plan-Analyse ist ein Vision-API-Call auf eine PDF-Bauplan-Datei: die KI erkennt automatisch Räume, Wandflächen, Decken und Öffnungen. Pro Analyse zählt ein PDF mit beliebig vielen Seiten innerhalb der Plattform-Limits. Der Developer-Plan enthält 50 solche Analysen pro Monat, der Pro-Plan 200.",
  },
  {
    q: "Welche Bezahlmethoden unterstützt ihr?",
    a: "Kreditkarte über Stripe (Visa, Mastercard, AmEx). SEPA-Lastschrift für Enterprise-Kunden auf Anfrage. Rechnung mit 14 Tagen Zahlungsziel ab Pro-Plan.",
  },
  {
    q: "Kann ich jederzeit kündigen?",
    a: "Ja. Alle Tarife sind monatlich kündbar. Du behältst Zugriff bis zum Ende des bezahlten Monats und kannst bis dahin deine Daten exportieren (Art. 20 DSGVO).",
  },
  {
    q: "Was zählt als API-Call?",
    a: "Jeder erfolgreiche Tool-Aufruf über den MCP-Endpoint bzw. die REST-API zählt als ein Call. Discovery-Endpoints (/.well-known/mcp.json), das Token-Listing und Read-Calls auf das eigene Profil sind kostenfrei und zählen nicht. Plan-Analysen werden separat gezählt — sie sind teurer in der Verarbeitung und haben deshalb ein eigenes Kontingent pro Tarif.",
  },
  {
    q: "Was passiert bei Limit-Überschreitung?",
    a: "Der nächste Call bekommt eine 429-Antwort mit einer deutschen Fehlermeldung und einem Retry-After-Header. Der Service ist nicht 'kaputt' — er pausiert nur für dich, bis das Monats-Kontingent wieder freigeschaltet wird oder du in einen größeren Tarif wechselst. Pro- und Enterprise-Kunden können Pakete für überschüssige Calls dazubuchen.",
  },
];

export function ApiPricingPage() {
  // Single shared modal — the active CTA passes a custom subject so
  // the mailto fallback in the modal is pre-tagged with the tier
  // the user clicked. ``null`` = closed.
  const [comingSoonFor, setComingSoonFor] = useState<ApiTier | null>(null);
  // FAQ accordion state. Single-open at a time so a user can't
  // build a wall of expanded panels.
  const [openFaq, setOpenFaq] = useState<number | null>(null);

  return (
    <div className="min-h-screen bg-white">
      {/* Public navbar — matches LandingPage style. */}
      <nav className="sticky top-0 z-40 border-b bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link
            to="/"
            className="flex items-center gap-2 font-bold text-primary"
          >
            <Building2 className="h-7 w-7" />
            <span className="text-xl">BauLV</span>
          </Link>
          <div className="hidden items-center gap-6 md:flex">
            <Link
              to="/"
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              Plattform
            </Link>
            <Link
              to="/api-pricing"
              className="text-sm font-medium text-foreground"
            >
              API-Tarife
            </Link>
            <Link
              to="/developers"
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              Entwickler
            </Link>
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
        </div>
      </nav>

      {/* Hero */}
      <section className="relative overflow-hidden bg-gradient-to-br from-blue-50 via-white to-orange-50">
        <div className="mx-auto max-w-5xl px-6 py-20 md:py-28">
          <div className="text-center">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border bg-white px-4 py-1.5 text-sm text-muted-foreground shadow-sm">
              <Code2 className="h-4 w-4 text-primary" />
              MCP-Server · 15 Tools · EU-Hosting
            </div>
            <h1 className="text-4xl font-extrabold tracking-tight md:text-5xl">
              BauLV API für{" "}
              <span className="bg-gradient-to-r from-blue-600 to-blue-400 bg-clip-text text-transparent">
                KI-Agenten
              </span>{" "}
              und Entwickler
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground md:text-xl">
              Integriere Bau-Ausschreibungen in deine Workflows.
              MCP-Server, REST-API, 15 Tools — von der Plan-Analyse
              bis zum fertigen Leistungsverzeichnis.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                to="/app/api-keys"
                className="flex items-center gap-2 rounded-lg bg-primary px-6 py-3 text-base font-medium text-primary-foreground shadow-lg hover:bg-primary/90"
              >
                API-Key erstellen
                <ArrowRight className="h-5 w-5" />
              </Link>
              <Link
                to="/developers"
                className="flex items-center gap-2 rounded-lg border bg-white px-6 py-3 text-base font-medium hover:bg-accent"
              >
                <Code2 className="h-4 w-4" />
                Entwickler-Doku öffnen
              </Link>
            </div>
          </div>
        </div>
        <div className="absolute -bottom-40 left-1/2 h-80 w-80 -translate-x-1/2 rounded-full bg-blue-200/30 blur-3xl" />
      </section>

      {/* Tier cards */}
      <section id="tarife" className="py-20">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold">API-Tarife</h2>
            <p className="mt-3 text-muted-foreground">
              Wähle den Plan, der zu deinem API-Verbrauch passt. Jederzeit
              monatlich kündbar.
            </p>
          </div>
          <div className="grid gap-6 lg:grid-cols-4">
            {TIERS.map((tier) => (
              <TierCard
                key={tier.id}
                tier={tier}
                onComingSoon={() => setComingSoonFor(tier)}
              />
            ))}
          </div>
        </div>
      </section>

      {/* Use-cases */}
      <section className="border-y bg-muted/20 py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-12 text-center">
            <h2 className="text-3xl font-bold">Was kannst du damit machen?</h2>
            <p className="mt-3 text-muted-foreground">
              Drei Personas, eine API.
            </p>
          </div>
          <div className="grid gap-6 md:grid-cols-3">
            {USE_CASES.map((uc) => (
              <div
                key={uc.title}
                className="rounded-xl border bg-card p-6 transition-shadow hover:shadow-md"
              >
                <div className="mb-4 inline-flex rounded-lg bg-primary/10 p-3">
                  <uc.icon className="h-6 w-6 text-primary" />
                </div>
                <h3 className="mb-2 text-lg font-semibold">{uc.title}</h3>
                <p className="text-sm text-muted-foreground">{uc.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* About the API */}
      <section className="py-20">
        <div className="mx-auto grid max-w-6xl gap-10 px-6 md:grid-cols-2">
          <div>
            <h2 className="text-3xl font-bold">Über die API</h2>
            <p className="mt-3 text-muted-foreground">
              Standardisiert, dokumentiert, EU-gehostet — ein
              Production-Stack ohne Überraschungen.
            </p>
            <Link
              to="/developers"
              className="mt-6 inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
            >
              <Code2 className="h-4 w-4" />
              Vollständige Entwickler-Doku
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="grid gap-3 text-sm">
            <ApiFact
              icon={Server}
              label="15 Tools"
              value="8 lesend · 7 schreibend"
            />
            <ApiFact
              icon={Code2}
              label="Standard"
              value="Model Context Protocol (MCP)"
            />
            <ApiFact
              icon={Sparkles}
              label="Discovery"
              value="/.well-known/mcp.json"
            />
            <ApiFact
              icon={Shield}
              label="Authentifizierung"
              value="Personal Access Tokens (PAT, Bearer)"
            />
            <ApiFact
              icon={Server}
              label="Rate-Limits"
              value="60/min, 1.000/Tag pro Key"
            />
            <ApiFact
              icon={Shield}
              label="Region"
              value="EU (Cloudflare + Railway)"
            />
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section className="border-t bg-muted/20 py-20">
        <div className="mx-auto max-w-3xl px-6">
          <h2 className="mb-10 text-center text-3xl font-bold">Häufige Fragen</h2>
          <div className="space-y-3">
            {FAQS.map((faq, i) => (
              <FaqItem
                key={faq.q}
                question={faq.q}
                answer={faq.a}
                isOpen={openFaq === i}
                onToggle={() => setOpenFaq(openFaq === i ? null : i)}
              />
            ))}
          </div>
          <p className="mt-10 text-center text-sm text-muted-foreground">
            Noch Fragen?{" "}
            <a
              href="mailto:kontakt@baulv.at?subject=BauLV%20API%20—%20Frage"
              className="font-medium text-primary hover:underline"
            >
              kontakt@baulv.at
            </a>
          </p>
        </div>
      </section>

      <Footer />

      {comingSoonFor && (
        <ComingSoonModal
          title={`${comingSoonFor.name}-Plan: API-Buchung kommt bald`}
          mailSubject={`BauLV API ${comingSoonFor.name} — Early-Access`}
          onClose={() => setComingSoonFor(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function TierCard({
  tier,
  onComingSoon,
}: {
  tier: ApiTier;
  onComingSoon: () => void;
}) {
  const cardClass = tier.highlighted
    ? "relative rounded-xl border-2 border-primary bg-card p-6 shadow-xl ring-2 ring-primary/20"
    : "relative rounded-xl border bg-card p-6";

  return (
    <div className={cardClass}>
      {tier.highlighted && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
          Most Popular
        </span>
      )}
      <h3 className="text-xl font-bold">{tier.name}</h3>
      <p className="mt-1 text-sm text-muted-foreground">{tier.blurb}</p>
      <div className="my-5 flex items-baseline gap-1">
        <span className="text-3xl font-bold">{tier.priceLabel}</span>
        <span className="text-sm text-muted-foreground">{tier.interval}</span>
      </div>
      <ul className="mb-6 space-y-2.5">
        {tier.features.map((f) => (
          <li key={f} className="flex items-start gap-2 text-sm">
            <Check className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
            <span>{f}</span>
          </li>
        ))}
      </ul>
      <TierCta tier={tier} onComingSoon={onComingSoon} />
    </div>
  );
}

function TierCta({
  tier,
  onComingSoon,
}: {
  tier: ApiTier;
  onComingSoon: () => void;
}) {
  const sharedClass = tier.highlighted
    ? "flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
    : "flex w-full items-center justify-center gap-2 rounded-md border px-4 py-2.5 text-sm font-medium hover:bg-accent";

  if (tier.ctaKind === "register") {
    return (
      <Link to="/register" className={sharedClass}>
        {tier.ctaLabel}
        <ArrowRight className="h-4 w-4" />
      </Link>
    );
  }
  if (tier.ctaKind === "contact") {
    return (
      <a
        href={`mailto:kontakt@baulv.at?subject=${encodeURIComponent(
          "BauLV API Enterprise — Anfrage",
        )}`}
        className={sharedClass}
      >
        {tier.ctaLabel}
        <ArrowRight className="h-4 w-4" />
      </a>
    );
  }
  // coming-soon — opens the modal
  return (
    <button type="button" onClick={onComingSoon} className={sharedClass}>
      {tier.ctaLabel}
      <ArrowRight className="h-4 w-4" />
    </button>
  );
}

function ApiFact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Server;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border bg-card px-4 py-3">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="flex-1">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="mt-0.5 font-medium">{value}</p>
      </div>
    </div>
  );
}

function FaqItem({
  question,
  answer,
  isOpen,
  onToggle,
}: {
  question: string;
  answer: string;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border bg-card">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left text-sm font-medium hover:bg-accent/50"
      >
        <span>{question}</span>
        {isOpen ? (
          <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>
      {isOpen && (
        <div className="border-t bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
          {answer}
        </div>
      )}
    </div>
  );
}

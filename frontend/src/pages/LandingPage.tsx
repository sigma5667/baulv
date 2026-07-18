import { Link } from "react-router-dom";
import { ArrowDown, ArrowRight } from "lucide-react";
import { BetaBanner } from "../components/BetaBanner";
import { Footer } from "../components/layout/Footer";
import { SupportChat } from "../components/SupportChat";

/**
 * Landing page, v25 redesign: "technisches Blatt".
 *
 * Design rules (deliberate, please keep):
 * - Brand blue (--primary, #2563eb) is used for lines, numbers and the
 *   CTA only — never as a surface fill. No gradients, no blobs.
 * - Logo orange (#f97316) appears exclusively as the room-detection
 *   marker inside the hero graphic.
 * - Sections are separated by 1px hairlines like fields on a plan
 *   sheet; headlines are left-aligned.
 * - IBM Plex Sans/Mono are self-hosted via @fontsource (see index.css).
 *   The built page makes zero third-party requests — keep it that way.
 * - Every figure in the hero follows from the plan dimensions shown
 *   next to it AND matches the real engine's Austrian measurement
 *   rules. Whoever checks the numbers finds they add up — that is
 *   the trust argument of the page.
 */

// Hero example flat. rect = SVG coordinates at 40 px/m inside the
// outer wall. Floor areas are exactly width × depth of the labelled
// dimensions. Wall areas are perimeter × 2,50 m; like the real
// calculation engine (malerarbeiten.py: openings on plaster are
// deducted only above 5,0 m² — Austrian Übermessung), windows
// (1,50×1,40 = 2,10) and doors (0,90×2,10 = 1,89) are NOT deducted;
// only the terrace door (2,40×2,10 = 5,04) is.
interface HeroRoom {
  name: string;
  dims: string;
  floor: string;
  wall: string;
  rect: { x: number; y: number; w: number; h: number };
  /** Baseline des Raumnamens; Default: Raummitte. */
  labelY?: number;
}

const ROOMS: HeroRoom[] = [
  {
    name: "Zimmer",
    dims: "4,20 × 3,60",
    floor: "15,12",
    wall: "39,00", // 15,60 × 2,50; Fenster + Tür ≤ 5 m² übermessen
    rect: { x: 74, y: 54, w: 168, h: 144 },
  },
  {
    name: "Wohnen / Küche",
    dims: "4,20 × 5,90",
    floor: "24,78",
    wall: "45,46", // 20,20 × 2,50 − Terrassentür 5,04
    rect: { x: 246, y: 54, w: 168, h: 236 },
  },
  {
    name: "Bad",
    dims: "2,40 × 2,20",
    floor: "5,28",
    wall: "23,00", // 9,20 × 2,50; Tür übermessen
    rect: { x: 74, y: 202, w: 96, h: 88 },
  },
  {
    name: "Vorraum",
    dims: "1,70 × 2,20",
    floor: "3,74",
    wall: "19,50", // 7,80 × 2,50; 4 Türen übermessen
    rect: { x: 174, y: 202, w: 68, h: 88 },
    // Beschriftung in die obere Raumhälfte — unten schwenkt der
    // Türbogen der Eingangstür durch.
    labelY: 222,
  },
];

const TOTALS = { floor: "48,92", wall: "126,96" };

const BLUE = "#2563eb";
const INK = "#0f172a";
const ORANGE = "#f97316";

/** 45°-Zwei-Punkt-Strich einer Maßkette. */
function Tick({ x, y }: { x: number; y: number }) {
  return <line x1={x - 3} y1={y + 3} x2={x + 3} y2={y - 3} stroke={BLUE} strokeWidth="1" />;
}

/**
 * The signature element: a line-drawn floor plan whose rooms get
 * marked (orange), flowing into the resulting Mengenermittlung table.
 * Static base plan renders immediately; markers and table rows fade in
 * staggered. `prefers-reduced-motion` shows the finished state at once
 * (see .lp-a in index.css).
 */
function HeroTransformation() {
  return (
    <div className="border border-slate-200 bg-white">
      {/* Plankopf */}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2 font-plexmono text-[11px] uppercase tracking-wider text-slate-500">
        <span>Grundriss · Top 3 · M 1:100</span>
        <span>PDF-Upload</span>
      </div>

      <div className="p-4 sm:p-6">
        {/* Plan */}
        <svg
          viewBox="0 0 468 332"
          role="img"
          aria-label="Beispiel-Grundriss mit vier Räumen und Maßketten; die Räume sind als erkannt markiert"
          className="w-full"
        >
          {/* Maßketten */}
          <g>
            {/* oben: 4,20 | 0,10 (Wand, unbeschriftet) | 4,20 — Ticks
                auf den lichten Kanten, damit jedes beschriftete
                Segment exakt sein Maß misst (168 px = 4,20 m) */}
            <line x1="74" y1="28" x2="414" y2="28" stroke={BLUE} strokeWidth="1" />
            <line x1="74" y1="44" x2="74" y2="24" stroke={BLUE} strokeWidth="0.5" />
            <line x1="242" y1="44" x2="242" y2="24" stroke={BLUE} strokeWidth="0.5" />
            <line x1="246" y1="44" x2="246" y2="24" stroke={BLUE} strokeWidth="0.5" />
            <line x1="414" y1="44" x2="414" y2="24" stroke={BLUE} strokeWidth="0.5" />
            <Tick x={74} y={28} />
            <Tick x={242} y={28} />
            <Tick x={246} y={28} />
            <Tick x={414} y={28} />
            <text x="159" y="22" textAnchor="middle" fontSize="10" fill={BLUE} className="font-plexmono">
              4,20
            </text>
            <text x="329" y="22" textAnchor="middle" fontSize="10" fill={BLUE} className="font-plexmono">
              4,20
            </text>
            {/* links: 3,60 | 0,10 | 2,20 — Ticks auf lichten Kanten */}
            <line x1="46" y1="54" x2="46" y2="290" stroke={BLUE} strokeWidth="1" />
            <line x1="64" y1="54" x2="42" y2="54" stroke={BLUE} strokeWidth="0.5" />
            <line x1="64" y1="198" x2="42" y2="198" stroke={BLUE} strokeWidth="0.5" />
            <line x1="64" y1="202" x2="42" y2="202" stroke={BLUE} strokeWidth="0.5" />
            <line x1="64" y1="290" x2="42" y2="290" stroke={BLUE} strokeWidth="0.5" />
            <Tick x={46} y={54} />
            <Tick x={46} y={198} />
            <Tick x={46} y={202} />
            <Tick x={46} y={290} />
            <text
              x="40"
              y="127"
              textAnchor="middle"
              fontSize="10"
              fill={BLUE}
              transform="rotate(-90 40 127)"
              className="font-plexmono"
            >
              3,60
            </text>
            <text
              x="40"
              y="245"
              textAnchor="middle"
              fontSize="10"
              fill={BLUE}
              transform="rotate(-90 40 245)"
              className="font-plexmono"
            >
              2,20
            </text>
            {/* unten: 2,40 | 0,10 | 1,70 — damit Bad- und Vorraum-
                Breite direkt am Plan ablesbar sind */}
            <line x1="74" y1="314" x2="242" y2="314" stroke={BLUE} strokeWidth="1" />
            <line x1="74" y1="300" x2="74" y2="318" stroke={BLUE} strokeWidth="0.5" />
            <line x1="170" y1="300" x2="170" y2="318" stroke={BLUE} strokeWidth="0.5" />
            <line x1="174" y1="300" x2="174" y2="318" stroke={BLUE} strokeWidth="0.5" />
            <line x1="242" y1="300" x2="242" y2="318" stroke={BLUE} strokeWidth="0.5" />
            <Tick x={74} y={314} />
            <Tick x={170} y={314} />
            <Tick x={174} y={314} />
            <Tick x={242} y={314} />
            <text x="122" y="328" textAnchor="middle" fontSize="10" fill={BLUE} className="font-plexmono">
              2,40
            </text>
            <text x="208" y="328" textAnchor="middle" fontSize="10" fill={BLUE} className="font-plexmono">
              1,70
            </text>
            {/* rechts: 5,90 */}
            <line x1="440" y1="54" x2="440" y2="290" stroke={BLUE} strokeWidth="1" />
            <line x1="424" y1="54" x2="444" y2="54" stroke={BLUE} strokeWidth="0.5" />
            <line x1="424" y1="290" x2="444" y2="290" stroke={BLUE} strokeWidth="0.5" />
            <Tick x={440} y={54} />
            <Tick x={440} y={290} />
            <text
              x="452"
              y="172"
              textAnchor="middle"
              fontSize="10"
              fill={BLUE}
              transform="rotate(90 452 172)"
              className="font-plexmono"
            >
              5,90
            </text>
          </g>

          {/* Wände: dunkle Außenkontur, Räume weiß ausgespart */}
          <rect x="64" y="44" width="360" height="256" fill={INK} />
          {ROOMS.map((r) => (
            <rect key={r.name} x={r.rect.x} y={r.rect.y} width={r.rect.w} height={r.rect.h} fill="#ffffff" />
          ))}

          {/* Fenster (Doppellinie) */}
          <g stroke="#64748b" strokeWidth="1">
            {/* Zimmer, oben */}
            <rect x="128" y="44" width="60" height="10" fill="#ffffff" stroke="none" />
            <line x1="128" y1="47.5" x2="188" y2="47.5" />
            <line x1="128" y1="50.5" x2="188" y2="50.5" />
            {/* Wohnen, rechts */}
            <rect x="414" y="142" width="10" height="60" fill="#ffffff" stroke="none" />
            <line x1="417.5" y1="142" x2="417.5" y2="202" />
            <line x1="420.5" y1="142" x2="420.5" y2="202" />
            {/* Terrassentür Wohnen, unten */}
            <rect x="282" y="290" width="96" height="10" fill="#ffffff" stroke="none" />
            <line x1="282" y1="293.5" x2="378" y2="293.5" />
            <line x1="282" y1="296.5" x2="378" y2="296.5" />
          </g>

          {/* Türen (Öffnung + Türblatt + Viertelkreis) */}
          <g stroke="#94a3b8" strokeWidth="1" fill="none">
            {/* Zimmer → Vorraum (schlägt ins Zimmer auf, hält den
                engen Vorraum frei für die Beschriftung) */}
            <rect x="190" y="198" width="36" height="4" fill="#ffffff" stroke="none" />
            <line x1="190" y1="198" x2="190" y2="162" />
            <path d="M 190 162 A 36 36 0 0 1 226 198" />
            {/* Wohnen → Vorraum */}
            <rect x="242" y="220" width="4" height="36" fill="#ffffff" stroke="none" />
            <line x1="246" y1="220" x2="282" y2="220" />
            <path d="M 282 220 A 36 36 0 0 1 246 256" />
            {/* Bad → Vorraum */}
            <rect x="170" y="228" width="4" height="36" fill="#ffffff" stroke="none" />
            <line x1="170" y1="228" x2="134" y2="228" />
            <path d="M 134 228 A 36 36 0 0 0 170 264" />
            {/* Eingang */}
            <rect x="186" y="290" width="36" height="10" fill="#ffffff" stroke="none" />
            <line x1="186" y1="290" x2="186" y2="254" />
            <path d="M 186 254 A 36 36 0 0 1 222 290" />
          </g>

          {/* Raumbeschriftung */}
          {ROOMS.map((r) => {
            const cx = r.rect.x + r.rect.w / 2;
            const nameY = r.labelY ?? r.rect.y + r.rect.h / 2 - 4;
            const small = r.rect.w < 100;
            return (
              <g key={r.name}>
                <text
                  x={cx}
                  y={nameY}
                  textAnchor="middle"
                  fontSize={small ? 9 : 10}
                  fill="#64748b"
                >
                  {r.name}
                </text>
                <text
                  x={cx}
                  y={nameY + 16}
                  textAnchor="middle"
                  fontSize={small ? 10 : 11}
                  fill={INK}
                  className="font-plexmono"
                >
                  {r.floor} m²
                </text>
              </g>
            );
          })}

          {/* Erkennungs-Marker — einziger Einsatz des Logo-Orange */}
          {ROOMS.map((r, i) => (
            <rect
              key={r.name}
              x={r.rect.x + 5}
              y={r.rect.y + 5}
              width={r.rect.w - 10}
              height={r.rect.h - 10}
              rx="3"
              fill={ORANGE}
              fillOpacity="0.05"
              stroke={ORANGE}
              strokeWidth="1.5"
              className="lp-a"
              style={{ animationDelay: `${0.5 + i * 0.15}s` }}
            />
          ))}
        </svg>

        {/* Übergang Plan → Tabelle */}
        <div
          className="lp-a my-3 flex items-center gap-3"
          style={{ animationDelay: "1.15s" }}
          aria-hidden="true"
        >
          <span className="h-px flex-1 bg-slate-200" />
          <span className="flex items-center gap-1.5 font-plexmono text-[11px] uppercase tracking-wider text-primary">
            <ArrowDown className="h-3.5 w-3.5" />
            automatisch ermittelt
          </span>
          <span className="h-px flex-1 bg-slate-200" />
        </div>

        {/* Ergebnis: Mengenermittlung */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">
              Mengenermittlung aus dem Grundriss: Boden- und Wandflächen je Raum
            </caption>
            <thead>
              <tr className="border-b border-slate-200 font-plexmono text-[11px] uppercase tracking-wider text-slate-500">
                <th scope="col" className="whitespace-nowrap py-2 pr-2 text-left font-normal">
                  Raum
                </th>
                <th scope="col" className="whitespace-nowrap px-2 py-2 text-right font-normal">
                  Maße (m)
                </th>
                <th scope="col" className="whitespace-nowrap px-2 py-2 text-right font-normal">
                  Boden (m²)
                </th>
                <th scope="col" className="whitespace-nowrap py-2 pl-2 text-right font-normal">
                  Wand (m²)
                </th>
              </tr>
            </thead>
            <tbody>
              {ROOMS.map((r, i) => (
                <tr
                  key={r.name}
                  className="lp-a border-b border-slate-100"
                  style={{ animationDelay: `${1.3 + i * 0.08}s` }}
                >
                  <th scope="row" className="py-2 pr-2 text-left font-normal text-slate-700">
                    {r.name}
                  </th>
                  <td className="whitespace-nowrap px-2 py-2 text-right font-plexmono text-slate-500">
                    {r.dims}
                  </td>
                  <td className="px-2 py-2 text-right font-plexmono text-slate-900">{r.floor}</td>
                  <td className="py-2 pl-2 text-right font-plexmono text-slate-900">{r.wall}</td>
                </tr>
              ))}
              <tr className="lp-a font-medium" style={{ animationDelay: "1.65s" }}>
                <th scope="row" className="py-2.5 pr-2 text-left font-medium text-slate-900">
                  Summe
                </th>
                <td className="px-2 py-2.5" />
                <td className="border-t-2 border-slate-300 px-2 py-2.5 text-right font-plexmono text-slate-900">
                  {TOTALS.floor}
                </td>
                <td className="border-t-2 border-slate-300 py-2.5 pl-2 text-right font-plexmono text-slate-900">
                  {TOTALS.wall}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="lp-a mt-3 text-xs leading-relaxed text-slate-500" style={{ animationDelay: "1.75s" }}>
          Beispiel, direkt nachrechenbar: Boden = Länge × Breite, Wand = Umfang × lichte Höhe
          2,50&nbsp;m. Öffnungen bis 5&nbsp;m² (Fenster 1,50&nbsp;×&nbsp;1,40, Türen
          0,90&nbsp;×&nbsp;2,10) werden nach österreichischer Abrechnungslogik übermessen — nur
          die Terrassentür (2,40&nbsp;×&nbsp;2,10&nbsp;m) wird abgezogen.
        </p>
      </div>
    </div>
  );
}

const STEPS = [
  {
    nr: "01",
    title: "Plan hochladen",
    desc: "Grundriss oder Schnitt als PDF, auch mehrere Pläne je Projekt.",
  },
  {
    nr: "02",
    title: "Erkennung prüfen",
    desc: "BauLV erkennt Räume, Flächen und Öffnungen, Raumhöhen aus dem Schnitt. Sie sehen jeden Wert und korrigieren direkt.",
  },
  {
    nr: "03",
    title: "LV exportieren",
    desc: "Positionen in österreichischer Baufachsprache, Mengen mit Rechenweg. Export als PDF oder Excel.",
  },
];

// Jede Karte nur mit Aussagen, die der Code bzw. die eigene
// Datenschutzerklärung deckt. KEIN "Hosting in der EU" — die
// Datenschutzerklärung dokumentiert Railway Corp. (USA) und
// Anthropic PBC (USA) als Auftragsverarbeiter.
const TRUST: { title: string; desc: string; mono?: string }[] = [
  {
    title: "Deterministisch statt Blackbox",
    desc: "Die KI liest den Plan. Gerechnet wird regelbasiert und reproduzierbar, mit Berechnungsnachweis je Position:",
    mono: "20,20 × 2,50 − 5,04 = 45,46 m²",
  },
  {
    title: "Für den österreichischen Markt",
    desc: "LV-Positionen in österreichischer Baufachsprache, Abrechnungslogik nach österreichischen Baustandards, inklusive Übermessung kleiner Öffnungen.",
  },
  {
    title: "Ihre Daten, Ihre Kontrolle",
    desc: "Datenexport und Löschung jederzeit direkt im Konto. Alle Auftragsverarbeiter transparent in der Datenschutzerklärung.",
  },
  {
    title: "Ehrliche Beta",
    desc: "BauLV ist in der Testphase, und das steht auch so auf der Seite. Sie testen kostenlos und sehen den echten Stand.",
  },
];

const PLANS = [
  {
    name: "Basis",
    price: "€ 49",
    interval: "/ Monat netto",
    features: ["3 aktive Projekte", "Manueller LV-Editor", "PDF-Export"],
    cta: "Kostenlos testen",
    href: "/register",
    popular: false,
  },
  {
    name: "Pro",
    price: "€ 149",
    interval: "/ Monat netto",
    features: [
      "Unbegrenzte Projekte",
      "KI-Plananalyse (PDF → Räume & Flächen)",
      "Automatische Wand- & Umfangberechnung",
      "KI-generierte LV-Positionen",
      "KI-Chatassistent",
      "Excel + PDF Export",
      "Prioritäts-Support",
    ],
    cta: "Kostenlos testen",
    href: "/register",
    popular: true,
  },
  {
    name: "Enterprise",
    price: "Auf Anfrage",
    interval: "",
    // Nur Angebotsvergleich/Team sind noch nicht gebaut — ehrlich
    // als "in Entwicklung" gekennzeichnet. API-Zugang (MCP + REST)
    // und dedizierter Support existieren.
    features: [
      "Alles aus Pro",
      "API-Zugang (MCP & REST)",
      "Dedizierter Support",
      "Angebotsvergleich / Preisspiegel (in Entwicklung)",
      "Team- und Multi-User-Konten (in Entwicklung)",
    ],
    cta: "Kontakt aufnehmen",
    href: "mailto:kontakt@baulv.at?subject=Enterprise-Plan%20Anfrage",
    popular: false,
  },
];

export function LandingPage() {
  return (
    <div className="min-h-screen bg-white font-plex text-slate-900 antialiased">
      {/* Beta warning — dismissible, persisted in localStorage */}
      <BetaBanner />

      {/* Navbar */}
      <nav className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2.5">
            <img src="/icons/icon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-semibold tracking-tight">BauLV</span>
          </Link>
          <div className="hidden items-center gap-6 md:flex">
            <a href="#ablauf" className="text-sm text-slate-600 hover:text-slate-900">
              So funktioniert's
            </a>
            <a href="#preise" className="text-sm text-slate-600 hover:text-slate-900">
              Preise
            </a>
            <Link to="/api-pricing" className="text-sm text-slate-600 hover:text-slate-900">
              API
            </Link>
            <Link to="/developers" className="text-sm text-slate-600 hover:text-slate-900">
              Entwickler
            </Link>
            {/* Hover abdunkeln statt Opacity — /80-Blau auf Weiß fällt
                unter 4,5:1 (WCAG AA). blue-700 bleibt im Markenton. */}
            <Link to="/login" className="text-sm font-medium text-primary hover:text-blue-700">
              Anmelden
            </Link>
            <Link
              to="/register"
              className="bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-blue-700"
            >
              Kostenlos testen
            </Link>
          </div>
          {/* Mobile */}
          <div className="flex items-center gap-3 md:hidden">
            <Link to="/login" className="text-sm font-medium text-primary">
              Login
            </Link>
            <Link
              to="/register"
              className="bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
            >
              Testen
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero: die Verwandlung Plan → Mengenermittlung */}
      <header className="mx-auto max-w-6xl px-6 py-14 md:py-20">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div>
            <p className="text-sm font-medium text-primary">
              Für österreichische Bauträger und Baumeister
            </p>
            <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-tight md:text-5xl">
              Mengenermittlung — automatisch aus dem Bauplan.
            </h1>
            <p className="mt-5 text-lg leading-relaxed text-slate-600">
              BauLV liest Grundrisse und Schnitte als PDF, erkennt Räume, Flächen und Raumhöhen
              und erstellt daraus ein prüfbares Leistungsverzeichnis. Jede Menge mit
              nachvollziehbarem Rechenweg.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/register"
                className="inline-flex items-center justify-center gap-2 bg-primary px-6 py-3 text-base font-medium text-primary-foreground hover:bg-blue-700"
              >
                Kostenlos testen
                <ArrowRight className="h-5 w-5" />
              </Link>
              <a
                href="#ablauf"
                className="inline-flex items-center justify-center gap-2 border border-slate-300 px-6 py-3 text-base font-medium text-slate-700 hover:bg-slate-50"
              >
                So funktioniert's
              </a>
            </div>
            <p className="mt-4 text-sm text-slate-500">
              Kostenloses Konto · in der Beta mit allen Pro-Funktionen · Start-Gewerk
              Malerarbeiten
            </p>
          </div>
          <HeroTransformation />
        </div>
      </header>

      {/* Problem → Lösung */}
      <section id="problem" className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <h2 className="max-w-xl text-2xl font-semibold tracking-tight md:text-3xl">
            Das Aufmaß ist der Flaschenhals.
          </h2>
          <div className="mt-10 grid gap-10 md:grid-cols-2 md:gap-0 md:divide-x md:divide-slate-200">
            <div className="md:pr-12">
              <h3 className="font-plexmono text-[11px] uppercase tracking-wider text-slate-500">
                Von Hand
              </h3>
              <ul className="mt-4 space-y-4 text-slate-600">
                <li className="flex gap-3">
                  <span className="select-none text-slate-300" aria-hidden="true">
                    —
                  </span>
                  Skalieren, Ablesen, Notieren — Raum für Raum, Plan für Plan.
                </li>
                <li className="flex gap-3">
                  <span className="select-none text-slate-300" aria-hidden="true">
                    —
                  </span>
                  Jede Übertragung in Excel oder ins LV ist eine eigene Fehlerquelle.
                </li>
                <li className="flex gap-3">
                  <span className="select-none text-slate-300" aria-hidden="true">
                    —
                  </span>
                  Der Rechenweg steht am Ende nirgends. Nachprüfen heißt Nachmessen.
                </li>
              </ul>
            </div>
            <div className="md:pl-12">
              <h3 className="font-plexmono text-[11px] uppercase tracking-wider text-primary">
                Mit BauLV
              </h3>
              <ul className="mt-4 space-y-4 text-slate-900">
                <li className="flex gap-3">
                  <span className="select-none text-primary" aria-hidden="true">
                    —
                  </span>
                  Plan als PDF hochladen: Räume, Flächen und Öffnungen werden erkannt.
                </li>
                <li className="flex gap-3">
                  <span className="select-none text-primary" aria-hidden="true">
                    —
                  </span>
                  Jeder Wert bleibt sichtbar und korrigierbar, bevor er ins LV geht.
                </li>
                <li className="flex gap-3">
                  <span className="select-none text-primary" aria-hidden="true">
                    —
                  </span>
                  Jede Menge trägt Formel und Herkunft — prüfbar statt Blackbox.
                </li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* So funktioniert's */}
      <section id="ablauf" className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">So funktioniert's</h2>
          <p className="mt-2 text-slate-600">Kein CAD-Import, kein Setup. Ein PDF genügt.</p>
          <ol className="mt-10 grid gap-10 md:grid-cols-3">
            {STEPS.map((s) => (
              <li key={s.nr} className="border-t border-slate-200 pt-5">
                <span className="font-plexmono text-sm text-primary">{s.nr}</span>
                <h3 className="mt-2 text-lg font-semibold">{s.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{s.desc}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Vertrauen */}
      <section id="vertrauen" className="border-t border-slate-200 bg-slate-50">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">
            Worauf Sie sich verlassen können
          </h2>
          <div className="mt-10 grid gap-px overflow-hidden border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
            {TRUST.map((t) => (
              <div key={t.title} className="bg-white p-6">
                <h3 className="font-semibold">{t.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{t.desc}</p>
                {t.mono && (
                  <p className="mt-2 font-plexmono text-xs text-slate-900">{t.mono}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Preise */}
      <section id="preise" className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">Preise</h2>
          <p className="mt-2 text-slate-600">Netto, monatlich, jederzeit kündbar.</p>
          <div className="mt-10 grid gap-px overflow-hidden border border-slate-200 bg-slate-200 md:grid-cols-3">
            {PLANS.map((plan) => (
              <div
                key={plan.name}
                className={`flex flex-col border-t-2 bg-white p-8 ${
                  plan.popular ? "border-primary" : "border-transparent"
                }`}
              >
                <div className="flex items-baseline justify-between">
                  <h3 className="text-lg font-semibold">{plan.name}</h3>
                  {plan.popular && (
                    <span className="font-plexmono text-[11px] uppercase tracking-wider text-primary">
                      Empfohlen
                    </span>
                  )}
                </div>
                <div className="mt-4 flex h-12 items-end">
                  <span
                    className={`font-plexmono font-medium ${
                      plan.interval ? "text-4xl" : "text-2xl"
                    }`}
                  >
                    {plan.price}
                  </span>
                  {plan.interval && (
                    <span className="ml-2 pb-1 text-sm text-slate-500">{plan.interval}</span>
                  )}
                </div>
                <ul className="mb-8 mt-6 flex-1 space-y-3">
                  {plan.features.map((f) => (
                    <li key={f} className="flex gap-3 text-sm text-slate-700">
                      <span className="select-none text-primary" aria-hidden="true">
                        —
                      </span>
                      {f}
                    </li>
                  ))}
                </ul>
                {plan.href.startsWith("mailto:") ? (
                  <a
                    href={plan.href}
                    className="inline-flex items-center justify-center gap-2 border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    {plan.cta}
                    <ArrowRight className="h-4 w-4" />
                  </a>
                ) : (
                  <Link
                    to={plan.href}
                    className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium ${
                      plan.popular
                        ? "bg-primary text-primary-foreground hover:bg-blue-700"
                        : "border border-slate-300 text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    {plan.cta}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                )}
              </div>
            ))}
          </div>
          <p className="mt-8 text-sm text-slate-500">
            Alle Preise netto, zzgl. gesetzlicher USt. Angebot ausschließlich für Unternehmer
            (§&nbsp;1 UGB). Während der Beta-Phase testen Sie kostenlos mit allen Funktionen des
            Pro-Plans.
          </p>
        </div>
      </section>

      {/* Abschluss-CTA — bleibt auf dem weißen Blatt; die dicke
          Tinte-Linie schließt die Seite wie ein Plankopf-Feld ab. */}
      <section className="border-t-2 border-slate-900">
        <div className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <h2 className="max-w-2xl text-2xl font-semibold tracking-tight md:text-3xl">
            Laden Sie einen Plan hoch und prüfen Sie das Ergebnis selbst.
          </h2>
          <p className="mt-3 max-w-2xl text-slate-600">
            Kostenloses Konto, keine Zahlungsdaten. Der beste Test ist Ihr eigener Grundriss.
          </p>
          <Link
            to="/register"
            className="mt-8 inline-flex items-center gap-2 bg-primary px-6 py-3 text-base font-medium text-primary-foreground hover:bg-blue-700"
          >
            Kostenlos testen
            <ArrowRight className="h-5 w-5" />
          </Link>
        </div>
      </section>

      {/* Footer */}
      <Footer />

      {/* Floating support-chat widget — public, rate-limited endpoint,
          only mounted on the landing page (not inside the app shell). */}
      <SupportChat />
    </div>
  );
}

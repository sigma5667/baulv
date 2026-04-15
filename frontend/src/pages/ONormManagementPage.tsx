import {
  BookOpen,
  CheckCircle2,
  Clock,
  ExternalLink,
  ShieldCheck,
} from "lucide-react";

/**
 * Integrated ÖNORM rule sets.
 *
 * IMPORTANT LEGAL NOTE:
 * BauLV does **not** store the copyrighted ÖNORM text on its servers.
 * Uploading third-party ÖNORM PDFs would violate Austrian Standards
 * International's copyright, so that functionality has been removed.
 *
 * Instead, each module implements the **mathematical calculation rules**
 * (factors, thresholds, deduction logic) as plain Python code in
 * backend/app/calculation_engine/trades/. Formulas and algorithms are not
 * copyrightable — only the expressive text of the ÖNORM is.
 */

type ModuleStatus = "implemented" | "in_development";

interface ONormModule {
  code: string;
  title: string;
  description: string;
  status: ModuleStatus;
}

const MODULES: ONormModule[] = [
  {
    code: "B 2230-1",
    title: "Maler- und Beschichtungsarbeiten",
    description:
      "Wandflächen, Deckenflächen, Leibungen inkl. Öffnungsabzug ab 2,5 m², Nassraumzuschlag und Höhenzuschlag laut ÖNORM.",
    status: "implemented",
  },
  {
    code: "B 2215",
    title: "Fliesen- und Plattenarbeiten",
    description:
      "Flächenermittlung für Wand- und Bodenfliesen, Sockelleisten, Dehnfugen und Eckprofile.",
    status: "in_development",
  },
  {
    code: "B 2213",
    title: "Estricharbeiten",
    description:
      "Mengenermittlung für Zement-, Anhydrit- und Fließestriche inkl. Randstreifen und Dämmung.",
    status: "in_development",
  },
  {
    code: "B 2219",
    title: "Trockenbauarbeiten",
    description:
      "Gipskarton-Wände und -Decken, Ständerwerk, Dämmung und Abrechnung nach Wand- und Deckenflächen.",
    status: "in_development",
  },
  {
    code: "B 2220",
    title: "Tischlerarbeiten",
    description:
      "Türen, Fenster, Einbaumöbel — Mengenermittlung nach Stück und laufenden Metern.",
    status: "in_development",
  },
  {
    code: "B 2210",
    title: "Putz- und Verputzarbeiten",
    description:
      "Innen- und Außenputz, Abzugsregeln für Öffnungen, Höhenzuschläge und Kantenzuschläge.",
    status: "in_development",
  },
];

const STATUS_LABEL: Record<ModuleStatus, string> = {
  implemented: "implementiert",
  in_development: "in Entwicklung",
};

export function ONormManagementPage() {
  return (
    <div className="p-6">
      <div className="mb-6 flex items-center gap-3">
        <BookOpen className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">ÖNORM-Bibliothek</h1>
      </div>

      <p className="mb-6 max-w-3xl text-sm text-muted-foreground">
        BauLV arbeitet mit fest integrierten ÖNORM-Regelwerken. Die
        Berechnungsregeln der jeweiligen Norm sind als mathematische Algorithmen
        direkt in der Software implementiert — es werden keine ÖNORM-Dokumente
        hochgeladen oder gespeichert.
      </p>

      {/* Modules grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {MODULES.map((m) => {
          const isImplemented = m.status === "implemented";
          return (
            <div
              key={m.code}
              aria-disabled={!isImplemented}
              className={`relative rounded-lg border p-5 transition-colors ${
                isImplemented
                  ? "border-primary/30 bg-card hover:border-primary/60"
                  : "border-border bg-muted/30 text-muted-foreground"
              }`}
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    ÖNORM
                  </p>
                  <h2 className="text-lg font-bold">{m.code}</h2>
                </div>
                <span
                  className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    isImplemented
                      ? "bg-green-100 text-green-800"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {isImplemented ? (
                    <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                  ) : (
                    <Clock className="h-3 w-3" aria-hidden="true" />
                  )}
                  {STATUS_LABEL[m.status]}
                </span>
              </div>
              <h3
                className={`mb-2 text-sm font-semibold ${
                  isImplemented ? "text-foreground" : "text-muted-foreground"
                }`}
              >
                {m.title}
              </h3>
              <p className="text-xs leading-relaxed">{m.description}</p>
            </div>
          );
        })}
      </div>

      {/* Copyright notice */}
      <div className="mt-8 rounded-lg border border-blue-200 bg-blue-50 p-5 text-sm text-blue-900">
        <div className="mb-2 flex items-center gap-2 font-semibold">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          Hinweis zum Urheberrecht
        </div>
        <p className="mb-3">
          BauLV speichert <strong>keine</strong> urheberrechtlich geschützten
          ÖNORM-Dokumente. Alle Berechnungsregeln sind als mathematische
          Algorithmen direkt in der Software implementiert. Für den
          vollständigen ÖNORM-Text verweisen wir auf den Webshop von Austrian
          Standards:
        </p>
        <a
          href="https://www.austrian-standards.at"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-blue-700 hover:underline"
        >
          www.austrian-standards.at
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </a>
      </div>
    </div>
  );
}

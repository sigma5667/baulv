/**
 * Coming-Soon-Schutz vor der öffentlichen App (v24.4.2).
 *
 * Erscheint überall dort, wo ``<PublicWithGate>`` vor einer Route
 * sitzt UND weder JWT-Login noch ein gültiges Beta-Session-Token
 * vorhanden ist. Die Legal-Routen (``/impressum``, ``/datenschutz``,
 * ``/agb``) liegen außerhalb des Gates — Pflichtseiten nach ECG §5
 * müssen immer erreichbar sein, auch ohne Code-Eingabe.
 *
 * Bei Erfolg verschwindet der Gate von selbst: ``submit()`` ändert
 * den State im ``useBetaUnlock``-Hook, der Wrapper rendert neu, die
 * Page hinter dem Gate wird sichtbar. URL bleibt erhalten — wer auf
 * ``/login`` gelandet ist, sieht nach Unlock direkt die LoginPage,
 * kein Bounce auf ``/``.
 */
import { useState, type FormEvent } from "react";
import { Building2, Lock } from "lucide-react";

import { useBetaUnlock } from "../hooks/useBetaUnlock";
import { useToast } from "../components/Toast";
import { Footer } from "../components/layout/Footer";

export function BetaGatePage() {
  const { submit } = useBetaUnlock();
  const toast = useToast();
  const [code, setCode] = useState("");
  const [isPending, setIsPending] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = code.trim();
    if (!trimmed || isPending) return;
    setIsPending(true);
    try {
      await submit(trimmed);
      // Bei Erfolg unmounted die Gate-Seite von selbst, weil der
      // Wrapper ``isUnlocked`` neu auswertet.
    } catch {
      // Bewusst keine Fehlerdetails — sowohl 401 (falscher Code) als
      // auch 429 (Rate-Limit) und 5xx werden gleich beantwortet.
      // Genauere Diagnose bleibt in den Browser-DevTools sichtbar.
      toast.error("Code ungültig");
    } finally {
      setIsPending(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-6 py-12">
        {/* Logo + Brand */}
        <div className="mb-8 flex items-center justify-center gap-2 font-bold text-primary">
          <Building2 className="h-8 w-8" />
          <span className="text-2xl">BauLV</span>
        </div>

        <h1 className="mb-2 text-center text-2xl font-bold">Beta-Phase</h1>
        <p className="mb-8 text-center text-sm text-muted-foreground">
          BauLV ist aktuell in der geschlossenen Test-Phase. Die
          öffentliche Freigabe folgt, sobald Impressum, AGB und
          Auftragsverarbeitungs-Verträge final geprüft sind.
        </p>

        {/* Code-Eingabe */}
        <div className="rounded-lg border bg-card p-6 shadow-sm">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Lock className="h-4 w-4 text-primary" />
            Sie haben einen Beta-Code?
          </h2>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Beta-Code eingeben"
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
              disabled={isPending}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none disabled:opacity-50"
              aria-label="Beta-Code"
            />
            <button
              type="submit"
              disabled={!code.trim() || isPending}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isPending ? "Prüfe…" : "Zugang freischalten"}
            </button>
          </form>
        </div>

        {/* Kontakt-Hinweis */}
        <div className="mt-6 rounded-lg border bg-muted/20 p-6">
          <h2 className="mb-2 text-sm font-semibold">
            Sie sind Bauträger und interessiert?
          </h2>
          <p className="text-sm text-muted-foreground">
            Schreiben Sie uns an{" "}
            <a
              href="mailto:kontakt@baulv.at"
              className="font-medium text-primary hover:underline"
            >
              kontakt@baulv.at
            </a>{" "}
            — wir melden uns, sobald die nächste Test-Runde startet.
          </p>
        </div>
      </main>

      <Footer />
    </div>
  );
}

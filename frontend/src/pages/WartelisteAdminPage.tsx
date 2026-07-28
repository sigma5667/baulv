/**
 * /app/admin/warteliste — Warteliste-Übersicht + Update-Versand (v25.1).
 *
 * Gleiche Doppel-Absicherung wie /admin/analytics: das Backend gated
 * mit 403 (ADMIN_EMAILS), das Frontend rendert zusätzlich einen
 * lokalen "Zugriff verweigert"-Fallback.
 *
 * Der Update-Versand erzwingt den Trockenlauf als ersten Schritt im
 * UI: der Echt-Senden-Button ist erst aktiv, wenn ein Trockenlauf für
 * EXAKT den aktuellen Betreff+Text gelaufen ist — jede Änderung am
 * Formular entwertet die Vorschau wieder. (Das Backend erzwingt das
 * nicht; die Reihenfolge ist eine reine Bedien-Leitplanke.)
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ClipboardList,
  Loader2,
  Lock,
  Mail,
  RefreshCw,
  Send,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import {
  fetchWaitlistAdmin,
  sendWaitlistUpdate,
  type WaitlistUpdateSendResult,
} from "../api/waitlist";

const PAGE_SIZE = 50;

const STATUS_LABELS: Record<string, string> = {
  confirmed: "bestätigt",
  pending: "ausstehend",
  unsubscribed: "abgemeldet",
};

const STATUS_CLASSES: Record<string, string> = {
  confirmed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  pending: "bg-amber-50 text-amber-700 border-amber-200",
  unsubscribed: "bg-slate-100 text-slate-500 border-slate-200",
};

export function WartelisteAdminPage() {
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);

  const overviewQ = useQuery({
    queryKey: ["waitlist-admin", offset],
    queryFn: () => fetchWaitlistAdmin({ limit: PAGE_SIZE, offset }),
    enabled: !!user?.is_admin,
  });

  // --- Update-Versand-Formular -------------------------------------
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  // Vorschau ist an den exakten Stand von Betreff+Text gebunden —
  // jede Änderung entwertet sie (Senden wieder gesperrt).
  const [preview, setPreview] = useState<{
    forSubject: string;
    forBody: string;
    recipients: number;
    text: string;
  } | null>(null);
  const [busy, setBusy] = useState<"dry" | "send" | null>(null);
  const [sendResult, setSendResult] =
    useState<WaitlistUpdateSendResult | null>(null);
  const [sendError, setSendError] = useState("");

  const previewValid =
    preview !== null &&
    preview.forSubject === subject &&
    preview.forBody === body;

  const runDry = async () => {
    setBusy("dry");
    setSendError("");
    setSendResult(null);
    try {
      const res = await sendWaitlistUpdate({
        subject,
        body,
        dry_run: true,
      });
      setPreview({
        forSubject: subject,
        forBody: body,
        recipients: res.recipients,
        text: res.preview?.text ?? "",
      });
    } catch (err: any) {
      setSendError(
        err?.response?.data?.detail ??
          "Trockenlauf fehlgeschlagen. Bitte erneut versuchen.",
      );
    } finally {
      setBusy(null);
    }
  };

  const runSend = async () => {
    if (!previewValid) return;
    setBusy("send");
    setSendError("");
    try {
      const res = await sendWaitlistUpdate({
        subject,
        body,
        dry_run: false,
      });
      setSendResult(res);
      setPreview(null);
    } catch (err: any) {
      setSendError(
        err?.response?.data?.detail ??
          "Versand fehlgeschlagen. Bitte Server-Log prüfen.",
      );
    } finally {
      setBusy(null);
    }
  };

  if (user && !user.is_admin) {
    return (
      <div className="p-6">
        <div className="flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <Lock className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-medium">Zugriff verweigert</p>
            <p className="mt-1">
              Diese Seite ist nur für Administratoren zugänglich.
            </p>
            <Link
              to="/app"
              className="mt-2 inline-flex font-medium text-primary hover:underline"
            >
              Zurück zum Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const data = overviewQ.data;

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-6 w-6 text-primary" />
          <h1 className="text-2xl font-bold">Warteliste</h1>
        </div>
        <button
          onClick={() => overviewQ.refetch()}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <RefreshCw className="h-3 w-3" />
          Aktualisieren
        </button>
      </div>

      {overviewQ.isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Lade Warteliste…
        </div>
      )}

      {overviewQ.isError && (
        <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Konnte die Warteliste nicht laden. Bitte erneut versuchen.
        </div>
      )}

      {data && (
        <div className="space-y-8">
          {/* Kennzahlen — technisches Blatt: nüchterne Zahlen, Mono */}
          <div className="grid gap-px border border-slate-300 bg-slate-300 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Gesamt" value={data.total} />
            <Stat
              label="Bestätigt"
              value={data.counts.confirmed}
              highlight
              hint="Die Zahl, die zählt"
            />
            <Stat label="Ausstehend" value={data.counts.pending} />
            <Stat label="Abgemeldet" value={data.counts.unsubscribed} />
            <Stat label="Letzte 7 Tage" value={data.signups_last_7d} />
            <Stat label="Letzte 30 Tage" value={data.signups_last_30d} />
          </div>

          {/* Herkunft (?ref=) */}
          <section className="border border-slate-300 p-5">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide">
              Anmeldungen nach Quelle
            </h2>
            {data.sources.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Noch keine Anmeldungen.
              </p>
            ) : (
              <div className="space-y-2">
                {data.sources.map((row) => {
                  const pct =
                    data.total > 0 ? (row.count / data.total) * 100 : 0;
                  return (
                    <div key={row.source ?? "__direkt__"}>
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-mono text-xs">
                          {row.source ?? "(direkt / ohne ref)"}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {row.count} ({pct.toFixed(0)} %)
                        </span>
                      </div>
                      <div className="mt-1 h-2 bg-slate-100">
                        <div
                          className="h-full bg-primary"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          {/* Update-Versand */}
          <section className="border border-slate-300 p-5">
            <div className="mb-3 flex items-center gap-2">
              <Mail className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold uppercase tracking-wide">
                Update an alle Bestätigten senden
              </h2>
            </div>
            <p className="mb-4 text-sm text-muted-foreground">
              Geht einzeln an alle {data.counts.confirmed} bestätigten
              Einträge, jeweils mit persönlichem Abmelde-Link und
              Firmen-Footer. Erst Trockenlauf, dann senden.
            </p>

            <div className="space-y-3">
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                maxLength={200}
                placeholder="Betreff, z. B. „BauLV-Update: Schnitt-Analyse ist da“"
                className="w-full border border-slate-300 px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                maxLength={10000}
                rows={7}
                placeholder={
                  "Nachricht als einfacher Text. Leerzeile = neuer Absatz.\n\nAnrede und Abmelde-Link ergänzt das System automatisch."
                }
                className="w-full border border-slate-300 px-3 py-2 font-mono text-sm focus:border-primary focus:outline-none"
              />
              <div className="flex flex-wrap items-center gap-3">
                <button
                  onClick={runDry}
                  disabled={busy !== null || !subject.trim() || !body.trim()}
                  className="border border-slate-800 px-4 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-40"
                >
                  {busy === "dry" ? (
                    <span className="flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Prüfe…
                    </span>
                  ) : (
                    "1. Trockenlauf (Vorschau)"
                  )}
                </button>
                <button
                  onClick={runSend}
                  disabled={busy !== null || !previewValid}
                  title={
                    previewValid
                      ? undefined
                      : "Erst Trockenlauf ausführen — Änderungen am Text entwerten die Vorschau."
                  }
                  className="flex items-center gap-2 bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                >
                  {busy === "send" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                  {previewValid
                    ? `2. Jetzt an ${preview.recipients} Empfänger senden`
                    : "2. Senden (erst Trockenlauf)"}
                </button>
              </div>

              {sendError && (
                <div className="border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
                  {sendError}
                </div>
              )}

              {previewValid && (
                <div className="border border-slate-300 bg-slate-50 p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Vorschau — {preview.recipients} Empfänger, noch nichts
                    gesendet
                  </p>
                  <p className="mb-2 text-sm font-medium">
                    Betreff: {preview.forSubject}
                  </p>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-xs text-slate-700">
                    {preview.text}
                  </pre>
                </div>
              )}

              {sendResult && (
                <div className="border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                  Versand abgeschlossen: {sendResult.sent} von{" "}
                  {sendResult.recipients} gesendet
                  {sendResult.failed > 0 &&
                    ` — ${sendResult.failed} fehlgeschlagen (Server-Log prüfen)`}
                  .
                </div>
              )}
            </div>
          </section>

          {/* Einträge */}
          <section className="border border-slate-300">
            <div className="flex items-center justify-between border-b border-slate-300 px-5 py-3">
              <h2 className="text-sm font-semibold uppercase tracking-wide">
                Einträge ({data.total})
              </h2>
              <div className="flex items-center gap-2 text-sm">
                <button
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  disabled={offset === 0}
                  className="border border-slate-300 px-3 py-1 hover:bg-slate-50 disabled:opacity-40"
                >
                  ← Zurück
                </button>
                <span className="font-mono text-xs text-muted-foreground">
                  {data.total === 0 ? 0 : offset + 1}–
                  {Math.min(offset + PAGE_SIZE, data.total)}
                </span>
                <button
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= data.total}
                  className="border border-slate-300 px-3 py-1 hover:bg-slate-50 disabled:opacity-40"
                >
                  Weiter →
                </button>
              </div>
            </div>
            {data.entries.length === 0 ? (
              <p className="px-5 py-4 text-sm text-muted-foreground">
                Noch keine Anmeldungen.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-slate-300 bg-slate-50 text-left text-xs uppercase tracking-wide">
                    <tr>
                      <th className="px-4 py-2 font-medium">E-Mail</th>
                      <th className="px-4 py-2 font-medium">Firma</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                      <th className="px-4 py-2 font-medium">Quelle</th>
                      <th className="px-4 py-2 font-medium">Anmeldung</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {data.entries.map((row) => (
                      <tr key={row.email}>
                        <td className="px-4 py-2 font-mono text-xs">
                          {row.email}
                        </td>
                        <td className="px-4 py-2">{row.company_name}</td>
                        <td className="px-4 py-2">
                          <span
                            className={`inline-block border px-2 py-0.5 text-xs ${
                              STATUS_CLASSES[row.status] ?? ""
                            }`}
                          >
                            {STATUS_LABELS[row.status] ?? row.status}
                          </span>
                        </td>
                        <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                          {row.source ?? "—"}
                        </td>
                        <td className="px-4 py-2 font-mono text-xs">
                          {new Date(row.signup_at).toLocaleDateString(
                            "de-AT",
                            {
                              day: "2-digit",
                              month: "2-digit",
                              year: "numeric",
                            },
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  highlight = false,
}: {
  label: string;
  value: number;
  hint?: string;
  highlight?: boolean;
}) {
  return (
    <div className={`bg-white p-4 ${highlight ? "bg-emerald-50/50" : ""}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={`mt-1 font-mono text-3xl font-bold ${
          highlight ? "text-emerald-700" : ""
        }`}
      >
        {value}
      </p>
      {hint && (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}

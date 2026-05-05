/**
 * /app/settings/datenschutz — privacy settings panel (v23.8).
 *
 * Three sections:
 *
 *   1. Analytics opt-in toggle + industry dropdown. Persists to
 *      ``PUT /auth/me/analytics-consent``. Toggling fires a fresh
 *      ``consent_snapshots`` row server-side so the DSGVO Art. 7
 *      evidence trail captures every flip.
 *
 *   2. Per-user analytics events (DSGVO Art. 20 — right to data
 *      portability). Pulled from
 *      ``GET /auth/me/analytics-events`` and shown as a paginated
 *      table. Empty when the user opted out and never had events.
 *
 *   3. Plain-text disclosure of what's stored, why it's
 *      pseudonymised, and the link to the full Datenschutzerklärung.
 *
 * Lives inside ``AppShell`` so the user reaches it via the
 * "Datenschutz" entry under Profile.
 */
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ShieldCheck,
  Loader2,
  Database,
  AlertTriangle,
  Check,
  ExternalLink,
  Info,
} from "lucide-react";
import {
  fetchAnalyticsConsent,
  fetchMyAnalyticsEvents,
  updateAnalyticsConsent,
} from "../api/auth";
import {
  INDUSTRY_LABELS,
  type IndustrySegment,
  type UserAnalyticsEvent,
} from "../types/user";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "../components/Toast";

export function PrivacySettingsPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { refreshUser } = useAuth();

  const consentQ = useQuery({
    queryKey: ["analytics-consent"],
    queryFn: fetchAnalyticsConsent,
  });

  // Local edit state, initialised from the server query once it
  // resolves. Splitting consent + industry into separate locals
  // means a user can flip the toggle without losing an in-flight
  // industry choice — the mutation only fires on Save.
  const [consent, setConsent] = useState<boolean>(false);
  const [industry, setIndustry] = useState<IndustrySegment | "">("");
  // Track whether the form is dirty so the Save button only enables
  // when the user actually changed something.
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!consentQ.data) return;
    setConsent(consentQ.data.analytics_consent);
    setIndustry(consentQ.data.industry_segment ?? "");
    setDirty(false);
  }, [consentQ.data]);

  const updateMut = useMutation({
    mutationFn: () =>
      updateAnalyticsConsent({
        analytics_consent: consent,
        // ``null`` clears the industry on the server (pydantic
        // differentiates omitted vs. null on the wire).
        industry_segment:
          consent && industry ? (industry as IndustrySegment) : null,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["analytics-consent"] });
      // Re-pull the user record so the AppShell + nav state pick
      // up the new ``analytics_consent`` flag.
      void refreshUser();
      toast.success(
        data.analytics_consent
          ? "Analytics-Einstellungen aktualisiert."
          : "Analytics deaktiviert. Es werden keine neuen Daten mehr gesammelt.",
      );
      setDirty(false);
    },
    onError: () => {
      toast.error("Speichern fehlgeschlagen. Bitte erneut versuchen.");
    },
  });

  const eventsQ = useQuery({
    queryKey: ["my-analytics-events"],
    queryFn: () => fetchMyAnalyticsEvents(200),
  });

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center gap-2">
        <ShieldCheck className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Datenschutz-Einstellungen</h1>
      </div>

      <p className="mb-8 max-w-3xl text-sm text-muted-foreground">
        BauLV verarbeitet Ihre Daten DSGVO-konform. Diese Seite
        steuert die <strong>optionale</strong> Erhebung anonymisierter
        Nutzungsdaten zur Produkt-Verbesserung und zeigt, welche
        Daten zu Ihrem pseudonymisierten Profil gespeichert sind.
        Details siehe{" "}
        <Link
          to="/datenschutz"
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-primary hover:underline"
        >
          Datenschutzerklärung
        </Link>
        .
      </p>

      {/* --- Toggle section ------------------------------------------ */}
      <section className="mb-10 rounded-lg border bg-card p-6">
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-primary/10 p-2">
            <Database className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-semibold">
              Anonymisierte Nutzungsdaten
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Wenn aktiviert, werden Ereignisse wie "Projekt erstellt"
              oder "Vorlage benutzt" als anonymisierte Datensätze
              gespeichert (kein Name, keine E-Mail, keine Adresse).
              Wir verwenden die aggregierten Daten zur
              Produkt-Verbesserung und für anonyme Branchen-Statistiken.
            </p>
          </div>
        </div>

        {consentQ.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Lade aktuelle Einstellung…
          </div>
        ) : consentQ.isError ? (
          <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            Konnte aktuelle Einstellung nicht laden. Bitte Seite neu
            laden.
          </div>
        ) : (
          <>
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => {
                  setConsent(e.target.checked);
                  if (!e.target.checked) setIndustry("");
                  setDirty(true);
                }}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-foreground">
                Ich bin damit einverstanden, dass anonymisierte
                Nutzungsdaten zur Produkt-Verbesserung gesammelt
                werden.
              </span>
            </label>

            {consent && (
              <div className="ml-6 mt-3 max-w-xs">
                <label className="block text-xs font-medium text-muted-foreground">
                  Branche{" "}
                  <span className="text-muted-foreground/60">
                    (optional, hilft uns die Daten zu segmentieren)
                  </span>
                </label>
                <select
                  value={industry}
                  onChange={(e) => {
                    setIndustry(e.target.value as IndustrySegment | "");
                    setDirty(true);
                  }}
                  className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">— bitte wählen —</option>
                  {(
                    Object.entries(INDUSTRY_LABELS) as [
                      IndustrySegment,
                      string,
                    ][]
                  ).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Withdrawal hint — appears only when the user is
                currently consented + about to disable. */}
            {consentQ.data?.analytics_consent && !consent && (
              <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p>
                  Bei Deaktivierung werden keine neuen Daten mehr
                  gesammelt. Bisherige Datensätze bleiben in
                  pseudonymisierter Form bestehen — sie sind ohne den
                  serverseitigen Salt nicht mehr Ihrem Konto
                  zuordenbar.
                </p>
              </div>
            )}

            <div className="mt-6 flex gap-2">
              <button
                type="button"
                onClick={() => updateMut.mutate()}
                disabled={!dirty || updateMut.isPending}
                className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {updateMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                {updateMut.isPending ? "Speichere…" : "Einstellung speichern"}
              </button>
            </div>
          </>
        )}
      </section>

      {/* --- DSGVO Art. 20 data export -------------------------------- */}
      <section className="mb-10 rounded-lg border bg-card p-6">
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-blue-100 p-2">
            <Info className="h-5 w-5 text-blue-700" />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-semibold">
              Meine pseudonymisierten Datensätze
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Diese Liste zeigt jeden anonymisierten Datensatz, der zu
              Ihrem pseudonymisierten Profil im System steht. Der{" "}
              <code className="rounded bg-muted px-1 font-mono text-xs">
                anonymous_user_id
              </code>{" "}
              ist die einzige Verbindung zu Ihrem Konto und nicht ohne
              den serverseitigen Salt zurückrechenbar.
            </p>
          </div>
        </div>
        <EventsTable events={eventsQ.data} loading={eventsQ.isLoading} />
      </section>

      <section className="rounded-lg border border-muted bg-muted/20 p-5 text-sm text-muted-foreground">
        <h3 className="mb-2 font-medium text-foreground">Was wir NICHT speichern</h3>
        <ul className="list-inside list-disc space-y-1">
          <li>Keine Namen, E-Mail-Adressen oder Telefonnummern</li>
          <li>Keine konkreten Adressen — nur Bundesland-Level (z.B. „AT-5" für Salzburg)</li>
          <li>Keine Projekt-, Datei- oder LV-Namen</li>
          <li>Keine Preise im Klartext — nur grobe Bereiche</li>
        </ul>
        <p className="mt-3">
          Mehr Details:{" "}
          <Link
            to="/datenschutz"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline"
          >
            Datenschutzerklärung
            <ExternalLink className="h-3 w-3" />
          </Link>
        </p>
      </section>
    </div>
  );
}

function EventsTable({
  events,
  loading,
}: {
  events: UserAnalyticsEvent[] | undefined;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Lade Datensätze…
      </div>
    );
  }
  if (!events || events.length === 0) {
    return (
      <p className="rounded-md border border-dashed bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
        Keine Datensätze. Wenn Analytics aktiviert ist, erscheinen
        hier zukünftig Ihre anonymisierten Ereignisse.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs">
        <thead className="bg-muted/50">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Zeitstempel</th>
            <th className="px-3 py-2 text-left font-medium">Ereignis</th>
            <th className="px-3 py-2 text-left font-medium">Region</th>
            <th className="px-3 py-2 text-left font-medium">Branche</th>
            <th className="px-3 py-2 text-left font-medium">Daten</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {events.map((e) => (
            <tr key={e.id}>
              <td className="whitespace-nowrap px-3 py-1.5 font-mono text-muted-foreground">
                {new Date(e.created_at).toLocaleString("de-AT")}
              </td>
              <td className="px-3 py-1.5 font-medium">{e.event_type}</td>
              <td className="px-3 py-1.5 text-muted-foreground">
                {e.region_code ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-muted-foreground">
                {e.industry_segment ?? "—"}
              </td>
              <td className="px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
                {e.event_data
                  ? JSON.stringify(e.event_data)
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length > 0 && (
        <div className="border-t bg-muted/20 px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
          anonymous_user_id: {events[0].anonymous_user_id}
        </div>
      )}
    </div>
  );
}

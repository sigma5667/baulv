/**
 * /app/admin/analytics — operator dashboard (v23.8).
 *
 * Aggregated metrics only; no per-row data leaks. Backend gates
 * with 403 for non-admins, but we render a sensible "not allowed"
 * page if a non-admin somehow reaches the URL — both frontend +
 * backend gate so the surface is unambiguous.
 *
 * The dashboard refetches on mount only. There's no auto-refresh
 * timer because the data updates slowly (daily-ish) and a
 * ticking timer eats DB cycles; the user can hit refresh if
 * they want fresher numbers.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  BarChart3,
  Users,
  FolderKanban,
  Calculator,
  Trophy,
  Loader2,
  Lock,
  RefreshCw,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { fetchAdminAnalytics } from "../api/auth";
import { INDUSTRY_LABELS, type IndustrySegment } from "../types/user";

export function AdminAnalyticsPage() {
  const { user } = useAuth();
  const dashQ = useQuery({
    queryKey: ["admin-analytics"],
    queryFn: fetchAdminAnalytics,
    // Frontend gate — same gate as backend, just here so the user
    // doesn't briefly see a 403 banner before the result resolves.
    enabled: !!user?.is_admin,
  });

  // Frontend gate. Returns the same DE message a 403 would render
  // so the experience is consistent whether the gate trips on the
  // client or the server side. ``user.is_admin`` may be stale on
  // first paint if /me is still loading; we only render the gate
  // when ``user`` is non-null (i.e., post-login).
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

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-6 w-6 text-primary" />
          <h1 className="text-2xl font-bold">Analytics-Dashboard</h1>
        </div>
        {dashQ.data && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <RefreshCw className="h-3 w-3" />
            Stand{" "}
            {new Date(dashQ.data.generated_at).toLocaleString("de-AT")}
          </span>
        )}
      </div>

      <p className="mb-6 max-w-3xl text-sm text-muted-foreground">
        Aggregierte Metriken über alle anonymisierten Datensätze hinweg.
        Es werden keine individuellen Datensätze angezeigt — alle Werte
        sind Summen oder Durchschnitte. DSGVO Art. 4 Nr. 5
        (Pseudonymisierung) gilt durchgehend.
      </p>

      {dashQ.isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Lade Dashboard…
        </div>
      )}

      {dashQ.isError && (
        <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Konnte das Dashboard nicht laden. Bitte erneut versuchen oder
          den Server-Log prüfen.
        </div>
      )}

      {dashQ.data && (
        <div className="space-y-8">
          {/* KPI cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi
              icon={Users}
              label="Aktive User (30 Tage)"
              value={dashQ.data.active_users_30d}
              hint="Eindeutige pseudonymisierte IDs mit ≥ 1 Event"
            />
            <Kpi
              icon={FolderKanban}
              label="Projekte gesamt"
              value={dashQ.data.projects_total}
            />
            <Kpi
              icon={FolderKanban}
              label="Neue Projekte (30 Tage)"
              value={dashQ.data.projects_last_30d}
            />
            <Kpi
              icon={Calculator}
              label="Ø Positionen pro LV"
              value={dashQ.data.avg_positions_per_lv.toFixed(1)}
              hint="Durchschnitt über alle bestehenden LVs"
            />
          </div>

          {/* Industry distribution */}
          <section className="rounded-lg border bg-card p-5">
            <h2 className="mb-3 text-lg font-semibold">
              Branchen-Verteilung
            </h2>
            {Object.keys(dashQ.data.industry_distribution).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Noch keine User mit ausgewählter Branche.
              </p>
            ) : (
              <div className="space-y-2">
                {Object.entries(dashQ.data.industry_distribution).map(
                  ([seg, count]) => {
                    const label =
                      INDUSTRY_LABELS[seg as IndustrySegment] ?? seg;
                    const total = Object.values(
                      dashQ.data.industry_distribution,
                    ).reduce((a, b) => a + b, 0);
                    const pct = total > 0 ? (count / total) * 100 : 0;
                    return (
                      <div key={seg}>
                        <div className="flex items-center justify-between text-sm">
                          <span>{label}</span>
                          <span className="font-mono text-xs text-muted-foreground">
                            {count} ({pct.toFixed(0)} %)
                          </span>
                        </div>
                        <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full bg-primary"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  },
                )}
              </div>
            )}
          </section>

          {/* Top templates */}
          <section className="rounded-lg border bg-card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Trophy className="h-5 w-5 text-amber-500" />
              <h2 className="text-lg font-semibold">
                Top 5 verwendete Vorlagen
              </h2>
            </div>
            {dashQ.data.top_templates.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Noch keine Vorlage wurde anonymisiert protokolliert.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-muted/30 text-xs uppercase tracking-wide">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">#</th>
                    <th className="px-3 py-2 text-left font-medium">
                      Template-ID
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      Verwendungen
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {dashQ.data.top_templates.map((row, idx) => (
                    <tr key={row.template_id}>
                      <td className="px-3 py-1.5 text-muted-foreground">
                        {idx + 1}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs">
                        {row.template_id}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono">
                        {row.use_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function Kpi({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof Users;
  label: string;
  value: number | string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary" />
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
      </div>
      <p className="text-3xl font-bold">{value}</p>
      {hint && (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  User as UserIcon,
  Save,
  Download,
  Trash2,
  KeyRound,
  ShieldAlert,
  ShieldCheck,
  Monitor,
  FileClock,
  Bot,
  LogOut,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import {
  updateProfile,
  changePassword,
  downloadMyDataExport,
  deleteMyAccount,
  updatePrivacySettings,
  listMySessions,
  revokeSession,
  revokeOtherSessions,
  fetchAuditLog,
} from "../api/auth";
import type { AuditLogEntry, UserSessionSummary } from "../types/user";

// Must match DELETE_CONFIRMATION_PHRASE in backend/app/api/auth.py.
const DELETE_PHRASE = "LÖSCHEN";

export function ProfilePage() {
  const { user, refreshUser, logout } = useAuth();
  const [form, setForm] = useState({
    full_name: user?.full_name ?? "",
    company_name: user?.company_name ?? "",
  });
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: () => updateProfile(form),
    onSuccess: () => {
      refreshUser();
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    },
  });

  const PLAN_LABELS: Record<string, string> = {
    basis: "Basis",
    pro: "Pro",
    enterprise: "Enterprise",
  };

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="mb-6 flex items-center gap-3">
        <UserIcon className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Profil</h1>
      </div>

      <div className="rounded-lg border bg-card p-6 space-y-6">
        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">E-Mail</label>
          <p className="text-sm">{user?.email}</p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">Aktueller Plan</label>
          <span className="inline-block rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary">
            {PLAN_LABELS[user?.subscription_plan ?? "basis"]}
          </span>
        </div>

        <hr />

        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          <div>
            <label className="mb-1 block text-sm font-medium">Vollständiger Name</label>
            <input
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Firmenname</label>
            <input
              value={form.company_name}
              onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          {success && (
            <div className="rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
              Profil erfolgreich aktualisiert.
            </div>
          )}
          {mutation.isError && (
            <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
              Fehler beim Speichern.
            </div>
          )}

          <button
            type="submit"
            disabled={mutation.isPending}
            className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <Save className="h-4 w-4" />
            {mutation.isPending ? "Speichern..." : "Speichern"}
          </button>
        </form>

        <hr />

        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">Mitglied seit</label>
          <p className="text-sm">
            {user?.created_at ? new Date(user.created_at).toLocaleDateString("de-AT") : "–"}
          </p>
        </div>
      </div>

      <PasswordChangeCard />
      <PrivacySettingsCard />
      <SessionsCard />
      <AuditLogCard />
      <DataExportCard />
      <DangerZoneCard onDeleted={logout} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password change
// ---------------------------------------------------------------------------

function PasswordChangeCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      setError(null);
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Fehler beim Ändern des Passworts.";
      setError(msg);
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError("Neues Passwort muss mindestens 8 Zeichen lang sein.");
      return;
    }
    if (next !== confirm) {
      setError("Passwörter stimmen nicht überein.");
      return;
    }
    mutation.mutate();
  };

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4 flex items-center gap-2">
        <KeyRound className="h-5 w-5 text-primary" />
        <h2 className="text-lg font-semibold">Passwort ändern</h2>
      </div>
      <form onSubmit={onSubmit} className="space-y-3">
        <input
          type="password"
          placeholder="Aktuelles Passwort"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
          className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <input
          type="password"
          placeholder="Neues Passwort (mind. 8 Zeichen)"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
          className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <input
          type="password"
          placeholder="Neues Passwort bestätigen"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
          className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        {error && (
          <div className="rounded-md bg-destructive/10 px-4 py-2 text-sm text-destructive">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-md bg-green-50 px-4 py-2 text-sm text-green-700">
            Passwort erfolgreich geändert.
          </div>
        )}
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {mutation.isPending ? "Speichern..." : "Passwort ändern"}
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Art. 20 DSGVO — Data export
// ---------------------------------------------------------------------------

function DataExportCard() {
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: downloadMyDataExport,
    onError: () => setError("Download fehlgeschlagen. Bitte erneut versuchen."),
    onSuccess: () => setError(null),
  });

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-2 flex items-center gap-2">
        <Download className="h-5 w-5 text-primary" />
        <h2 className="text-lg font-semibold">Meine Daten herunterladen</h2>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Laden Sie eine vollständige Kopie Ihrer gespeicherten Daten im
        JSON-Format herunter — Profildaten, Projekte, Pläne (Metadaten),
        Leistungsverzeichnisse, Berechnungen und Chat-Verläufe. Dies ist
        Ihr Recht auf Datenübertragbarkeit nach Art. 20 DSGVO.
      </p>
      {error && (
        <div className="mb-3 rounded-md bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </div>
      )}
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="flex items-center gap-2 rounded-md border border-primary px-4 py-2 text-sm font-medium text-primary hover:bg-primary/5 disabled:opacity-50"
      >
        <Download className="h-4 w-4" />
        {mutation.isPending ? "Export läuft..." : "Datenexport herunterladen"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Art. 17 DSGVO — Danger zone (account deletion)
// ---------------------------------------------------------------------------

function DangerZoneCard({ onDeleted }: { onDeleted: () => void }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => deleteMyAccount({ password, confirmation }),
    onSuccess: () => {
      // Account is gone; drop the token and bounce to login.
      onDeleted();
      window.location.href = "/";
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Konto konnte nicht gelöscht werden.";
      setError(msg);
    },
  });

  const canSubmit =
    confirmation === DELETE_PHRASE && password.length > 0 && !mutation.isPending;

  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-6">
      <div className="mb-2 flex items-center gap-2">
        <ShieldAlert className="h-5 w-5 text-destructive" />
        <h2 className="text-lg font-semibold text-destructive">
          Konto löschen
        </h2>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Nach Art. 17 DSGVO können Sie jederzeit die vollständige Löschung
        Ihres Kontos verlangen. Dabei werden unwiderruflich gelöscht: Ihr
        Profil, alle Projekte, hochgeladenen Baupläne, Leistungsverzeichnisse,
        Berechnungen und Chat-Verläufe. Ein aktives Abonnement wird
        automatisch gekündigt. <strong>Diese Aktion kann nicht rückgängig
        gemacht werden.</strong>
      </p>

      {!open ? (
        <button
          type="button"
          onClick={() => {
            setOpen(true);
            setError(null);
          }}
          className="flex items-center gap-2 rounded-md border border-destructive px-4 py-2 text-sm font-medium text-destructive hover:bg-destructive/10"
        >
          <Trash2 className="h-4 w-4" />
          Konto löschen
        </button>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-sm font-medium">Passwort</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-destructive focus:outline-none focus:ring-1 focus:ring-destructive"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">
              Zur Bestätigung &quot;{DELETE_PHRASE}&quot; eingeben
            </label>
            <input
              type="text"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
              required
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-destructive focus:outline-none focus:ring-1 focus:ring-destructive"
            />
          </div>
          {error && (
            <div className="rounded-md bg-destructive/10 px-4 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={!canSubmit}
              className="flex items-center gap-2 rounded-md bg-destructive px-4 py-2 text-sm font-medium text-white hover:bg-destructive/90 disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4" />
              {mutation.isPending ? "Löschen..." : "Endgültig löschen"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setPassword("");
                setConfirmation("");
                setError(null);
              }}
              className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
            >
              Abbrechen
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Privacy settings (marketing consent + AI processing disclosure)
// ---------------------------------------------------------------------------

function PrivacySettingsCard() {
  const { user, refreshUser } = useAuth();
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: (opt_in: boolean) =>
      updatePrivacySettings({ marketing_email_opt_in: opt_in }),
    onSuccess: () => {
      refreshUser();
      setSuccess(true);
      setTimeout(() => setSuccess(false), 2500);
    },
  });

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4 flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary" />
        <h2 className="text-lg font-semibold">Datenschutz-Einstellungen</h2>
      </div>

      {/* Marketing opt-in */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div className="flex-1">
          <p className="font-medium text-sm">Marketing-E-Mails</p>
          <p className="text-sm text-muted-foreground">
            Gelegentliche Produkt-Updates, neue Features und Hinweise zu
            ÖNORM-Änderungen. Keine Werbung Dritter. Sie können diese
            Einwilligung jederzeit widerrufen (Art. 7 Abs. 3 DSGVO).
          </p>
        </div>
        <label className="relative inline-flex cursor-pointer items-center">
          <input
            type="checkbox"
            checked={user?.marketing_email_opt_in ?? false}
            onChange={(e) => mutation.mutate(e.target.checked)}
            disabled={mutation.isPending}
            className="peer sr-only"
          />
          <div className="peer h-6 w-11 rounded-full bg-muted after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:bg-primary peer-checked:after:translate-x-full"></div>
        </label>
      </div>

      {success && (
        <div className="mb-4 rounded-md bg-green-50 px-4 py-2 text-sm text-green-700">
          Einstellung gespeichert.
        </div>
      )}

      <hr className="my-4" />

      {/* AI processing disclosure */}
      <div className="flex items-start gap-3 rounded-md bg-muted/50 p-4">
        <Bot className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="text-sm text-muted-foreground">
          <p className="mb-1 font-medium text-foreground">
            Hinweis zur KI-Verarbeitung
          </p>
          <p>
            BauLV nutzt die Claude API von Anthropic (PBC, San Francisco,
            USA) für die Analyse von Bauplänen, die Erstellung von
            LV-Positionstexten und den Chat-Assistenten. Dabei werden
            relevante Inhalte Ihrer Projekte an Anthropic übermittelt.
            Anthropic ist nach dem EU-US Data Privacy Framework
            zertifiziert und verwendet die Daten ausschließlich zur
            Erbringung der Dienstleistung — es erfolgt{" "}
            <strong>kein Training</strong> der Modelle auf Ihren Daten.
            Details finden Sie in der{" "}
            <a href="/datenschutz" className="underline hover:text-foreground">
              Datenschutzerklärung
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active sessions
// ---------------------------------------------------------------------------

function formatUserAgent(ua: string | null): string {
  if (!ua) return "Unbekanntes Gerät";
  // Cheap UA parsing — good enough to label "Chrome on Windows" etc.
  // Not a full UA parser; we just match the common names we care about.
  const browser =
    /Firefox\/\d/.test(ua)
      ? "Firefox"
      : /Edg\/\d/.test(ua)
        ? "Edge"
        : /Chrome\/\d/.test(ua)
          ? "Chrome"
          : /Safari\/\d/.test(ua)
            ? "Safari"
            : "Browser";
  const os =
    /Windows/.test(ua)
      ? "Windows"
      : /Mac OS X/.test(ua)
        ? "macOS"
        : /Android/.test(ua)
          ? "Android"
          : /iPhone|iPad/.test(ua)
            ? "iOS"
            : /Linux/.test(ua)
              ? "Linux"
              : "";
  return os ? `${browser} auf ${os}` : browser;
}

function SessionsCard() {
  const qc = useQueryClient();
  const { data: sessions, isLoading } = useQuery({
    queryKey: ["my-sessions"],
    queryFn: listMySessions,
  });

  const revokeOne = useMutation({
    mutationFn: revokeSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-sessions"] }),
  });

  const revokeAll = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-sessions"] }),
  });

  const active = (sessions ?? []).filter((s) => s.revoked_at === null);
  const hasOthers = active.some((s) => !s.is_current);

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Monitor className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">Aktive Sitzungen</h2>
        </div>
        {hasOthers && (
          <button
            type="button"
            onClick={() => revokeAll.mutate()}
            disabled={revokeAll.isPending}
            className="flex items-center gap-1.5 rounded-md border border-destructive px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            <LogOut className="h-3.5 w-3.5" />
            Alle anderen abmelden
          </button>
        )}
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Geräte und Browser, die derzeit bei Ihrem Konto angemeldet sind.
        Wenn Sie ein unbekanntes Gerät sehen, beenden Sie die Sitzung
        sofort und ändern Sie Ihr Passwort.
      </p>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Lade Sitzungen…</p>
      ) : (sessions ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">Keine Sitzungen gefunden.</p>
      ) : (
        <ul className="divide-y">
          {sessions!.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              onRevoke={() => revokeOne.mutate(s.id)}
              revoking={revokeOne.isPending}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function SessionRow({
  session,
  onRevoke,
  revoking,
}: {
  session: UserSessionSummary;
  onRevoke: () => void;
  revoking: boolean;
}) {
  const revoked = session.revoked_at !== null;
  const expired = new Date(session.expires_at) < new Date();
  const inactive = revoked || expired;

  return (
    <li className="flex items-center justify-between gap-4 py-3">
      <div className={inactive ? "opacity-60" : ""}>
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">
            {formatUserAgent(session.user_agent)}
          </p>
          {session.is_current && !inactive && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              Dieses Gerät
            </span>
          )}
          {revoked && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              Beendet
            </span>
          )}
          {expired && !revoked && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              Abgelaufen
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {session.ip_address ?? "unbekannte IP"} · Letzte Aktivität:{" "}
          {new Date(session.last_used_at).toLocaleString("de-AT")}
        </p>
      </div>
      {!inactive && !session.is_current && (
        <button
          type="button"
          onClick={onRevoke}
          disabled={revoking}
          className="rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
        >
          Beenden
        </button>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

const EVENT_LABELS: Record<string, string> = {
  "user.login": "Anmeldung",
  "user.login_failed": "Fehlgeschlagene Anmeldung",
  "user.register": "Registrierung",
  "user.password_changed": "Passwort geändert",
  "user.data_exported": "Datenexport",
  "user.account_deleted": "Konto gelöscht",
  "user.session_revoked": "Sitzung beendet",
  "user.sessions_revoked_all": "Alle anderen Sitzungen beendet",
  "user.privacy_updated": "Datenschutz-Einstellungen geändert",
};

function eventLabel(t: string): string {
  return EVENT_LABELS[t] ?? t;
}

function AuditLogCard() {
  const [expanded, setExpanded] = useState(false);
  const { data: entries, isLoading } = useQuery({
    queryKey: ["my-audit-log"],
    queryFn: () => fetchAuditLog(50),
    enabled: expanded,
  });

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileClock className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-semibold">Aktivitätsprotokoll</h2>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="rounded-md border px-3 py-1 text-xs font-medium hover:bg-accent"
        >
          {expanded ? "Ausblenden" : "Anzeigen"}
        </button>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Chronologisches Protokoll sicherheitsrelevanter Kontoaktionen
        (Anmeldungen, Passwortänderungen, Datenexporte, Löschungen). Die
        letzten 50 Einträge werden angezeigt — vollständiges Protokoll
        ist Teil Ihres Datenexports.
      </p>

      {expanded && (
        <>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Lade Protokoll…</p>
          ) : (entries ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Keine Einträge gefunden.
            </p>
          ) : (
            <ul className="divide-y">
              {entries!.map((e) => (
                <AuditRow key={e.id} entry={e} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditLogEntry }) {
  const isFail = entry.event_type === "user.login_failed";
  return (
    <li className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span
          className={`text-sm font-medium ${isFail ? "text-destructive" : ""}`}
        >
          {eventLabel(entry.event_type)}
        </span>
        <span className="text-xs text-muted-foreground">
          {new Date(entry.created_at).toLocaleString("de-AT")}
        </span>
      </div>
      <div className="text-xs text-muted-foreground">
        {entry.ip_address ?? "unbekannte IP"}
        {entry.user_agent ? ` · ${formatUserAgent(entry.user_agent)}` : ""}
      </div>
    </li>
  );
}

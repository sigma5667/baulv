import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  Plus,
  Trash2,
  Copy,
  CheckCircle2,
  AlertTriangle,
  Activity,
  Clock,
  ChevronLeft,
  ChevronRight,
  XCircle,
} from "lucide-react";
import {
  type ApiKey,
  type ApiKeyCreated,
  type McpAuditEntry,
  createApiKey,
  fetchApiKeyAudit,
  listApiKeys,
  revokeApiKey,
  updateApiKey,
} from "../api/apiKeys";

/**
 * Profile → API Keys.
 *
 * Three responsibilities:
 *
 *   1. Show the list of the user's PATs with a "last used vor X" badge
 *      so they can spot dormant keys at a glance.
 *   2. Allow create / revoke / change-expiry. The plaintext token is
 *      shown ONCE at creation time and never again.
 *   3. Surface the per-key MCP audit log with pagination, so the user
 *      can answer "what did Claude Desktop do last night".
 */
export function ApiKeysPage() {
  const qc = useQueryClient();
  const { data: keys, isLoading } = useQuery<ApiKey[]>({
    queryKey: ["api-keys"],
    queryFn: listApiKeys,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [justCreated, setJustCreated] = useState<ApiKeyCreated | null>(null);
  const [auditKeyId, setAuditKeyId] = useState<string | null>(null);

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <KeyRound className="h-6 w-6 text-primary" />
          <h1 className="text-2xl font-bold">API-Keys</h1>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Neuer Key
        </button>
      </div>

      <div className="rounded-md bg-muted/40 p-4 text-sm text-muted-foreground">
        API-Keys (auch <em>Personal Access Tokens</em>, PATs) erlauben es
        Agenten wie Claude Desktop, n8n oder eigenen Skripten, im Namen
        Ihres Accounts auf BauLV zuzugreifen — über das{" "}
        <code className="rounded bg-muted px-1 py-0.5 text-xs">/mcp</code>{" "}
        Endpoint. Behandeln Sie Keys wie Passwörter: niemals in
        Repos einchecken, niemals teilen, bei Verdacht sofort widerrufen.
      </div>

      {justCreated && (
        <NewlyCreatedBanner
          created={justCreated}
          onDismiss={() => setJustCreated(null)}
        />
      )}

      {createOpen && (
        <CreateKeyDialog
          onClose={() => setCreateOpen(false)}
          onCreated={(created) => {
            setJustCreated(created);
            setCreateOpen(false);
            qc.invalidateQueries({ queryKey: ["api-keys"] });
          }}
        />
      )}

      {auditKeyId && (
        <AuditDialog
          keyId={auditKeyId}
          keyName={keys?.find((k) => k.id === auditKeyId)?.name ?? "Key"}
          onClose={() => setAuditKeyId(null)}
        />
      )}

      <div className="rounded-lg border bg-card p-2">
        {isLoading && (
          <p className="p-4 text-sm text-muted-foreground">Lade...</p>
        )}
        {!isLoading && keys && keys.length === 0 && (
          <p className="p-6 text-center text-sm text-muted-foreground">
            Noch kein API-Key erstellt. Klicken Sie auf{" "}
            <span className="font-medium">Neuer Key</span>, um den ersten
            anzulegen.
          </p>
        )}
        {keys?.map((key) => (
          <KeyRow
            key={key.id}
            apiKey={key}
            onShowAudit={() => setAuditKeyId(key.id)}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One row in the keys list
// ---------------------------------------------------------------------------

function KeyRow({
  apiKey,
  onShowAudit,
}: {
  apiKey: ApiKey;
  onShowAudit: () => void;
}) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => revokeApiKey(apiKey.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const isRevoked = apiKey.revoked_at !== null;
  const isExpired =
    apiKey.expires_at !== null &&
    new Date(apiKey.expires_at).getTime() <= Date.now();
  const isActive = !isRevoked && !isExpired;

  return (
    <div
      className={`flex items-center justify-between gap-4 border-b p-4 last:border-b-0 ${
        !isActive ? "opacity-60" : ""
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-sm">{apiKey.name}</p>
          {isRevoked && (
            <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">
              widerrufen
            </span>
          )}
          {!isRevoked && isExpired && (
            <span className="rounded-full bg-orange-100 px-2 py-0.5 text-xs text-orange-700">
              abgelaufen
            </span>
          )}
        </div>
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {apiKey.key_prefix}…
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span title={apiKey.created_at}>
            Erstellt {formatRelative(apiKey.created_at)}
          </span>
          <span
            className={
              apiKey.last_used_at
                ? "text-foreground"
                : "italic text-muted-foreground"
            }
            title={apiKey.last_used_at ?? undefined}
          >
            {apiKey.last_used_at
              ? `Zuletzt verwendet ${formatRelative(apiKey.last_used_at)}`
              : "Nie verwendet"}
          </span>
          {apiKey.expires_at && !isExpired && (
            <span title={apiKey.expires_at}>
              Läuft ab {formatRelative(apiKey.expires_at)}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onShowAudit}
          className="flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent"
        >
          <Activity className="h-3 w-3" />
          Verlauf
        </button>
        {!isRevoked && (
          <button
            onClick={() => {
              if (confirm(`Key '${apiKey.name}' wirklich widerrufen?`)) {
                revoke.mutate();
              }
            }}
            disabled={revoke.isPending}
            className="flex items-center gap-1 rounded-md border border-destructive/40 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            <Trash2 className="h-3 w-3" />
            Widerrufen
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------

function CreateKeyDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (created: ApiKeyCreated) => void;
}) {
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState<"never" | "30" | "90" | "365">("never");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      createApiKey({
        name: name.trim(),
        expires_in_days: expiry === "never" ? null : Number(expiry),
      }),
    onSuccess: (created) => onCreated(created),
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail ?? "Konnte Key nicht erstellen.";
      setError(detail);
    },
  });

  return (
    <div className="rounded-lg border bg-card p-6 space-y-4">
      <h2 className="text-lg font-semibold">Neuer API-Key</h2>
      <div>
        <label className="mb-1 block text-sm font-medium">
          Bezeichnung
        </label>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="z.B. Claude Desktop"
          className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Frei wählbar. Hilft Ihnen später zu erkennen, welcher Agent
          welchen Key benutzt.
        </p>
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium">
          Gültigkeitsdauer
        </label>
        <select
          value={expiry}
          onChange={(e) =>
            setExpiry(e.target.value as "never" | "30" | "90" | "365")
          }
          className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="never">Unbegrenzt (Standard)</option>
          <option value="30">30 Tage</option>
          <option value="90">90 Tage</option>
          <option value="365">1 Jahr</option>
        </select>
        <p className="mt-1 text-xs text-muted-foreground">
          Nach Ablauf wird der Key automatisch ungültig — gut für
          temporäre Skripte oder Test-Setups.
        </p>
      </div>
      {error && (
        <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
        >
          Abbrechen
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={!name.trim() || create.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50"
        >
          {create.isPending ? "Erstelle..." : "Erstellen"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One-time plaintext display
// ---------------------------------------------------------------------------

function NewlyCreatedBanner({
  created,
  onDismiss,
}: {
  created: ApiKeyCreated;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(created.token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-lg border-2 border-amber-300 bg-amber-50 p-6 space-y-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
        <div className="flex-1 space-y-2">
          <p className="font-semibold text-amber-900">
            Token jetzt kopieren — nur einmal sichtbar!
          </p>
          <p className="text-sm text-amber-800">
            Aus Sicherheitsgründen speichert BauLV den Klartext nicht. Sobald
            Sie diese Box schließen, ist der Token nicht mehr abrufbar. Bei
            Verlust: alten Key widerrufen und neuen erstellen.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-md border bg-white p-3">
        <code className="flex-1 truncate font-mono text-sm">
          {created.token}
        </code>
        <button
          onClick={copy}
          className="flex shrink-0 items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90"
        >
          {copied ? (
            <>
              <CheckCircle2 className="h-3 w-3" />
              Kopiert!
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              Kopieren
            </>
          )}
        </button>
      </div>
      <button
        onClick={onDismiss}
        className="text-sm font-medium text-amber-900 underline hover:no-underline"
      >
        Ich habe den Token sicher gespeichert.
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit log dialog (pagination)
// ---------------------------------------------------------------------------

const AUDIT_PAGE_SIZE = 25;

function AuditDialog({
  keyId,
  keyName,
  onClose,
}: {
  keyId: string;
  keyName: string;
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0);

  const { data, isLoading } = useQuery({
    queryKey: ["api-key-audit", keyId, offset],
    queryFn: () =>
      fetchApiKeyAudit(keyId, { limit: AUDIT_PAGE_SIZE, offset }),
    // Keep stale data visible while paging — feels like instant
    // pagination instead of an empty flash on each click.
    placeholderData: (prev) => prev,
  });

  const total = data?.total ?? 0;
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + AUDIT_PAGE_SIZE, total);
  const canPrev = offset > 0;
  const canNext = offset + AUDIT_PAGE_SIZE < total;

  return (
    <div className="rounded-lg border bg-card p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Activity className="h-5 w-5 text-primary" />
          Verlauf: {keyName}
        </h2>
        <button
          onClick={onClose}
          className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          Schließen
        </button>
      </div>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Lade...</p>
      )}

      {!isLoading && total === 0 && (
        <p className="rounded-md bg-muted/40 p-6 text-center text-sm text-muted-foreground">
          Noch keine MCP-Aufrufe für diesen Key protokolliert.
        </p>
      )}

      {!isLoading && total > 0 && (
        <>
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Zeitpunkt</th>
                  <th className="px-3 py-2">Tool</th>
                  <th className="px-3 py-2">Ergebnis</th>
                  <th className="px-3 py-2 text-right">Dauer</th>
                </tr>
              </thead>
              <tbody>
                {data!.items.map((row) => (
                  <AuditRow key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {pageStart}–{pageEnd} von {total}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() =>
                  setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))
                }
                disabled={!canPrev}
                className="flex items-center gap-1 rounded-md border px-2 py-1 hover:bg-accent disabled:opacity-50"
              >
                <ChevronLeft className="h-3 w-3" />
                Zurück
              </button>
              <button
                onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}
                disabled={!canNext}
                className="flex items-center gap-1 rounded-md border px-2 py-1 hover:bg-accent disabled:opacity-50"
              >
                Weiter
                <ChevronRight className="h-3 w-3" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function AuditRow({ row }: { row: McpAuditEntry }) {
  const [open, setOpen] = useState(false);

  const resultBadge =
    row.result === "ok" ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700">
        <CheckCircle2 className="h-3 w-3" />
        OK
      </span>
    ) : row.result === "rate_limited" ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-xs text-orange-700">
        <Clock className="h-3 w-3" />
        Rate-Limit
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">
        <XCircle className="h-3 w-3" />
        Fehler
      </span>
    );

  return (
    <>
      <tr
        onClick={() => setOpen((v) => !v)}
        className="cursor-pointer border-t hover:bg-accent/50"
      >
        <td className="px-3 py-2 text-xs text-muted-foreground">
          {new Date(row.created_at).toLocaleString("de-DE")}
        </td>
        <td className="px-3 py-2 font-mono text-xs">{row.tool_name}</td>
        <td className="px-3 py-2">{resultBadge}</td>
        <td className="px-3 py-2 text-right text-xs text-muted-foreground">
          {row.latency_ms} ms
        </td>
      </tr>
      {open && (
        <tr className="border-t bg-muted/30">
          <td colSpan={4} className="px-3 py-3 text-xs">
            {row.error_message && (
              <div className="mb-2">
                <p className="font-medium text-destructive">
                  Fehlermeldung:
                </p>
                <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-card p-2 font-mono">
                  {row.error_message}
                </pre>
              </div>
            )}
            <div>
              <p className="font-medium">Argumente:</p>
              <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-card p-2 font-mono">
                {row.arguments
                  ? JSON.stringify(row.arguments, null, 2)
                  : "(keine)"}
              </pre>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Lightweight relative-time formatter — intl-friendly without pulling
 * in date-fns. Returns strings like "vor 3 Stunden", "in 2 Tagen".
 *
 * Threshold table is intentionally coarse — minute precision in a
 * key-management UI is overkill and would just add visual noise.
 */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffSec = Math.round((then - now) / 1000);
  const abs = Math.abs(diffSec);

  const rtf = new Intl.RelativeTimeFormat("de", { numeric: "auto" });

  if (abs < 60) return rtf.format(diffSec, "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 365)
    return rtf.format(Math.round(diffSec / (86400 * 30)), "month");
  return rtf.format(Math.round(diffSec / (86400 * 365)), "year");
}

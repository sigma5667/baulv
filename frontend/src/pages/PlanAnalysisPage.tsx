import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Upload,
  FileText,
  Loader2,
  CheckCircle,
  AlertCircle,
  ArrowLeft,
  Search,
  Sparkles,
  Clock,
  Lock,
  Info,
} from "lucide-react";
import { fetchPlans, uploadPlan, analyzePlan } from "../api/plans";
import { fetchProjectRooms, updateRoom, deleteRoom } from "../api/rooms";
import { useAuth } from "../hooks/useAuth";
import {
  normalizeError,
  isUpgradeRequired,
  type NormalizedError,
} from "../lib/errors";
import { pushDiagnostic } from "../lib/diagnostics";
import type { Plan } from "../types/plan";
import type { Room } from "../types/room";

// Kept in sync with backend config.max_plan_file_mb / max_plan_pages —
// we pre-validate on the client so obviously-wrong uploads don't
// consume backend resources and so the user gets feedback instantly.
const MAX_FILE_MB = 25;
const MAX_PAGES = 20;

// The exact German rejection copy for non-PDF uploads. Pinned here so
// the top banner, the inline row error, and the backend's 400 detail
// all say the same thing; any change here should mirror NOT_A_PDF in
// backend/app/api/plans.py.
const NOT_A_PDF_MSG =
  "Nur PDF-Dateien sind erlaubt. Bitte konvertieren Sie Ihr Bild in eine PDF oder verwenden Sie einen Bauplan im PDF-Format.";

/**
 * Return true iff ``file`` begins with the ``%PDF-`` magic bytes.
 *
 * The extension check is necessary but not sufficient — a user can
 * rename ``image.png`` to ``image.pdf`` and the browser will happily
 * hand us the bytes. Reading the file header is the authoritative
 * answer and matches what the backend does with ``file.file.read(8)``.
 */
async function isRealPdf(file: File): Promise<boolean> {
  try {
    const buf = await file.slice(0, 8).arrayBuffer();
    const bytes = new Uint8Array(buf);
    // "%PDF-" = 0x25 0x50 0x44 0x46 0x2D
    return (
      bytes.length >= 5 &&
      bytes[0] === 0x25 &&
      bytes[1] === 0x50 &&
      bytes[2] === 0x44 &&
      bytes[3] === 0x46 &&
      bytes[4] === 0x2d
    );
  } catch {
    // If we can't read the file (very large, permissions weirdness),
    // let the backend be the final judge rather than silently blocking.
    return true;
  }
}

export function PlanAnalysisPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const { hasFeature } = useAuth();
  const canAnalyze = hasFeature("ai_plan_analysis");

  const [uploadError, setUploadError] = useState<NormalizedError | null>(null);
  const [analyzeError, setAnalyzeError] = useState<NormalizedError | null>(
    null
  );
  // Map of ``planId -> last-error-message`` so a failed analysis stays
  // visible inline on the row even if the top banner is dismissed or
  // the user scrolls. This is the primary defense against "analyze
  // button appears to do nothing" — an error here is always rendered
  // next to the button that caused it.
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [analyzeSummary, setAnalyzeSummary] = useState<null | {
    pages_analyzed: number;
    rooms_extracted: number;
    page_errors: string[];
  }>(null);

  // Ref on the top-of-page alert region so we can scroll errors into
  // view the moment they appear. Users reported analyze failures
  // seeming to "do nothing" — one cause is the error rendering above
  // the fold; auto-scroll fixes it.
  const alertAnchorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (uploadError || analyzeError) {
      alertAnchorRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }, [uploadError, analyzeError]);

  const { data: plans = [] } = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => fetchPlans(projectId!),
    enabled: !!projectId,
  });

  const { data: rooms = [] } = useQuery({
    queryKey: ["rooms", projectId],
    queryFn: () => fetchProjectRooms(projectId!),
    enabled: !!projectId,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadPlan(projectId!, file),
    onSuccess: () => {
      setUploadError(null);
      queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
    },
    onError: (e) => setUploadError(normalizeError(e)),
  });

  const analyzeMutation = useMutation({
    mutationFn: (planId: string) => {
      // eslint-disable-next-line no-console
      console.info("[analyze] mutation starting", { planId });
      return analyzePlan(planId);
    },
    onSuccess: (result, planId) => {
      // eslint-disable-next-line no-console
      console.info("[analyze] success", { planId, result });
      try {
        setAnalyzeError(null);
        setRowErrors((prev) => {
          const next = { ...prev };
          delete next[planId];
          return next;
        });
        setAnalyzeSummary({
          pages_analyzed: result.pages_analyzed ?? 0,
          rooms_extracted: result.rooms_extracted ?? 0,
          page_errors: result.page_errors ?? [],
        });
        queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
        queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
      } catch (cbErr) {
        // If React state updates themselves throw (rare but possible
        // in production with stale closures or broken refs), surface
        // it via the diagnostic overlay so the user sees *something*.
        pushDiagnostic(
          "manual",
          "Fehler beim Verarbeiten des Analyse-Ergebnisses: " +
            (cbErr instanceof Error ? cbErr.message : String(cbErr)),
          cbErr instanceof Error ? cbErr.stack : undefined
        );
      }
    },
    onError: (e, planId) => {
      // eslint-disable-next-line no-console
      console.error("[analyze] error", { planId, error: e });
      try {
        const normalized = normalizeError(e);
        setAnalyzeError(normalized);
        setRowErrors((prev) => ({ ...prev, [planId]: normalized.message }));
        // Even on error, refresh plan status so it shows "failed" icon.
        queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
      } catch (cbErr) {
        // Last-ditch fallback — if our own error handler throws, push
        // to the overlay. If even THAT fails, log to the console.
        try {
          pushDiagnostic(
            "manual",
            "KI-Analyse fehlgeschlagen (Handler-Fehler): " +
              (cbErr instanceof Error ? cbErr.message : String(cbErr)),
            cbErr instanceof Error ? cbErr.stack : undefined
          );
        } catch {
          // eslint-disable-next-line no-console
          console.error("[analyze] onError handler itself failed", cbErr, e);
        }
      }
    },
  });

  const validateAndUpload = useCallback(
    async (files: File[]) => {
      setUploadError(null);
      for (const file of files) {
        // Fast extension check first (no async cost).
        if (!file.name.toLowerCase().endsWith(".pdf")) {
          setUploadError({ status: null, message: NOT_A_PDF_MSG });
          continue;
        }
        // Size check before reading the header (cheap; size is already
        // populated by the browser for any File).
        if (file.size > MAX_FILE_MB * 1024 * 1024) {
          const mb = Math.round(file.size / (1024 * 1024));
          setUploadError({
            status: null,
            message: `"${file.name}" ist ${mb} MB groß — maximal ${MAX_FILE_MB} MB pro Plan erlaubt.`,
          });
          continue;
        }
        // Magic-byte check: catches image.png renamed to image.pdf.
        const pdfOk = await isRealPdf(file);
        if (!pdfOk) {
          setUploadError({ status: null, message: NOT_A_PDF_MSG });
          continue;
        }
        uploadMutation.mutate(file);
      }
    },
    [uploadMutation]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      void validateAndUpload(Array.from(e.dataTransfer.files));
    },
    [validateAndUpload]
  );

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    void validateAndUpload(Array.from(e.target.files ?? []));
    // Reset so re-selecting the same file re-fires change.
    e.target.value = "";
  };

  const startAnalyze = (planId: string) => {
    // eslint-disable-next-line no-console
    console.info("[analyze] button clicked", {
      planId,
      canAnalyze,
      mutationPending: analyzeMutation.isPending,
    });
    setAnalyzeError(null);
    setAnalyzeSummary(null);
    setRowErrors((prev) => {
      const next = { ...prev };
      delete next[planId];
      return next;
    });
    analyzeMutation.mutate(planId);
  };

  return (
    <div className="p-6">
      <Link
        to={`/app/projects/${projectId}`}
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Zum Projekt
      </Link>

      <h1 className="mb-2 text-2xl font-bold">Plananalyse</h1>
      <p className="mb-6 text-sm text-muted-foreground">
        Laden Sie Ihre Grundriss-PDFs hoch und extrahieren Sie Räume,
        Flächen und Öffnungen automatisch per KI.
      </p>

      {/* Scroll anchor — any error banner below lives in this region
          and we scroll here when one appears. */}
      <div ref={alertAnchorRef} />

      {/* Upload area */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        className="mb-4 rounded-lg border-2 border-dashed border-border p-8 text-center transition-colors hover:border-primary/50"
      >
        <Upload className="mx-auto h-10 w-10 text-muted-foreground/50" />
        <p className="mt-2 text-sm text-muted-foreground">
          PDF-Baupläne hierher ziehen oder{" "}
          <label className="cursor-pointer text-primary hover:underline">
            Datei auswählen
            <input
              type="file"
              accept="application/pdf,.pdf"
              multiple
              onChange={handleFileSelect}
              className="hidden"
            />
          </label>
        </p>
        <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
          <Info className="h-3 w-3" />
          Nur PDF-Dateien (max. {MAX_FILE_MB} MB, max. {MAX_PAGES} Seiten)
        </p>
        {uploadMutation.isPending && (
          <div className="mt-3 flex items-center justify-center gap-2 text-sm text-primary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Wird hochgeladen...
          </div>
        )}
      </div>

      {uploadError && (
        <ErrorBanner
          title="Upload fehlgeschlagen"
          err={uploadError}
          onDismiss={() => setUploadError(null)}
        />
      )}

      {/* Analysis progress panel — only while analysis is running */}
      {analyzeMutation.isPending && <AnalysisProgress />}

      {analyzeError && (
        <AnalysisErrorBanner
          err={analyzeError}
          onDismiss={() => setAnalyzeError(null)}
        />
      )}

      {analyzeSummary && !analyzeMutation.isPending && (
        <div className="mb-6 flex items-start gap-3 rounded-md border border-green-200 bg-green-50 p-4">
          <CheckCircle className="mt-0.5 h-5 w-5 shrink-0 text-green-600" />
          <div className="flex-1 text-sm">
            <p className="font-medium text-green-900">
              Analyse abgeschlossen —{" "}
              {analyzeSummary.rooms_extracted} Räume aus{" "}
              {analyzeSummary.pages_analyzed}{" "}
              {analyzeSummary.pages_analyzed === 1 ? "Seite" : "Seiten"}{" "}
              extrahiert.
            </p>
            {analyzeSummary.page_errors.length > 0 && (
              <details className="mt-1 text-green-800">
                <summary className="cursor-pointer">
                  {analyzeSummary.page_errors.length} Seite
                  {analyzeSummary.page_errors.length === 1 ? "" : "n"} mit
                  Warnungen
                </summary>
                <ul className="mt-1 list-inside list-disc">
                  {analyzeSummary.page_errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </div>
      )}

      {/* Plans list */}
      {plans.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-3 text-lg font-semibold">Hochgeladene Pläne</h2>
          <div className="space-y-2">
            {plans.map((plan) => (
              <PlanRow
                key={plan.id}
                plan={plan}
                canAnalyze={canAnalyze}
                onAnalyze={() => startAnalyze(plan.id)}
                isAnalyzing={
                  analyzeMutation.isPending &&
                  analyzeMutation.variables === plan.id
                }
                rowError={rowErrors[plan.id] ?? null}
                onDismissRowError={() =>
                  setRowErrors((prev) => {
                    const next = { ...prev };
                    delete next[plan.id];
                    return next;
                  })
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* Extracted rooms table */}
      {rooms.length > 0 && (
        <div>
          <h2 className="mb-3 text-lg font-semibold">
            Extrahierte Räume ({rooms.length})
          </h2>
          <RoomTable rooms={rooms} projectId={projectId!} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error banners
// ---------------------------------------------------------------------------

function ErrorBanner({
  title,
  err,
  onDismiss,
}: {
  title: string;
  err: NormalizedError;
  onDismiss?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mb-6 flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4"
    >
      <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
      <div className="flex-1 text-sm">
        <p className="font-medium text-destructive">{title}</p>
        <p className="mt-0.5 text-destructive/90">{err.message}</p>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-destructive hover:underline"
        >
          Schließen
        </button>
      )}
    </div>
  );
}

/**
 * Analysis-specific error banner. Handles the 403 "upgrade required"
 * case with a dedicated CTA instead of just showing the raw message.
 */
function AnalysisErrorBanner({
  err,
  onDismiss,
}: {
  err: NormalizedError;
  onDismiss: () => void;
}) {
  if (isUpgradeRequired(err)) {
    return (
      <div
        role="alert"
        className="mb-6 flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-4"
      >
        <Lock className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
        <div className="flex-1 text-sm">
          <p className="font-medium text-amber-900">Upgrade erforderlich</p>
          <p className="mt-0.5 text-amber-800">{err.message}</p>
          <Link
            to="/app/subscription"
            className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Plan upgraden
          </Link>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-amber-700 hover:underline"
        >
          Schließen
        </button>
      </div>
    );
  }
  return (
    <div
      role="alert"
      className="mb-6 flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4"
    >
      <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
      <div className="flex-1 text-sm">
        <p className="font-medium text-destructive">
          KI-Analyse fehlgeschlagen
        </p>
        <p className="mt-0.5 text-destructive/90">{err.message}</p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-xs text-destructive hover:underline"
      >
        Schließen
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Analysis progress panel
// ---------------------------------------------------------------------------

const PHASES = [
  { label: "Plan wird analysiert", startAt: 0 },
  { label: "KI wertet Räume und Maße aus", startAt: 5 },
  { label: "Ergebnisse werden gespeichert", startAt: 45 },
] as const;

function AnalysisProgress() {
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef(Date.now());

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt.current) / 1000));
    }, 500);
    return () => clearInterval(id);
  }, []);

  const activeIdx = PHASES.reduce(
    (acc, p, i) => (elapsed >= p.startAt ? i : acc),
    0
  );

  return (
    <div
      role="status"
      className="mb-6 rounded-lg border border-primary/30 bg-primary/5 p-4"
    >
      <div className="flex items-center gap-2">
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
        <p className="font-medium text-primary">KI-Analyse läuft…</p>
        <span className="ml-auto flex items-center gap-1 text-xs text-primary/80">
          <Clock className="h-3.5 w-3.5" />
          {elapsed}s
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Das kann 30–60 Sekunden dauern. Bitte schließen Sie diese Seite
        nicht.
      </p>
      <ul className="mt-3 space-y-1.5 text-sm">
        {PHASES.map((phase, i) => {
          const done = i < activeIdx;
          const active = i === activeIdx;
          return (
            <li key={phase.label} className="flex items-center gap-2">
              {done ? (
                <CheckCircle className="h-4 w-4 text-green-600" />
              ) : active ? (
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
              ) : (
                <span className="h-2 w-2 rounded-full bg-muted-foreground/30" />
              )}
              <span
                className={
                  done
                    ? "text-muted-foreground line-through"
                    : active
                      ? "text-foreground"
                      : "text-muted-foreground"
                }
              >
                {phase.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plan row
// ---------------------------------------------------------------------------

function PlanRow({
  plan,
  canAnalyze,
  onAnalyze,
  isAnalyzing,
  rowError,
  onDismissRowError,
}: {
  plan: Plan;
  canAnalyze: boolean;
  onAnalyze: () => void;
  isAnalyzing: boolean;
  rowError: string | null;
  onDismissRowError: () => void;
}) {
  const statusIcon =
    {
      pending: <FileText className="h-4 w-4 text-muted-foreground" />,
      processing: <Loader2 className="h-4 w-4 animate-spin text-primary" />,
      completed: <CheckCircle className="h-4 w-4 text-green-600" />,
      failed: <AlertCircle className="h-4 w-4 text-destructive" />,
    }[plan.analysis_status] ?? <FileText className="h-4 w-4" />;

  // Before analysis, the status label should reflect the user's real
  // capability. Basis users can't trigger analysis — the action button
  // is already replaced by the upgrade pill — so "Bereit zur Analyse"
  // is misleading (implies one click is missing when an upgrade is).
  const pendingLabel = canAnalyze
    ? "Bereit zur Analyse"
    : "Analyse im Pro-Plan verfügbar";

  const statusLabel: Record<string, string> = {
    pending: pendingLabel,
    processing: "Analyse läuft",
    completed: "Analysiert",
    failed: "Analyse fehlgeschlagen",
  };

  // Page count is populated as a side-effect of analysis, so for basis
  // users the "? Seiten" placeholder never fills in — hide it. Pro
  // users see "? Seiten" as a hint that analysis will populate it.
  const pageCountText =
    plan.page_count != null
      ? `${plan.page_count} Seiten`
      : canAnalyze
        ? "? Seiten"
        : null;

  // Color the pending label so plan-gating is visible at a glance:
  // amber for basis (blocked), primary for pro (actionable).
  const pendingClass =
    plan.analysis_status === "pending"
      ? canAnalyze
        ? "text-primary"
        : "text-amber-700"
      : "";

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          {statusIcon}
          <div>
            <p className="text-sm font-medium">{plan.filename}</p>
            <p className="text-xs text-muted-foreground">
              {plan.plan_type ?? "Plan"}
              {pageCountText ? ` · ${pageCountText}` : ""}
              {" · "}
              <span className={pendingClass}>
                {statusLabel[plan.analysis_status] ?? plan.analysis_status}
              </span>
            </p>
          </div>
        </div>

        {(plan.analysis_status === "pending" ||
          plan.analysis_status === "failed") &&
          (canAnalyze ? (
            <button
              onClick={onAnalyze}
              disabled={isAnalyzing}
              className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {isAnalyzing ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Analysiere…
                </>
              ) : (
                <>
                  <Search className="h-3 w-3" />
                  {plan.analysis_status === "failed"
                    ? "Erneut analysieren"
                    : "AI-Analyse starten"}
                </>
              )}
            </button>
          ) : (
            <Link
              to="/app/subscription"
              className="flex items-center gap-1.5 rounded-md border border-amber-400 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100"
              title="KI-Plananalyse ist im Pro-Plan enthalten."
            >
              <Lock className="h-3 w-3" />
              Upgrade erforderlich
            </Link>
          ))}
      </div>

      {/* Inline error directly under the row that caused it. Stays
          visible even if the top banner is dismissed — the user always
          sees feedback next to the button they clicked. */}
      {rowError && !isAnalyzing && (
        <div
          role="alert"
          className="flex items-start gap-2 border-t border-destructive/20 bg-destructive/5 px-4 py-2.5 text-xs"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
          <p className="flex-1 text-destructive">{rowError}</p>
          <button
            type="button"
            onClick={onDismissRowError}
            className="text-destructive hover:underline"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Room table
// ---------------------------------------------------------------------------

function RoomTable({ rooms, projectId }: { rooms: Room[]; projectId: string }) {
  const queryClient = useQueryClient();
  const [_editingId, setEditingId] = useState<string | null>(null);

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _updateMutation = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<Room> }) =>
      updateRoom(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRoom,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rooms", projectId] }),
  });

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Raum</th>
            <th className="px-4 py-2 text-left font-medium">Typ</th>
            <th className="px-4 py-2 text-right font-medium">Fläche m²</th>
            <th className="px-4 py-2 text-right font-medium">Umfang m</th>
            <th className="px-4 py-2 text-right font-medium">RH m</th>
            <th className="px-4 py-2 text-left font-medium">Boden</th>
            <th className="px-4 py-2 text-center font-medium">Nassraum</th>
            <th className="px-4 py-2 text-center font-medium">Quelle</th>
            <th className="px-4 py-2 text-right font-medium">Konfidenz</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rooms.map((room) => (
            <tr key={room.id} className="hover:bg-muted/30">
              <td className="px-4 py-2 font-medium">{room.name}</td>
              <td className="px-4 py-2 text-muted-foreground">{room.room_type ?? "-"}</td>
              <td className="px-4 py-2 text-right font-mono">
                {room.area_m2?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-right font-mono">
                {room.perimeter_m?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-right font-mono">
                {room.height_m?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-muted-foreground">{room.floor_type ?? "-"}</td>
              <td className="px-4 py-2 text-center">
                {room.is_wet_room ? (
                  <span className="text-blue-600">Ja</span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </td>
              <td className="px-4 py-2 text-center">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    room.source === "ai"
                      ? "bg-purple-100 text-purple-700"
                      : "bg-gray-100 text-gray-700"
                  }`}
                >
                  {room.source === "ai" ? "AI" : "Manuell"}
                </span>
              </td>
              <td className="px-4 py-2 text-right font-mono text-xs">
                {room.ai_confidence ? `${(room.ai_confidence * 100).toFixed(0)}%` : "-"}
              </td>
              <td className="px-4 py-2 text-right">
                <button
                  onClick={() => deleteMutation.mutate(room.id)}
                  className="text-xs text-destructive hover:underline"
                >
                  Löschen
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

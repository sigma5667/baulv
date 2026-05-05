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
  Plus,
  Ruler,
  Calculator,
  Building2,
  Trash2,
} from "lucide-react";
import {
  fetchPlans,
  uploadPlan,
  analyzePlan,
  deletePlan,
  fetchPlanDeletionPreview,
  type PlanDeletionResult,
} from "../api/plans";
import {
  fetchProjectRooms,
  updateRoom,
  deleteRoom,
  createRoom,
  bulkCalculateWalls,
} from "../api/rooms";
import { useAuth } from "../hooks/useAuth";
import {
  normalizeError,
  isUpgradeRequired,
  type NormalizedError,
} from "../lib/errors";
import { InlineNumericEdit } from "../components/room/InlineNumericEdit";
import { perimeterAnnotation } from "../lib/roomHints";
import { pushDiagnostic } from "../lib/diagnostics";
import { useToast } from "../components/Toast";
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

  // Plan deletion state — when set, the confirmation dialog opens.
  // Cleared on close or after a successful delete. The dialog
  // component owns the preview-fetch and the actual delete mutation;
  // we just gate its visibility from here.
  const [deletingPlan, setDeletingPlan] = useState<Plan | null>(null);
  const [deleteResult, setDeleteResult] = useState<
    | (PlanDeletionResult & { filename: string })
    | null
  >(null);

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

  // v23.7 (Bug 1) — duplicate-upload guard. The drag-and-drop API can
  // emit multiple drop events for the same gesture on some browsers
  // (a fast double-drop, or a parent + nested DOM dropzone both
  // catching the same DataTransfer); without a lock, every event
  // fires a separate POST /plans and the file lands twice in the
  // list. ``useRef`` rather than state because a state-driven check
  // would race against the React batched update — by the time the
  // second event handler reads ``uploading``, the setState from the
  // first handler hasn't flushed yet, both pass the guard, both
  // upload. A ref's writes are synchronous so the second handler
  // sees the lock immediately. The lock releases on settled (success
  // or error) via the mutation's lifecycle.
  const uploadLockRef = useRef(false);

  const validateAndUpload = useCallback(
    async (files: File[]) => {
      // Fast bail-out: another upload is already in flight. Surfaces
      // a German hint so the user understands why the second drop
      // didn't take, but does NOT clear the existing upload state.
      if (uploadLockRef.current) {
        setUploadError({
          status: null,
          message:
            "Ein Upload läuft bereits. Bitte warten Sie, bis der erste " +
            "Plan hochgeladen ist, bevor Sie den nächsten anhängen.",
        });
        return;
      }
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
        // Lock. Released by the effect below when ``isPending`` flips
        // back to false (covers both success and error paths).
        uploadLockRef.current = true;
        uploadMutation.mutate(file);
      }
    },
    [uploadMutation]
  );

  // Release the upload-lock ref whenever the mutation settles. A
  // standalone effect (rather than wiring this into onSuccess /
  // onError) ensures the lock release runs after React has applied
  // the mutation state — preventing a "settled but lock still set"
  // window where the user couldn't drop a follow-up file.
  useEffect(() => {
    if (!uploadMutation.isPending && uploadLockRef.current) {
      uploadLockRef.current = false;
    }
  }, [uploadMutation.isPending]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      // Stop propagation as well — defence in depth against a parent
      // dropzone (if anything ever wraps PlanAnalysisPage) re-firing
      // the same drop. Without this we've seen browsers emit two
      // separate dropEvents for one gesture in tester logs.
      e.stopPropagation();
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

      {/* Upload area
          v23.7 (Bug 1): the dropzone visually grays out and stops
          accepting drops while an upload is in flight. The
          ``handleDrop`` handler still runs (so we can show the
          German "Upload läuft" error if the user insists), but the
          ``aria-disabled`` + ``cursor-not-allowed`` styling makes
          the state unmistakable. The file picker label is also
          disabled — clicking it during upload would otherwise
          re-fire the same gesture path. */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        aria-disabled={uploadMutation.isPending}
        className={`mb-4 rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
          uploadMutation.isPending
            ? "cursor-not-allowed border-primary/50 bg-primary/5"
            : "border-border hover:border-primary/50"
        }`}
      >
        <Upload
          className={`mx-auto h-10 w-10 ${
            uploadMutation.isPending
              ? "text-primary/40"
              : "text-muted-foreground/50"
          }`}
        />
        <p className="mt-2 text-sm text-muted-foreground">
          PDF-Baupläne hierher ziehen oder{" "}
          {uploadMutation.isPending ? (
            <span className="text-muted-foreground/50">Datei auswählen</span>
          ) : (
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
          )}
        </p>
        <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
          <Info className="h-3 w-3" />
          Nur PDF-Dateien (max. {MAX_FILE_MB} MB, max. {MAX_PAGES} Seiten)
        </p>
        {uploadMutation.isPending && (
          <div
            className="mt-3 flex items-center justify-center gap-2 text-sm font-medium text-primary"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            Upload läuft… bitte warten
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
                onDelete={() => setDeletingPlan(plan)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Empty-state: no rooms yet. Points the user to the manual
          structure editor as an alternative to the PDF-analysis path
          — a tester without a Bauplan-PDF has no other way to get
          rooms into the project. */}
      {rooms.length === 0 && !analyzeMutation.isPending && (
        <div className="mb-8 rounded-lg border border-dashed bg-muted/20 p-6">
          <div className="flex flex-wrap items-start gap-3">
            <div className="rounded-lg bg-blue-50 p-2.5">
              <Building2 className="h-5 w-5 text-blue-600" />
            </div>
            <div className="flex-1">
              <h3 className="font-medium">Noch keine Räume im Projekt</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Laden Sie oben einen Bauplan als PDF hoch und starten Sie die
                KI-Analyse, oder legen Sie die Gebäudestruktur manuell an.
              </p>
              <Link
                to={`/app/projects/${projectId}/structure`}
                className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-primary bg-card px-3 py-1.5 text-sm font-medium text-primary hover:bg-primary/5"
              >
                <Plus className="h-3.5 w-3.5" />
                Gebäudestruktur manuell anlegen
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Extracted rooms table */}
      {rooms.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-3 text-lg font-semibold">
            Extrahierte Räume ({rooms.length})
          </h2>
          <RoomTable rooms={rooms} projectId={projectId!} />
        </div>
      )}

      {/* Wall-area calculation — runs on the same rooms table but shows
          the numbers that will flow into paint/wallpaper/tiles/plaster
          LV positions. Separate section so the user can eyeball the
          gross/net values and confirm before the LV pulls them. */}
      {rooms.length > 0 && (
        <div>
          <h2 className="mb-1 text-lg font-semibold">Wandberechnung</h2>
          <p className="mb-3 text-sm text-muted-foreground">
            Automatische Berechnung nach österreichischen Baustandards:
            Treppenhaus-Aufschlag 1,5 ×, Höhenzuschläge für Räume über
            3 m (1,12 ×) bzw. über 4 m (1,16 ×). Öffnungen ab 2,5 m²
            werden abgezogen. Amber-markierte Zeilen brauchen eine
            bestätigte Raumhöhe.
          </p>
          <WallCalculationTable rooms={rooms} projectId={projectId!} />
        </div>
      )}

      {/* Plan deletion — confirmation dialog and post-delete toast.
          Dialog opens when ``deletingPlan`` is set (from the trash
          button on a PlanRow). On success we clear the dialog,
          surface a green banner with the precise counts, and
          invalidate the plans + rooms queries so the UI redraws
          with the row gone. */}
      {deletingPlan && (
        <PlanDeleteDialog
          plan={deletingPlan}
          onClose={() => setDeletingPlan(null)}
          onDeleted={(result) => {
            setDeleteResult({ ...result, filename: deletingPlan.filename });
            setDeletingPlan(null);
            queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
            queryClient.invalidateQueries({
              queryKey: ["rooms", projectId],
            });
            queryClient.invalidateQueries({
              queryKey: ["structure", projectId],
            });
          }}
        />
      )}

      {deleteResult && (
        <div
          role="status"
          className="fixed bottom-4 right-4 z-40 max-w-sm rounded-lg border border-green-300 bg-green-50 p-3 shadow-lg"
        >
          <div className="flex items-start gap-2">
            <CheckCircle className="mt-0.5 h-4 w-4 shrink-0 text-green-700" />
            <div className="flex-1 text-sm">
              <p className="font-medium text-green-900">
                Plan „{deleteResult.filename}" gelöscht.
              </p>
              {deleteResult.delete_rooms ? (
                <p className="mt-0.5 text-xs text-green-800">
                  {deleteResult.rooms_deleted} Raum/Räume,{" "}
                  {deleteResult.openings_deleted} Öffnung(en),{" "}
                  {deleteResult.proofs_deleted} Berechnungsnachweis(e)
                  mitgelöscht.
                </p>
              ) : (
                <p className="mt-0.5 text-xs text-green-800">
                  Verknüpfte Räume bleiben erhalten (ohne Plan-
                  Verbindung).
                </p>
              )}
              {!deleteResult.file_unlinked && (
                <p className="mt-0.5 text-xs text-amber-800">
                  Hinweis: PDF-Datei auf dem Server konnte nicht
                  entfernt werden — der Eintrag in der Datenbank ist
                  dennoch weg.
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => setDeleteResult(null)}
              className="text-xs text-green-900 hover:underline"
              aria-label="Hinweis schließen"
            >
              ×
            </button>
          </div>
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
  onDelete,
}: {
  plan: Plan;
  canAnalyze: boolean;
  onAnalyze: () => void;
  isAnalyzing: boolean;
  rowError: string | null;
  onDismissRowError: () => void;
  onDelete: () => void;
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

        <div className="flex items-center gap-2">
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

          {/* Delete button — always available, even while analysis
              is running (the user might want to abort a stalled
              upload). The confirmation dialog and preview-fetch
              live in PlanDeleteDialog. */}
          <button
            type="button"
            onClick={onDelete}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
            aria-label={`Plan ${plan.filename} löschen`}
            title="Plan löschen"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
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
// Plan deletion dialog
//
// Owns three things:
//   1. The pre-delete impact preview (``GET /plans/{id}/deletion-preview``).
//      Loaded as soon as the dialog opens so the user sees specific
//      numbers ("8 Räume verknüpft, davon 3 manuell überarbeitet").
//   2. The two action buttons. "Nur Plan löschen" → delete_rooms=false;
//      "Plan und alle Räume löschen" → delete_rooms=true. The second
//      is destructive-styled (red) so the user can't fat-finger it.
//   3. The actual ``DELETE``-mutation. On success we propagate the
//      result up via ``onDeleted`` so the parent can render the toast
//      and invalidate queries; on error we render the message inline.
//
// Modal pattern: fixed overlay, click-to-close on the backdrop, ESC
// keyboard close. We don't use a portal because the page only has one
// dialog active at a time and the z-index is high enough to clear
// every other absolutely-positioned widget.
// ---------------------------------------------------------------------------

function PlanDeleteDialog({
  plan,
  onClose,
  onDeleted,
}: {
  plan: Plan;
  onClose: () => void;
  onDeleted: (result: PlanDeletionResult) => void;
}) {
  const previewQuery = useQuery({
    queryKey: ["plan-deletion-preview", plan.id],
    queryFn: () => fetchPlanDeletionPreview(plan.id),
  });

  const deleteMut = useMutation({
    mutationFn: (deleteRooms: boolean) =>
      deletePlan(plan.id, { deleteRooms }),
    onSuccess: onDeleted,
  });

  const error = deleteMut.error ? normalizeError(deleteMut.error) : null;

  // ESC key closes the dialog.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !deleteMut.isPending) {
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, deleteMut.isPending]);

  const preview = previewQuery.data;
  const hasLinkedRooms = (preview?.rooms_linked ?? 0) > 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="plan-delete-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        // Close on backdrop click only — not when the user clicks
        // inside the dialog content.
        if (e.target === e.currentTarget && !deleteMut.isPending) {
          onClose();
        }
      }}
    >
      <div className="w-full max-w-md rounded-lg border bg-card p-5 shadow-xl">
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-destructive/10 p-2">
            <Trash2 className="h-5 w-5 text-destructive" />
          </div>
          <div className="flex-1">
            <h2 id="plan-delete-title" className="text-lg font-semibold">
              Plan löschen?
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">
                {plan.filename}
              </span>{" "}
              wird endgültig entfernt. Diese Aktion kann nicht
              rückgängig gemacht werden.
            </p>
          </div>
        </div>

        {/* Impact preview — loading, then the actual numbers. */}
        {previewQuery.isLoading && (
          <p className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Auswirkung wird geprüft…
          </p>
        )}

        {preview && hasLinkedRooms && (
          <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
            <p className="font-medium">
              {preview.rooms_linked === 1
                ? "1 Raum ist mit diesem Plan verknüpft"
                : `${preview.rooms_linked} Räume sind mit diesem Plan verknüpft`}
            </p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              {preview.rooms_manual_among_linked > 0 && (
                <li>
                  davon{" "}
                  <span className="font-medium">
                    {preview.rooms_manual_among_linked} manuell
                    überarbeitet
                  </span>{" "}
                  — diese Bearbeitungen gehen verloren
                </li>
              )}
              {preview.openings_linked > 0 && (
                <li>
                  {preview.openings_linked} Öffnung(en) (Fenster /
                  Türen)
                </li>
              )}
              {preview.proofs_linked > 0 && (
                <li>
                  {preview.proofs_linked} Berechnungsnachweis(e) in
                  LV-Positionen — die Mengen bleiben gecacht, aber
                  die Nachvollziehbarkeit der Berechnung geht
                  verloren
                </li>
              )}
            </ul>
          </div>
        )}

        {preview && !hasLinkedRooms && (
          <p className="mb-4 rounded-md border border-muted bg-muted/30 p-3 text-xs text-muted-foreground">
            Keine Räume sind mit diesem Plan verknüpft. Es wird nur
            die PDF-Datei und der Plan-Eintrag entfernt.
          </p>
        )}

        {error && (
          <div
            role="alert"
            className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive"
          >
            Löschen fehlgeschlagen: {error.message}
          </div>
        )}

        {/* Actions. Layout depends on whether there are linked rooms:
            with rooms we offer two distinct destructive actions
            (keep-rooms vs delete-everything); without we just need
            one "Löschen" + a Cancel. */}
        <div className="flex flex-col gap-2">
          {hasLinkedRooms && (
            <>
              <button
                type="button"
                onClick={() => deleteMut.mutate(false)}
                disabled={deleteMut.isPending}
                className="rounded-md border border-input bg-background px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
              >
                Nur Plan löschen — Räume bleiben erhalten
              </button>
              <button
                type="button"
                onClick={() => deleteMut.mutate(true)}
                disabled={deleteMut.isPending}
                className="rounded-md bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
              >
                Plan UND alle damit extrahierten Räume löschen
              </button>
            </>
          )}
          {preview && !hasLinkedRooms && (
            <button
              type="button"
              onClick={() => deleteMut.mutate(false)}
              disabled={deleteMut.isPending}
              className="rounded-md bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              {deleteMut.isPending ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Lösche…
                </span>
              ) : (
                "Plan löschen"
              )}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            disabled={deleteMut.isPending}
            className="rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-accent disabled:opacity-50"
          >
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Room table
// ---------------------------------------------------------------------------

// Parse a user-entered numeric field. Empty string maps to null (= "unset"
// on the backend, distinct from 0), localized decimal commas are accepted
// so "10,5" works for Austrian/German users.
function parseDecimal(s: string): number | null {
  if (s.trim() === "") return null;
  const n = parseFloat(s.replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

function RoomTable({ rooms, projectId }: { rooms: Room[]; projectId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  // v23.7 (Bug 3) — track which row's name is in inline-edit so the
  // span-or-input branch can decide what to render. ``null`` means
  // every name renders as a clickable read-only span.
  const [namingRowId, setNamingRowId] = useState<string | null>(null);
  // ``inlineSavingId`` tracks per-row the in-flight save spinner for
  // the InlineNumericEdit cells. Without per-row tracking, every
  // numeric cell's save spinner would fire whenever ANY row was
  // saving (because mutation state is shared).
  const [inlineSavingId, setInlineSavingId] = useState<string | null>(null);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });

  const updateMutation = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<Room> }) =>
      updateRoom(id, updates),
    onSuccess: (_data, variables) => {
      setEditingId(null);
      setNamingRowId(null);
      setInlineSavingId(null);
      invalidate();
      // v23.7 (Bug 3) — toast feedback consistent with v23.6 toast
      // system for position-edit. Surfaces the changed field so the
      // user gets a concrete confirmation instead of a generic
      // "saved". Falls back to a generic message for the bulk
      // RoomEditRow path where ``updates`` may carry several fields.
      const updates = variables.updates as Partial<Room>;
      const keys = Object.keys(updates);
      const labelMap: Record<string, string> = {
        name: "Raumname",
        area_m2: "Fläche",
        perimeter_m: "Umfang",
        height_m: "Raumhöhe",
        room_type: "Raumtyp",
        floor_type: "Bodenbelag",
        is_wet_room: "Nassraum-Flag",
      };
      const single =
        keys.length === 1 && labelMap[keys[0]] ? labelMap[keys[0]] : null;
      toast.success(
        single ? `${single} aktualisiert.` : "Raum gespeichert.",
      );
    },
    onError: (err) => {
      setInlineSavingId(null);
      toast.error(normalizeError(err).message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRoom,
    onSuccess: () => {
      invalidate();
      toast.success("Raum gelöscht.");
    },
    onError: (err) => toast.error(normalizeError(err).message),
  });

  const createMutation = useMutation({
    mutationFn: ({ unitId, data }: { unitId: string; data: Partial<Room> & { name: string } }) =>
      createRoom(unitId, data),
    onSuccess: () => {
      setShowAddForm(false);
      invalidate();
      toast.success("Raum hinzugefügt.");
    },
    onError: (err) => toast.error(normalizeError(err).message),
  });

  // Manual-add targets an existing Unit. We derive the candidate unit
  // from rooms the pipeline already created — we don't have a Unit API
  // on the frontend yet, and the AI pipeline auto-creates Building/
  // Floor/Unit from the first page of output. If there are zero rooms,
  // there is no unit to attach to and the Add button is disabled.
  const firstUnitId = rooms[0]?.unit_id ?? null;

  const createError = createMutation.error
    ? normalizeError(createMutation.error)
    : null;
  const updateError = updateMutation.error
    ? normalizeError(updateMutation.error)
    : null;

  return (
    <div>
      <div className="mb-3 flex items-center justify-end">
        <button
          type="button"
          onClick={() => setShowAddForm((v) => !v)}
          disabled={!firstUnitId}
          title={
            !firstUnitId
              ? "Zuerst eine KI-Analyse durchführen, damit eine Einheit existiert, zu der Räume hinzugefügt werden können."
              : undefined
          }
          className="flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Plus className="h-3 w-3" />
          Raum hinzufügen
        </button>
      </div>

      {showAddForm && firstUnitId && (
        <RoomAddForm
          onSubmit={(data) =>
            createMutation.mutate({ unitId: firstUnitId, data })
          }
          onCancel={() => setShowAddForm(false)}
          isPending={createMutation.isPending}
          error={createError}
        />
      )}

      {updateError && editingId === null && (
        <div
          role="alert"
          className="mb-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p className="flex-1">Speichern fehlgeschlagen: {updateError.message}</p>
        </div>
      )}

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
            {rooms.map((room) =>
              editingId === room.id ? (
                <RoomEditRow
                  key={room.id}
                  room={room}
                  onSave={(updates) =>
                    updateMutation.mutate({ id: room.id, updates })
                  }
                  onCancel={() => {
                    updateMutation.reset();
                    setEditingId(null);
                  }}
                  isPending={updateMutation.isPending}
                  error={updateError}
                />
              ) : (
                <tr key={room.id} className="hover:bg-muted/30">
                  {/* v23.7 (Bug 3) — clickable name. Switches to an
                      input on click, commits on Enter/blur, reverts
                      on Escape. Same UX semantics as the numeric
                      cells below so muscle memory carries across
                      columns. */}
                  <td className="px-4 py-2 font-medium">
                    {namingRowId === room.id ? (
                      <RoomNameInlineEdit
                        initialValue={room.name}
                        isSaving={
                          updateMutation.isPending &&
                          inlineSavingId === room.id
                        }
                        onCancel={() => setNamingRowId(null)}
                        onSave={(name) => {
                          if (name === room.name) {
                            setNamingRowId(null);
                            return;
                          }
                          setInlineSavingId(room.id);
                          updateMutation.mutate({
                            id: room.id,
                            updates: { name },
                          });
                        }}
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={() => setNamingRowId(room.id)}
                        title="Klicken zum Umbenennen"
                        className="group inline-flex items-center gap-1 rounded px-1 transition-colors hover:bg-accent"
                      >
                        <span>{room.name}</span>
                        <span className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-50">
                          ✎
                        </span>
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {room.room_type ?? "-"}
                  </td>
                  {/* v23.7 (Bug 3) — Fläche, Umfang, RH inline-editable
                      via the same ``InlineNumericEdit`` component used
                      by the wall-calc table below. Saves PUT
                      ``/rooms/{id}`` with just the changed field,
                      fires a toast on success/error via the shared
                      mutation. The 2 m² lower bound and "—" empty
                      state stay consistent with the read-only
                      rendering pre-v23.7. */}
                  <td className="px-4 py-2 text-right font-mono">
                    <InlineNumericEdit
                      value={room.area_m2 ?? null}
                      unit=""
                      state={room.area_m2 != null ? "ok" : "missing"}
                      missingLabel="Bitte eintragen"
                      warningLabel=""
                      tooltip="Raumfläche in m²"
                      isSaving={
                        updateMutation.isPending &&
                        inlineSavingId === room.id
                      }
                      digits={2}
                      ariaLabel={`Fläche von ${room.name}`}
                      onSave={(next) => {
                        setInlineSavingId(room.id);
                        updateMutation.mutate({
                          id: room.id,
                          updates: { area_m2: next ?? undefined },
                        });
                      }}
                    />
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    <InlineNumericEdit
                      value={room.perimeter_m ?? null}
                      unit=""
                      state={room.perimeter_m != null ? "ok" : "missing"}
                      missingLabel="Bitte eintragen"
                      warningLabel=""
                      tooltip="Raumumfang in m"
                      isSaving={
                        updateMutation.isPending &&
                        inlineSavingId === room.id
                      }
                      digits={2}
                      ariaLabel={`Umfang von ${room.name}`}
                      onSave={(next) => {
                        setInlineSavingId(room.id);
                        updateMutation.mutate({
                          id: room.id,
                          updates: { perimeter_m: next ?? undefined },
                        });
                      }}
                    />
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    <InlineNumericEdit
                      value={room.height_m ?? null}
                      unit=""
                      state={room.height_m != null ? "ok" : "missing"}
                      missingLabel="Bitte eintragen"
                      warningLabel=""
                      tooltip="Raumhöhe in m"
                      isSaving={
                        updateMutation.isPending &&
                        inlineSavingId === room.id
                      }
                      digits={2}
                      ariaLabel={`Raumhöhe von ${room.name}`}
                      onSave={(next) => {
                        setInlineSavingId(room.id);
                        updateMutation.mutate({
                          id: room.id,
                          updates: { height_m: next ?? undefined },
                        });
                      }}
                    />
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {room.floor_type ?? "-"}
                  </td>
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
                    {room.ai_confidence
                      ? `${(room.ai_confidence * 100).toFixed(0)}%`
                      : "-"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2 text-right">
                    {/* "Bearbeiten" stays for the multi-field
                        edit (room_type, floor_type, is_wet_room —
                        not yet inline-editable). Renamed to
                        "Mehr…" so it doesn't compete with the
                        click-to-edit affordance on individual
                        cells. */}
                    <button
                      type="button"
                      onClick={() => {
                        updateMutation.reset();
                        setEditingId(room.id);
                      }}
                      title="Alle Felder bearbeiten (Raumtyp, Bodenbelag, Nassraum)"
                      className="mr-3 text-xs text-primary hover:underline"
                    >
                      Mehr…
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (
                          confirm(
                            `Raum "${room.name}" wirklich löschen?`
                          )
                        ) {
                          deleteMutation.mutate(room.id);
                        }
                      }}
                      className="text-xs text-destructive hover:underline"
                    >
                      Löschen
                    </button>
                  </td>
                </tr>
              )
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Room edit / add
// ---------------------------------------------------------------------------

/**
 * Inline text-edit for the room name (v23.7 Bug 3).
 *
 * ``InlineNumericEdit`` is numbers-only; renaming a room needs a
 * plain text input with the same Enter / Escape / blur semantics so
 * the row's interaction model stays consistent column-to-column.
 *
 * Escape cancellation uses the same ``cancelRequestedRef`` guard as
 * ``InlineNumericEdit`` — without it the unmount-triggered blur
 * commits the draft the user just tried to discard. See the
 * "Escape semantics" docstring in InlineNumericEdit.
 *
 * Empty name is silently ignored (parent decides what counts as a
 * no-op). The parent normalises by trimming whitespace.
 */
function RoomNameInlineEdit({
  initialValue,
  isSaving,
  onSave,
  onCancel,
}: {
  initialValue: string;
  isSaving: boolean;
  onSave: (next: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initialValue);
  const cancelRequestedRef = useRef(false);

  const cancel = () => {
    cancelRequestedRef.current = true;
    onCancel();
  };

  const commit = () => {
    if (cancelRequestedRef.current) {
      cancelRequestedRef.current = false;
      return;
    }
    const trimmed = draft.trim();
    if (!trimmed) {
      // Empty input — treat as cancel rather than committing an
      // empty name (the backend would 400 anyway).
      onCancel();
      return;
    }
    onSave(trimmed);
  };

  return (
    <input
      type="text"
      autoFocus
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          cancel();
        }
      }}
      disabled={isSaving}
      aria-label="Raumname"
      className="w-full rounded border border-primary bg-background px-2 py-0.5 text-sm font-medium"
    />
  );
}

/**
 * Inline edit row. Holds a local draft so typing doesn't churn the
 * query cache, and only the fields we want to expose for correction
 * (name, type, area, perimeter, height, floor_type, wet-room) are
 * editable. Openings are out of scope for this form — a dedicated
 * openings editor is future work.
 */
function RoomEditRow({
  room,
  onSave,
  onCancel,
  isPending,
  error,
}: {
  room: Room;
  onSave: (updates: Partial<Room>) => void;
  onCancel: () => void;
  isPending: boolean;
  error: NormalizedError | null;
}) {
  const [draft, setDraft] = useState({
    name: room.name,
    room_type: room.room_type ?? "",
    area_m2: room.area_m2?.toString() ?? "",
    perimeter_m: room.perimeter_m?.toString() ?? "",
    height_m: room.height_m?.toString() ?? "",
    floor_type: room.floor_type ?? "",
    is_wet_room: room.is_wet_room,
  });

  const trimmedName = draft.name.trim();

  const handleSave = () => {
    if (!trimmedName) return;
    onSave({
      name: trimmedName,
      room_type: draft.room_type.trim() || null,
      area_m2: parseDecimal(draft.area_m2),
      perimeter_m: parseDecimal(draft.perimeter_m),
      height_m: parseDecimal(draft.height_m),
      floor_type: draft.floor_type.trim() || null,
      is_wet_room: draft.is_wet_room,
    });
  };

  return (
    <>
      <tr className="bg-primary/5">
        <td className="px-2 py-2">
          <input
            type="text"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="w-full rounded border bg-background px-2 py-1 text-sm"
            aria-label="Raum-Name"
          />
        </td>
        <td className="px-2 py-2">
          <input
            type="text"
            value={draft.room_type}
            onChange={(e) => setDraft({ ...draft, room_type: e.target.value })}
            className="w-full rounded border bg-background px-2 py-1 text-sm"
            aria-label="Raum-Typ"
          />
        </td>
        <td className="px-2 py-2">
          <input
            type="text"
            inputMode="decimal"
            value={draft.area_m2}
            onChange={(e) => setDraft({ ...draft, area_m2: e.target.value })}
            className="w-24 rounded border bg-background px-2 py-1 text-right font-mono text-sm"
            aria-label="Fläche in m²"
          />
        </td>
        <td className="px-2 py-2">
          <input
            type="text"
            inputMode="decimal"
            value={draft.perimeter_m}
            onChange={(e) =>
              setDraft({ ...draft, perimeter_m: e.target.value })
            }
            className="w-24 rounded border bg-background px-2 py-1 text-right font-mono text-sm"
            aria-label="Umfang in m"
          />
        </td>
        <td className="px-2 py-2">
          <input
            type="text"
            inputMode="decimal"
            value={draft.height_m}
            onChange={(e) => setDraft({ ...draft, height_m: e.target.value })}
            className="w-20 rounded border bg-background px-2 py-1 text-right font-mono text-sm"
            aria-label="Raumhöhe in m"
          />
        </td>
        <td className="px-2 py-2">
          <input
            type="text"
            value={draft.floor_type}
            onChange={(e) =>
              setDraft({ ...draft, floor_type: e.target.value })
            }
            className="w-full rounded border bg-background px-2 py-1 text-sm"
            aria-label="Bodentyp"
          />
        </td>
        <td className="px-2 py-2 text-center">
          <input
            type="checkbox"
            checked={draft.is_wet_room}
            onChange={(e) =>
              setDraft({ ...draft, is_wet_room: e.target.checked })
            }
            aria-label="Nassraum"
          />
        </td>
        <td className="px-2 py-2 text-center text-xs text-muted-foreground">
          —
        </td>
        <td className="px-2 py-2 text-right text-xs text-muted-foreground">
          —
        </td>
        <td className="whitespace-nowrap px-2 py-2 text-right">
          <button
            type="button"
            onClick={handleSave}
            disabled={isPending || !trimmedName}
            className="mr-3 text-xs text-primary hover:underline disabled:opacity-50"
          >
            {isPending ? "Speichert…" : "Speichern"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="text-xs text-muted-foreground hover:underline"
          >
            Abbrechen
          </button>
        </td>
      </tr>
      {error && (
        <tr className="bg-destructive/5">
          <td colSpan={10} className="px-4 py-2 text-xs text-destructive">
            Speichern fehlgeschlagen: {error.message}
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Inline "add room" form shown above the table. Only `name` is required
 * by the backend; optional numeric fields (area, height) are offered
 * up-front because they're the two values a user adding a missing
 * room is most likely to have from the plan.
 */
function RoomAddForm({
  onSubmit,
  onCancel,
  isPending,
  error,
}: {
  onSubmit: (data: Partial<Room> & { name: string }) => void;
  onCancel: () => void;
  isPending: boolean;
  error: NormalizedError | null;
}) {
  const [name, setName] = useState("");
  const [area, setArea] = useState("");
  const [height, setHeight] = useState("");

  const trimmedName = name.trim();

  const handleSubmit = () => {
    if (!trimmedName) return;
    onSubmit({
      name: trimmedName,
      area_m2: parseDecimal(area),
      height_m: parseDecimal(height),
    });
  };

  return (
    <div className="mb-3 rounded-lg border border-primary/30 bg-primary/5 p-3">
      <p className="mb-2 text-xs font-medium text-primary">
        Neuen Raum hinzufügen
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[140px] flex-1">
          <span className="block text-xs text-muted-foreground">Name *</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z.B. Wohnzimmer"
            className="w-full rounded border bg-background px-2 py-1 text-sm"
          />
        </label>
        <label className="w-28">
          <span className="block text-xs text-muted-foreground">
            Fläche m²
          </span>
          <input
            type="text"
            inputMode="decimal"
            value={area}
            onChange={(e) => setArea(e.target.value)}
            placeholder="z.B. 25,5"
            title="Wenn nur die Fläche bekannt ist, schätzt das System den Wandumfang automatisch (4·√A·1,10)."
            className="w-full rounded border bg-background px-2 py-1 text-right font-mono text-sm"
          />
        </label>
        <label className="w-32">
          <span className="block text-xs text-muted-foreground">RH m</span>
          <input
            type="text"
            inputMode="decimal"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            placeholder="2,50 (Standard)"
            title="Leer lassen verwendet 2,50 m (österreichischer Wohnbau-Standard)."
            className="w-full rounded border bg-background px-2 py-1 text-right font-mono text-sm"
          />
        </label>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isPending || !trimmedName}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isPending ? "Speichert…" : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={isPending}
          className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted"
        >
          Abbrechen
        </button>
      </div>
      {error && (
        <p className="mt-2 text-xs text-destructive">
          Anlegen fehlgeschlagen: {error.message}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Wall calculation table
// ---------------------------------------------------------------------------

/**
 * Format a number with a German decimal comma, 2 decimals. ``null``
 * becomes an em-dash so the column stays visually aligned.
 */
function fmt2(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toFixed(2).replace(".", ",");
}

function fmtFactor(f: number | null | undefined): string {
  if (f === null || f === undefined) return "—";
  // 1.000 → "1,00"; 1.120 → "1,12"; 1.160 → "1,16"; 1.500 → "1,50"
  return f.toFixed(2).replace(".", ",") + " ×";
}

/**
 * Short label for the ceiling-height provenance. Rendered as a badge.
 */
function ceilingSourceLabel(source: string): string {
  switch (source) {
    case "schnitt":
      return "Schnitt";
    case "grundriss":
      return "Grundriss";
    case "manual":
      return "Manuell";
    default:
      return "Standard";
  }
}

/**
 * "Wandberechnung" table. One row per room, with inline toggle for the
 * per-room deductions flag, a per-row "Neu berechnen" action (the
 * backend recomputes on every room/opening PUT anyway, this is for
 * when the user just wants to force a fresh run), and a bulk
 * "Wandflächen berechnen" button above the table.
 *
 * Amber highlight is the main UX signal — a row with
 * ``ceiling_height_source === "default"`` means the calculator fell
 * back to 2.50 m because nothing in the plan told us the height.
 * The user should confirm that height before any of these numbers
 * flow into an LV.
 */
function WallCalculationTable({
  rooms,
  projectId,
}: {
  rooms: Room[];
  projectId: string;
}) {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });

  const bulkMutation = useMutation({
    mutationFn: () => bulkCalculateWalls(projectId),
    onSuccess: invalidate,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<Room> }) =>
      updateRoom(id, updates),
    onSuccess: invalidate,
  });

  // Same wire shape as ``toggleMutation``, separate instance so that
  // an in-flight inline edit (perimeter / height) doesn't disable the
  // deduction toggles via ``isPending``, and vice versa. Cheap.
  const quickEditMut = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<Room> }) =>
      updateRoom(id, updates),
    onSuccess: invalidate,
  });

  const bulkError = bulkMutation.error ? normalizeError(bulkMutation.error) : null;

  // Totals row — handy for the estimator to sanity-check the whole
  // project at a glance. Sum over net because that's what flows into
  // the LV; nulls collapse to 0.
  const totalNet = rooms.reduce(
    (acc, r) => acc + (r.wall_area_net_m2 ?? 0),
    0
  );
  const totalGross = rooms.reduce(
    (acc, r) => acc + (r.wall_area_gross_m2 ?? 0),
    0
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-end gap-3">
        {/* The "X Räume ohne erkannte Raumhöhe" banner that used to
            live here was removed in v22: 2,50 m is the Austrian
            residential default and a soft fallback shouldn't read
            as a failure. The cell-level subtle hint on each row
            carries the same message without alarming the user. */}
        <button
          type="button"
          onClick={() => bulkMutation.mutate()}
          disabled={bulkMutation.isPending}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          title="Wandflächen für alle Räume neu berechnen"
        >
          {bulkMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Calculator className="h-3.5 w-3.5" />
          )}
          Wandflächen berechnen
        </button>
      </div>

      {bulkError && (
        <div
          role="alert"
          className="mb-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-2.5 text-xs text-destructive"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p className="flex-1">
            Berechnung fehlgeschlagen: {bulkError.message}
          </p>
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Raumname</th>
              <th className="px-3 py-2 text-right font-medium">
                Wandlänge m
              </th>
              <th className="px-3 py-2 text-right font-medium">
                Deckenhöhe m
              </th>
              <th className="px-3 py-2 text-left font-medium">Höhenquelle</th>
              <th className="px-3 py-2 text-right font-medium">Brutto m²</th>
              <th className="px-3 py-2 text-center font-medium">Abzüge</th>
              <th className="px-3 py-2 text-right font-medium">Netto m²</th>
              <th className="px-3 py-2 text-left font-medium">Raumtyp</th>
              <th className="px-3 py-2 text-right font-medium">Faktor</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rooms.map((room) => {
              const isDefault = room.ceiling_height_source === "default";
              const perimeterHint = perimeterAnnotation(room);
              return (
                <tr key={room.id} className="hover:bg-muted/30">
                  <td className="px-3 py-2 font-medium">
                    {room.name}
                    {room.is_staircase && (
                      <span
                        className="ml-2 inline-flex items-center gap-1 rounded-full bg-purple-100 px-2 py-0.5 text-xs text-purple-700"
                        title="Treppenhaus — 1,5 × Aufschlag"
                      >
                        <Ruler className="h-3 w-3" />
                        Treppenhaus
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {/* Wandlänge — annotation comes from
                        ``perimeterAnnotation`` (see lib/roomHints.ts).
                        Maps each ``perimeter_source`` to a hint badge
                        and tooltip that mirrors the v22.3 confidence
                        ladder. Red empty-state badge fires only when
                        perimeter_m is genuinely null. */}
                    <InlineNumericEdit
                      value={room.perimeter_m}
                      unit=""
                      state={room.perimeter_m === null ? "missing" : "ok"}
                      missingLabel="Bitte eintragen"
                      warningLabel=""
                      hint={perimeterHint.hint}
                      tooltip={perimeterHint.tooltip}
                      isSaving={quickEditMut.isPending}
                      onSave={(next) =>
                        quickEditMut.mutate({
                          id: room.id,
                          updates: { perimeter_m: next },
                        })
                      }
                      ariaLabel={`Wandlänge von ${room.name} bearbeiten`}
                    />
                  </td>
                  <td className="px-3 py-2 text-right">
                    {/* Deckenhöhe — three states.
                        - height_m IS NULL AND source != 'default'
                          → red "Bitte eintragen" (genuinely missing —
                          neither user nor calc has touched this row).
                        - height_m IS NULL AND source == 'default'
                          → defensive fallback display: show 2,50 m
                          with the same amber Info-icon hint we use
                          for the explicit-2,50 case. The backend
                          recalc writes the resolved height back, so
                          this branch shouldn't trigger after a
                          deploy of v22.2+, but leaving the
                          fallback in place keeps the table
                          coherent if any data inconsistency slips
                          through.
                        - height_m present (default or measured) →
                          vanilla value cell. Subtle hint when
                          source is 'default'. */}
                    <InlineNumericEdit
                      value={
                        room.height_m === null && isDefault
                          ? 2.5
                          : room.height_m
                      }
                      unit=""
                      state={
                        room.height_m === null && !isDefault
                          ? "missing"
                          : "ok"
                      }
                      missingLabel="Bitte eintragen"
                      warningLabel=""
                      hint={
                        isDefault
                          ? "Standardwert — prüfen falls anders"
                          : undefined
                      }
                      tooltip={
                        room.height_m === null && !isDefault
                          ? "Raumhöhe fehlt — bitte aus Plan oder Schnitt messen"
                          : isDefault
                            ? "Standardwert 2,50 m — bitte aus Schnittplan prüfen falls anders"
                            : "Deckenhöhe"
                      }
                      isSaving={quickEditMut.isPending}
                      onSave={(next) =>
                        quickEditMut.mutate({
                          id: room.id,
                          updates: { height_m: next },
                        })
                      }
                      ariaLabel={`Deckenhöhe von ${room.name} bearbeiten`}
                    />
                  </td>
                  <td className="px-3 py-2">
                    {isDefault ? (
                      // Höhenquelle: passive grey "Standard" pill.
                      // The actual edit affordance lives on the
                      // Deckenhöhe value cell — having it twice in a
                      // row was redundant once 2,50 m stopped being
                      // an alarm condition.
                      <span
                        className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700"
                        title="Standardwert 2,50 m — die Deckenhöhe-Zelle ist editierbar"
                      >
                        Standard
                      </span>
                    ) : (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                        {ceilingSourceLabel(room.ceiling_height_source)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {fmt2(room.wall_area_gross_m2)}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <label
                      className="inline-flex cursor-pointer items-center gap-1.5 text-xs"
                      title="Öffnungen ab 2,5 m² vom Netto abziehen"
                    >
                      <input
                        type="checkbox"
                        checked={room.deductions_enabled}
                        disabled={toggleMutation.isPending}
                        onChange={(e) =>
                          toggleMutation.mutate({
                            id: room.id,
                            updates: {
                              deductions_enabled: e.target.checked,
                            },
                          })
                        }
                        aria-label={`Öffnungsabzüge für ${room.name}`}
                      />
                      <span className="text-muted-foreground">
                        {room.deductions_enabled ? "aktiv" : "aus"}
                      </span>
                    </label>
                  </td>
                  <td className="px-3 py-2 text-right font-mono font-medium">
                    {fmt2(room.wall_area_net_m2)}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {room.room_type ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {fmtFactor(room.applied_factor)}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot className="bg-muted/30">
            <tr className="font-medium">
              <td className="px-3 py-2" colSpan={4}>
                Summe über {rooms.length}{" "}
                {rooms.length === 1 ? "Raum" : "Räume"}
              </td>
              <td className="px-3 py-2 text-right font-mono">
                {fmt2(totalGross)}
              </td>
              <td className="px-3 py-2" />
              <td className="px-3 py-2 text-right font-mono">
                {fmt2(totalNet)}
              </td>
              <td className="px-3 py-2" colSpan={2} />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

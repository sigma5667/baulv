import api from "./client";
import type { Plan, PlanType } from "../types/plan";

export const fetchPlans = async (projectId: string): Promise<Plan[]> => {
  const { data } = await api.get(`/plans/projects/${projectId}/plans`);
  return data;
};

/**
 * v24.1 — ``planType`` is now typed against the PlanType union.
 * Default stays ``"grundriss"`` so existing callers without the
 * argument keep their previous behaviour. The backend rejects any
 * other string with a 400 (whitelist guard); the frontend toggle
 * is constrained to the same three values, so this should never
 * round-trip an invalid value in practice.
 */
export const uploadPlan = async (
  projectId: string,
  file: File,
  planType: PlanType = "grundriss",
): Promise<Plan> => {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post(
    `/plans/projects/${projectId}/plans?plan_type=${planType}`,
    formData,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return data;
};

export interface AnalyzePlanResult {
  plan_id: string;
  pages_analyzed: number;
  rooms_extracted: number;
  /** Per-page errors that didn't fail the whole run (e.g. one bad page). */
  page_errors: string[];
  /**
   * v24.2 — Schnitt-only fields. Populated when the analysed plan
   * was a Schnitt-Plan; the backend pipeline writes height_m onto
   * matching project rooms and reports the X-of-Y count back so the
   * frontend can show a precise toast.
   *
   * For Grundriss runs these stay undefined; the toast renderer
   * falls back to the Grundriss copy when ``heights_matched`` is
   * absent.
   */
  heights_extracted?: number;
  heights_matched?: number;
  rooms_in_project?: number;
}

/** 2026-11 — Status eines Analyse-Hintergrund-Jobs
 * (``GET /plans/{id}/analysis``). */
export interface AnalysisJob {
  job_id: string | null;
  plan_id: string;
  status: "running" | "completed" | "failed";
  pages_done: number;
  pages_total: number | null;
  result: AnalyzePlanResult | null;
  error_detail: string | null;
  error_status: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AnalysisProgressInfo {
  pagesDone: number;
  pagesTotal: number | null;
}

/** Fehler eines fertig gelaufenen, aber fehlgeschlagenen Jobs.
 * ``normalizeError`` nimmt ``message`` direkt (deutsche Server-Meldung). */
export class AnalysisFailedError extends Error {
  status: number | null;
  constructor(message: string, status: number | null) {
    super(message);
    this.name = "AnalysisFailedError";
    this.status = status;
  }
}

export const fetchAnalysisStatus = async (planId: string): Promise<AnalysisJob> => {
  const { data } = await api.get(`/plans/${planId}/analysis`);
  return data;
};

const POLL_INTERVAL_MS = 2000;
// Kurze Netzwerk-Aussetzer beim Abfragen tolerieren (Cloudflare,
// Deploy-Wechsel), statt die laufende Analyse als Fehler zu melden.
const MAX_POLL_FAILURES = 15;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Fragt den Job-Status ab, bis er fertig oder fehlgeschlagen ist. */
export const waitForAnalysis = async (
  planId: string,
  onProgress?: (p: AnalysisProgressInfo) => void,
): Promise<AnalyzePlanResult> => {
  let failures = 0;
  for (;;) {
    let job: AnalysisJob;
    try {
      job = await fetchAnalysisStatus(planId);
      failures = 0;
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      // 401/403/404 sind endgültig — nicht endlos weiterfragen.
      if (status === 401 || status === 403 || status === 404) throw e;
      failures += 1;
      if (failures > MAX_POLL_FAILURES) throw e;
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    onProgress?.({ pagesDone: job.pages_done, pagesTotal: job.pages_total });
    if (job.status === "completed" && job.result) return job.result;
    if (job.status === "failed") {
      throw new AnalysisFailedError(
        job.error_detail ||
          "Die KI-Analyse ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
        job.error_status,
      );
    }
    await sleep(POLL_INTERVAL_MS);
  }
};

/** Startet die Analyse als Hintergrund-Job (202) und wartet per
 * Polling auf das Ergebnis. Läuft schon eine (409), wird diese
 * übernommen statt einen Fehler zu zeigen. */
export const analyzePlan = async (
  planId: string,
  onProgress?: (p: AnalysisProgressInfo) => void,
): Promise<AnalyzePlanResult> => {
  try {
    await api.post(`/plans/${planId}/analyze`);
  } catch (e) {
    const status = (e as { response?: { status?: number } })?.response?.status;
    if (status !== 409) throw e;
  }
  return waitForAnalysis(planId, onProgress);
};

/** Pre-delete impact summary — how many rooms/openings/proofs are
 * tied to this plan. Backend computes once via SQL aggregates so the
 * confirmation dialog can render specific copy ("8 Räume verknüpft,
 * davon 3 manuell überarbeitet") instead of generic "Sicher?". */
export interface PlanDeletionPreview {
  plan_id: string;
  filename: string;
  rooms_linked: number;
  rooms_manual_among_linked: number;
  openings_linked: number;
  proofs_linked: number;
}

export const fetchPlanDeletionPreview = async (
  planId: string
): Promise<PlanDeletionPreview> => {
  const { data } = await api.get(`/plans/${planId}/deletion-preview`);
  return data;
};

/** Result of an actual plan delete — same counts as the preview
 * minus filename, plus the disk-unlink outcome. The frontend uses
 * this to show a precise "X Räume und Y Berechnungsnachweise
 * gelöscht" toast after the operation. */
export interface PlanDeletionResult {
  plan_id: string;
  delete_rooms: boolean;
  rooms_deleted: number;
  openings_deleted: number;
  proofs_deleted: number;
  file_unlinked: boolean;
}

export const deletePlan = async (
  planId: string,
  options: { deleteRooms: boolean }
): Promise<PlanDeletionResult> => {
  const { data } = await api.delete(
    `/plans/${planId}?delete_rooms=${options.deleteRooms}`
  );
  return data;
};

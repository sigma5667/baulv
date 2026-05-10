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

export const analyzePlan = async (planId: string): Promise<AnalyzePlanResult> => {
  const { data } = await api.post(`/plans/${planId}/analyze`);
  return data;
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

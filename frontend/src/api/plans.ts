import api from "./client";
import type { Plan } from "../types/plan";

export const fetchPlans = async (projectId: string): Promise<Plan[]> => {
  const { data } = await api.get(`/plans/projects/${projectId}/plans`);
  return data;
};

export const uploadPlan = async (projectId: string, file: File, planType = "grundriss"): Promise<Plan> => {
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
}

export const analyzePlan = async (planId: string): Promise<AnalyzePlanResult> => {
  const { data } = await api.post(`/plans/${planId}/analyze`);
  return data;
};

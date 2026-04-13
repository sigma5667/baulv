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

export const analyzePlan = async (planId: string): Promise<{ rooms_extracted: number }> => {
  const { data } = await api.post(`/plans/${planId}/analyze`);
  return data;
};

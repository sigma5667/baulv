import api from "./client";
import type {
  LVFromTemplateRequest,
  LVFromTemplateResponse,
  TemplateCreateFromLV,
  TemplateDetail,
  TemplateSummary,
} from "../types/template";

/**
 * List all templates visible to the current user (system + their own).
 * Optional filter params — both match exactly on the backend.
 */
export const fetchTemplates = async (filters?: {
  category?: string;
  gewerk?: string;
}): Promise<TemplateSummary[]> => {
  const { data } = await api.get("/templates", { params: filters });
  return data;
};

export const fetchTemplate = async (id: string): Promise<TemplateDetail> => {
  const { data } = await api.get(`/templates/${id}`);
  return data;
};

/**
 * Save an existing LV as a user template. The backend strips prices
 * and quantities — templates are price- and quantity-agnostic.
 */
export const createTemplateFromLV = async (
  payload: TemplateCreateFromLV
): Promise<TemplateSummary> => {
  const { data } = await api.post("/templates", payload);
  return data;
};

export const deleteTemplate = async (id: string): Promise<void> => {
  await api.delete(`/templates/${id}`);
};

/**
 * Spawn a new LV in a project from a template. Returns the new LV's id
 * so the caller can navigate straight to it.
 */
export const createLVFromTemplate = async (
  payload: LVFromTemplateRequest
): Promise<LVFromTemplateResponse> => {
  const { data } = await api.post("/lv/from-template", payload);
  return data;
};

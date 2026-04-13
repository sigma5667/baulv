import api from "./client";
import type { LV, LVCreate, ONormSelectionItem } from "../types/lv";

export const fetchProjectLVs = async (projectId: string): Promise<LV[]> => {
  const { data } = await api.get(`/lv/projects/${projectId}/lv`);
  return data;
};

export const fetchLV = async (lvId: string): Promise<LV> => {
  const { data } = await api.get(`/lv/${lvId}`);
  return data;
};

export const createLV = async (projectId: string, lv: LVCreate): Promise<LV> => {
  const { data } = await api.post(`/lv/projects/${projectId}/lv`, lv);
  return data;
};

export const calculateLV = async (lvId: string): Promise<{ positions_calculated: number }> => {
  const { data } = await api.post(`/lv/${lvId}/calculate`);
  return data;
};

export const generateTexts = async (lvId: string): Promise<{ positions_updated: number }> => {
  const { data } = await api.post(`/lv/${lvId}/generate-texts`);
  return data;
};

export const updatePosition = async (
  positionId: string,
  updates: { kurztext?: string; langtext?: string; einheitspreis?: number; is_locked?: boolean }
): Promise<void> => {
  await api.put(`/lv/positionen/${positionId}`, updates);
};

export const exportLV = async (lvId: string, format = "xlsx"): Promise<Blob> => {
  const { data } = await api.post(`/lv/${lvId}/export?format=${format}`, null, {
    responseType: "blob",
  });
  return data;
};

export const updateONormSelection = async (
  lvId: string,
  onormDokumentIds: string[]
): Promise<ONormSelectionItem[]> => {
  const { data } = await api.put(`/lv/${lvId}/onorm-selection`, {
    onorm_dokument_ids: onormDokumentIds,
  });
  return data;
};

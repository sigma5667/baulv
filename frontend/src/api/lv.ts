import api from "./client";
import type { LV, LVCreate } from "../types/lv";

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

export interface WallAreaSyncResult {
  lv_id: string;
  total_wall_area_m2: number;
  positions_updated: number;
  positions_skipped_locked: number;
  rooms_considered: number;
}

/**
 * Copy the project's net wall area into every m² position whose text
 * looks like wall work (Wand / Tapete / Anstrich / Fliesen / Putz).
 * Locked positions are skipped. Returns the aggregate so the UI can
 * show a confirmation toast ("X Positionen aktualisiert, Summe Y m²").
 */
export const syncWallAreas = async (lvId: string): Promise<WallAreaSyncResult> => {
  const { data } = await api.post(`/lv/${lvId}/sync-wall-areas`);
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

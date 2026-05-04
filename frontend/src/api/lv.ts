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
  // New in v15 — the backend now also routes ceiling positions
  // (Decke / Deckenanstrich / Decken-) to floor_area_m2. These fields
  // are optional on the type so older deployed backends (pre-v15) that
  // don't emit them still type-check — the toast falls back gracefully
  // if they're missing.
  total_ceiling_area_m2?: number;
  wall_positions_updated?: number;
  ceiling_positions_updated?: number;
  positions_updated: number; // total across both kinds
  positions_skipped_locked: number;
  rooms_considered: number;
}

/**
 * Copy the project's net wall area into every m² position whose text
 * looks like wall work (Wand / Tapete / Anstrich / Fliesen / Putz —
 * but NOT Decke), and the project's total floor area into every m²
 * position whose text looks like ceiling work (Decke / Deckenanstrich).
 * Locked positions are skipped. Returns the aggregate so the UI can
 * show a confirmation toast.
 */
export const syncWallAreas = async (lvId: string): Promise<WallAreaSyncResult> => {
  const { data } = await api.post(`/lv/${lvId}/sync-wall-areas`);
  return data;
};

/**
 * Partial-update a single LV position.
 *
 * v23.5 added ``menge`` to the accepted fields so the inline-edit
 * surface in ``LVEditorPage`` can override calculated quantities.
 * The backend gates locked positions: a row with ``is_locked=true``
 * rejects every field except the lock flag itself with a 409 — the
 * caller is expected to surface that as "Position gesperrt — bitte
 * erst entsperren" rather than silently dropping the change.
 *
 * Returns the updated position so callers can update React Query
 * cache directly (avoid the round-trip refetch when an explicit
 * write succeeded).
 */
export const updatePosition = async (
  positionId: string,
  updates: {
    kurztext?: string;
    langtext?: string;
    menge?: number;
    einheitspreis?: number;
    is_locked?: boolean;
  }
): Promise<void> => {
  await api.put(`/lv/positionen/${positionId}`, updates);
};

/**
 * Export the LV as xlsx or pdf, with automatic retry on transient
 * gateway errors.
 *
 * Why retry: reportlab's first import on a cold Railway dyno can take
 * 15-40s, which blows past Railway's edge-proxy timeout and surfaces
 * to the browser as a 502 Bad Gateway. We also pre-warm reportlab at
 * backend startup (see main.py), but on the first few seconds after
 * a deploy the warm-up may not have completed yet. Similarly, the
 * backend wraps the export in ``asyncio.wait_for`` and returns a
 * well-formed 503 + Retry-After instead of a 502 when it does its
 * own timeout dance. Both 502 and 503 are retry-able; 404, 400, 403
 * are not (the request itself is bad).
 *
 * We retry up to 2 times with 2s linear backoff. That caps total wait
 * at ~5s which is inside the UX patience window; beyond that the user
 * sees the error and can manually retry.
 */
const EXPORT_RETRYABLE_STATUSES = new Set([502, 503, 504]);
const EXPORT_MAX_RETRIES = 2;
const EXPORT_RETRY_DELAY_MS = 2000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const exportLV = async (lvId: string, format = "xlsx"): Promise<Blob> => {
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= EXPORT_MAX_RETRIES; attempt++) {
    try {
      const { data } = await api.post(`/lv/${lvId}/export?format=${format}`, null, {
        responseType: "blob",
        // Axios defaults to 0 (no timeout). For exports we explicitly
        // allow up to 60s for the network round-trip — the backend's
        // own wait_for timeout is 45s, so 60s on the client gives a
        // small grace for TCP + gateway overhead on top.
        timeout: 60_000,
      });
      return data;
    } catch (err: any) {
      lastError = err;
      const status: number | undefined = err?.response?.status;
      const isRetryable = status !== undefined && EXPORT_RETRYABLE_STATUSES.has(status);
      const hasAttemptsLeft = attempt < EXPORT_MAX_RETRIES;
      if (!isRetryable || !hasAttemptsLeft) {
        throw err;
      }
      // Respect Retry-After if the server sent one, otherwise use the
      // default backoff. Retry-After is a header axios exposes via
      // response.headers (all lowercased).
      const retryAfter = Number(err?.response?.headers?.["retry-after"]);
      const delayMs =
        Number.isFinite(retryAfter) && retryAfter > 0
          ? Math.min(retryAfter * 1000, 5_000)
          : EXPORT_RETRY_DELAY_MS;
      await sleep(delayMs);
    }
  }
  // Unreachable — the loop either returns or throws — but TS doesn't know that.
  throw lastError;
};

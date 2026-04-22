import api from "./client";
import type {
  BulkWallCalculationResult,
  Opening,
  Room,
  WallCalculationResult,
} from "../types/room";

export const fetchProjectRooms = async (projectId: string): Promise<Room[]> => {
  const { data } = await api.get(`/projects/${projectId}/rooms`);
  return data;
};

export const updateRoom = async (roomId: string, updates: Partial<Room>): Promise<Room> => {
  const { data } = await api.put(`/rooms/${roomId}`, updates);
  return data;
};

export const deleteRoom = async (roomId: string): Promise<void> => {
  await api.delete(`/rooms/${roomId}`);
};

// `name` is the only required field on RoomCreate (backend schema);
// all other fields are optional and default sensibly.
export const createRoom = async (
  unitId: string,
  data: Partial<Room> & { name: string }
): Promise<Room> => {
  const { data: res } = await api.post(`/units/${unitId}/rooms`, data);
  return res;
};

// --- Openings ----------------------------------------------------------------
//
// The backend's opening endpoints also recalculate the parent room's
// wall-area cache on every mutation (see ``_recalculate_walls_and_persist``
// in app/api/rooms.py). Callers don't need to also fire
// ``calculateWalls`` — the server does it for us.

export const createOpening = async (
  roomId: string,
  data: Omit<Opening, "id" | "room_id" | "source">
): Promise<Opening> => {
  const { data: res } = await api.post(`/rooms/${roomId}/openings`, data);
  return res;
};

export const updateOpening = async (
  openingId: string,
  data: Partial<Omit<Opening, "id" | "room_id" | "source">>
): Promise<Opening> => {
  const { data: res } = await api.put(`/openings/${openingId}`, data);
  return res;
};

export const deleteOpening = async (openingId: string): Promise<void> => {
  await api.delete(`/openings/${openingId}`);
};

/**
 * Recalculate wall areas for a single room after the user edits
 * perimeter, height, is_staircase, or deductions_enabled. The backend
 * also runs this automatically on every PUT /rooms/{id}, but we keep
 * an explicit endpoint so the UI can offer a "recompute this row"
 * button independent of other edits.
 */
export const calculateWalls = async (
  roomId: string
): Promise<WallCalculationResult> => {
  const { data } = await api.post(`/rooms/${roomId}/calculate-walls`);
  return data;
};

/**
 * One-click bulk recalculation for every room in a project. Used by
 * the "Wandflächen berechnen" button above the Wandberechnung table.
 */
export const bulkCalculateWalls = async (
  projectId: string
): Promise<BulkWallCalculationResult> => {
  const { data } = await api.post(
    `/projects/${projectId}/rooms/bulk-calculate-walls`
  );
  return data;
};

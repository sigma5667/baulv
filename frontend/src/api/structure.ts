/**
 * CRUD for the project-structure hierarchy (Gebäude → Stockwerk → Einheit)
 * plus the one-shot tree fetch used by the StructurePage, plus the
 * "Schnell-Anlage Einfamilienhaus" quick-add.
 *
 * Room and Opening CRUD already lives in ``api/rooms.ts`` — we don't
 * duplicate it here. The tree endpoint below returns rooms nested in
 * the same shape anyway, so the structure page reads the whole subtree
 * in one request and uses the existing Room mutation endpoints for
 * edits.
 */

import api from "./client";
import type {
  Building,
  BuildingCreate,
  BuildingUpdate,
  Floor,
  FloorCreate,
  FloorUpdate,
  ProjectStructure,
  QuickAddResponse,
  Unit,
  UnitCreate,
  UnitUpdate,
} from "../types/project";

// --- One-shot tree fetch -----------------------------------------------------

export const fetchProjectStructure = async (
  projectId: string
): Promise<ProjectStructure> => {
  const { data } = await api.get(`/projects/${projectId}/structure`);
  return data;
};

// --- Buildings ---------------------------------------------------------------

export const createBuilding = async (
  projectId: string,
  data: BuildingCreate
): Promise<Building> => {
  const { data: res } = await api.post(
    `/projects/${projectId}/buildings`,
    data
  );
  return res;
};

export const updateBuilding = async (
  buildingId: string,
  data: BuildingUpdate
): Promise<Building> => {
  const { data: res } = await api.put(`/buildings/${buildingId}`, data);
  return res;
};

export const deleteBuilding = async (buildingId: string): Promise<void> => {
  await api.delete(`/buildings/${buildingId}`);
};

// --- Floors ------------------------------------------------------------------

export const createFloor = async (
  buildingId: string,
  data: FloorCreate
): Promise<Floor> => {
  const { data: res } = await api.post(
    `/buildings/${buildingId}/floors`,
    data
  );
  return res;
};

export const updateFloor = async (
  floorId: string,
  data: FloorUpdate
): Promise<Floor> => {
  const { data: res } = await api.put(`/floors/${floorId}`, data);
  return res;
};

export const deleteFloor = async (floorId: string): Promise<void> => {
  await api.delete(`/floors/${floorId}`);
};

// --- Units -------------------------------------------------------------------

export const createUnit = async (
  floorId: string,
  data: UnitCreate
): Promise<Unit> => {
  const { data: res } = await api.post(`/floors/${floorId}/units`, data);
  return res;
};

export const updateUnit = async (
  unitId: string,
  data: UnitUpdate
): Promise<Unit> => {
  const { data: res } = await api.put(`/units/${unitId}`, data);
  return res;
};

export const deleteUnit = async (unitId: string): Promise<void> => {
  await api.delete(`/units/${unitId}`);
};

// --- Quick add: Schnell-Anlage Einfamilienhaus -------------------------------

/**
 * Seeds a "Haupthaus" building with Keller / EG / OG stockwerke and one
 * "Standard" unit per floor. The backend refuses with a 400 if a
 * Gebäude called "Haupthaus" already exists — testers who click twice
 * would otherwise end up with duplicate trees.
 */
export const quickAddSingleFamily = async (
  projectId: string
): Promise<QuickAddResponse> => {
  const { data } = await api.post(
    `/projects/${projectId}/quick-add/single-family`
  );
  return data;
};

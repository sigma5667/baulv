export interface Project {
  id: string;
  name: string;
  description: string | null;
  address: string | null;
  client_name: string | null;
  project_number: string | null;
  grundstuecksnr: string | null;
  planverfasser: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  name: string;
  description?: string;
  address?: string;
  client_name?: string;
  project_number?: string;
  grundstuecksnr?: string;
  planverfasser?: string;
}

import type { Room } from "./room";

export interface Building {
  id: string;
  project_id: string;
  name: string;
  sort_order: number;
}

export interface BuildingCreate {
  name: string;
  sort_order?: number;
}

export interface BuildingUpdate {
  name?: string;
  sort_order?: number;
}

export interface Floor {
  id: string;
  building_id: string;
  name: string;
  level_number: number | null;
  floor_height_m: number | null;
  sort_order: number;
}

export interface FloorCreate {
  name: string;
  level_number?: number | null;
  floor_height_m?: number | null;
  sort_order?: number;
}

export interface FloorUpdate {
  name?: string;
  level_number?: number | null;
  floor_height_m?: number | null;
  sort_order?: number;
}

export interface Unit {
  id: string;
  floor_id: string;
  name: string;
  unit_type: string | null;
  sort_order: number;
}

export interface UnitCreate {
  name: string;
  unit_type?: string | null;
  sort_order?: number;
}

export interface UnitUpdate {
  name?: string;
  unit_type?: string | null;
  sort_order?: number;
}

// --- Aggregated structure tree ----------------------------------------------
// Mirrors the server-side ``ProjectStructureResponse`` payload. The tree is
// rendered by ``StructurePage`` as collapsible nested cards; keeping the
// nesting on the types (rather than normalizing into a flat list on the
// client) means the UI never has to stitch four separate queries back
// together to redraw after a mutation.

export interface UnitWithRooms extends Unit {
  rooms: Room[];
}

export interface FloorWithUnits extends Floor {
  units: UnitWithRooms[];
}

export interface BuildingWithChildren extends Building {
  floors: FloorWithUnits[];
}

export interface ProjectStructure {
  project_id: string;
  buildings: BuildingWithChildren[];
}

export interface QuickAddResponse {
  project_id: string;
  building_id: string;
  floor_ids: string[];
  unit_ids: string[];
}

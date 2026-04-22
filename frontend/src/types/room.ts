export interface Opening {
  id: string;
  room_id: string;
  opening_type: string;
  width_m: number;
  height_m: number;
  count: number;
  description: string | null;
  source: string;
}

/**
 * Source of the ceiling-height value for a room.
 *
 * * ``schnitt``   — extracted from a Schnittzeichnung
 * * ``grundriss`` — labelled on the floorplan (e.g. "RH=2.50")
 * * ``manual``    — the user typed it in
 * * ``default``   — assumed 2.50 m because nothing else was available;
 *                   the UI highlights these rows in amber so the user
 *                   confirms before the number flows into the LV
 */
export type CeilingHeightSource = "schnitt" | "grundriss" | "manual" | "default";

export interface Room {
  id: string;
  unit_id: string;
  plan_id: string | null;
  name: string;
  room_number: string | null;
  room_type: string | null;
  area_m2: number | null;
  perimeter_m: number | null;
  height_m: number | null;
  ceiling_height_source: CeilingHeightSource;
  floor_type: string | null;
  wall_type: string | null;
  ceiling_type: string | null;
  is_wet_room: boolean;
  has_dachschraege: boolean;
  is_staircase: boolean;
  /** Gross wall area (perimeter × height × factor). Null until calculated. */
  wall_area_gross_m2: number | null;
  /** Net wall area (gross minus openings ≥ 2.5 m² when deductions enabled). */
  wall_area_net_m2: number | null;
  /** Multiplier applied on the last run: 1.0, 1.12, 1.16, or 1.5. */
  applied_factor: number | null;
  /** If false, the calculator treats gross == net (no opening deduction). */
  deductions_enabled: boolean;
  source: string;
  ai_confidence: number | null;
  openings: Opening[];
}

export interface WallCalculationResult {
  room_id: string;
  wall_area_gross_m2: number;
  wall_area_net_m2: number;
  applied_factor: number;
  deductions_total_m2: number;
  deductions_considered_count: number;
  perimeter_m: number;
  height_used_m: number;
  ceiling_height_source: CeilingHeightSource;
}

export interface BulkWallCalculationResult {
  project_id: string;
  rooms_calculated: number;
  results: WallCalculationResult[];
}

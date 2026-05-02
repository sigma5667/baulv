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
 * * ``default``   — assumed 2.50 m because nothing else was available
 */
export type CeilingHeightSource = "schnitt" | "grundriss" | "manual" | "default";

/**
 * Provenance of the ``perimeter_m`` value. Confidence ladder
 * (high → low for AI extractions, plus the user-driven ``manual``
 * which trumps everything):
 *
 * * ``labeled``   — Vision read the inline perimeter label the
 *                   architect printed under the area on the plan.
 *                   Highest AI confidence (CAD output, 2-decimal).
 * * ``computed``  — Vision summed the dimension chain along the
 *                   walls itself. Medium AI confidence.
 * * ``vision``    — pre-v22.3 extraction; we couldn't tell which
 *                   of the two strategies above produced the
 *                   value, so we group them under one tag.
 * * ``estimated`` — backend fallback (4·√area·1.10) when Vision
 *                   returned nothing. Lowest confidence among the
 *                   non-null sources.
 * * ``manual``    — the user typed or corrected the value via the
 *                   inline editor or the manual room form.
 *                   Highest overall confidence — user > Vision.
 * * ``null``      — genuinely unknown (no perimeter, no area).
 *                   Rendered as the red "Bitte eintragen" badge.
 */
export type PerimeterSource =
  | "labeled"
  | "computed"
  | "vision"
  | "estimated"
  | "manual";

export interface Room {
  id: string;
  unit_id: string;
  plan_id: string | null;
  name: string;
  room_number: string | null;
  room_type: string | null;
  area_m2: number | null;
  perimeter_m: number | null;
  perimeter_source: PerimeterSource | null;
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
  /** Pin coordinates (v23.1, Phase 1 of plan-visualisation).
   *  Pixel-space inside a 300-DPI PNG render of ``page_number``.
   *  All four are NULL together when Vision wasn't sure or when
   *  the room came in via the manual editor. The Phase 2 pin
   *  renderer should treat any NULL among these five as "no pin
   *  for this room". */
  position_x: number | null;
  position_y: number | null;
  /** 1-based PDF page index. Injected by the pipeline so it's
   *  always trustworthy when ``position_x``/``position_y`` are
   *  populated. */
  page_number: number | null;
  bbox_width: number | null;
  bbox_height: number | null;
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

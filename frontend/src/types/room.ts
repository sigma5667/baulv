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
  floor_type: string | null;
  wall_type: string | null;
  ceiling_type: string | null;
  is_wet_room: boolean;
  has_dachschraege: boolean;
  is_staircase: boolean;
  source: string;
  ai_confidence: number | null;
  openings: Opening[];
}

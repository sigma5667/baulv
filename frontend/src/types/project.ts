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

export interface Building {
  id: string;
  project_id: string;
  name: string;
  sort_order: number;
}

export interface Floor {
  id: string;
  building_id: string;
  name: string;
  level_number: number | null;
  floor_height_m: number | null;
  sort_order: number;
}

export interface Unit {
  id: string;
  floor_id: string;
  name: string;
  unit_type: string | null;
  sort_order: number;
}

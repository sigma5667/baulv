export interface Berechnungsnachweis {
  id: string;
  position_id: string;
  room_id: string;
  raw_quantity: number;
  formula_description: string;
  formula_expression: string;
  // Renamed from ``onorm_*`` in the backend response. The DB columns
  // keep the historic ``onorm_`` prefix for compatibility with existing
  // rows, but the API + UI use generic terms — the app is a calculation
  // engine, not a norm library.
  rule_factor: number;
  rule_ref: string | null;
  rule_paragraph: string | null;
  deductions: DeductionDetail[];
  net_quantity: number;
  unit: string;
  notes: string | null;
}

export interface DeductionDetail {
  opening: string;
  area: number;
  deducted: boolean;
  reason?: string;
}

export interface Position {
  id: string;
  gruppe_id: string;
  positions_nummer: string;
  kurztext: string;
  langtext: string | null;
  einheit: string;
  menge: number | null;
  einheitspreis: number | null;
  gesamtpreis: number | null;
  positionsart: string;
  text_source: string;
  is_locked: boolean;
  sort_order: number;
  berechnungsnachweise: Berechnungsnachweis[];
}

export interface Leistungsgruppe {
  id: string;
  lv_id: string;
  nummer: string;
  bezeichnung: string;
  sort_order: number;
  positionen: Position[];
}

export interface LV {
  id: string;
  project_id: string;
  name: string;
  trade: string;
  status: string;
  vorbemerkungen: string | null;
  created_at: string;
  updated_at: string;
  gruppen: Leistungsgruppe[];
}

export interface LVCreate {
  name: string;
  trade: string;
  vorbemerkungen?: string;
}

// LV template library — TypeScript types mirror the backend shapes in
// ``backend/app/schemas/template.py``. Keep in sync when adding fields.

export type TemplateCategory =
  | "einfamilienhaus"
  | "wohnanlage"
  | "buero"
  | "sanierung"
  | "dachausbau"
  | "sonstiges";

// Labels used in the filter pill bar and in preview headers. Keeping
// the translation here so the backend can stay language-neutral.
export const TEMPLATE_CATEGORY_LABELS: Record<TemplateCategory, string> = {
  einfamilienhaus: "Einfamilienhaus",
  wohnanlage: "Wohnanlage",
  buero: "Bürogebäude",
  sanierung: "Sanierung",
  dachausbau: "Dachausbau",
  sonstiges: "Sonstiges",
};

// Gewerk labels — v17 only ships Malerarbeiten templates but the field
// exists so adding Elektro/Sanitär/etc. later is a pure data change.
export const TEMPLATE_GEWERK_LABELS: Record<string, string> = {
  malerarbeiten: "Malerarbeiten",
};

export interface TemplatePosition {
  positions_nummer: string;
  kurztext: string;
  langtext: string | null;
  einheit: string;
  kategorie: string | null; // "wand" | "decke" | "boden" | "vorarbeit" | "sonstiges"
}

export interface TemplateGruppe {
  nummer: string;
  bezeichnung: string;
  positionen: TemplatePosition[];
}

export interface TemplateData {
  gruppen: TemplateGruppe[];
}

/**
 * Row in the templates list — no positions payload, only the
 * aggregate counts so we don't ship hundreds of Langtext paragraphs
 * on every page load.
 */
export interface TemplateSummary {
  id: string;
  name: string;
  description: string | null;
  category: string;
  gewerk: string;
  is_system: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  gruppen_count: number;
  positionen_count: number;
}

/** Full template including the positions payload. */
export interface TemplateDetail extends TemplateSummary {
  template_data: TemplateData;
}

export interface TemplateCreateFromLV {
  lv_id: string;
  name: string;
  description?: string;
  category: TemplateCategory;
}

export interface LVFromTemplateRequest {
  project_id: string;
  template_id: string;
  name?: string;
}

export interface LVFromTemplateResponse {
  lv_id: string;
  project_id: string;
  name: string;
  trade: string;
  gruppen_created: number;
  positionen_created: number;
}

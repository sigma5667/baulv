/**
 * v24.1 — canonical plan-type values. Matches the backend whitelist
 * in ``app/api/plans.py`` (``ALLOWED_PLAN_TYPES``). The Plan type's
 * ``plan_type`` field stays widened to ``string | null`` because
 * legacy pre-v24.1 rows may carry NULL or arbitrary historical
 * values; UI code maps anything not in this set to "Grundriss" for
 * back-compat (the analyse path does the same on the backend).
 */
export type PlanType = "grundriss" | "schnitt" | "lageplan";

export const PLAN_TYPE_LABELS: Record<PlanType, string> = {
  grundriss: "Grundriss",
  schnitt: "Schnitt",
  lageplan: "Lageplan",
};

export const PLAN_TYPE_DESCRIPTIONS: Record<PlanType, string> = {
  grundriss:
    "Standard-Bauplan mit Räumen, Wänden und Maßen. Wird per KI analysiert.",
  schnitt:
    "Vertikalschnitt mit Raumhöhen. Höhen-Extraktion ist in Vorbereitung.",
  lageplan:
    "Übersichtsplan des Grundstücks. Wird gespeichert, nicht analysiert.",
};

/** Coerce a free-form ``plan_type`` (incl. NULL / legacy values)
 * into one of the canonical types. Default is ``grundriss`` to
 * mirror the backend's back-compat path. */
export function normalisePlanType(raw: string | null): PlanType {
  if (raw === "schnitt" || raw === "lageplan") return raw;
  return "grundriss";
}

export interface Plan {
  id: string;
  project_id: string;
  filename: string;
  file_size_bytes: number | null;
  page_count: number | null;
  plan_type: string | null;
  analysis_status: string;
  created_at: string;
}

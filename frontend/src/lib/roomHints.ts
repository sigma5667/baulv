import type { Room } from "../types/room";

/**
 * Cell annotation for ``perimeter_m`` — the subtle hint badge text
 * (or undefined when no badge is appropriate) plus the longer
 * tooltip surfaced on hover.
 *
 * Used by both the wall-calculation table on ``PlanAnalysisPage``
 * and the room cards in the manual structure tree on
 * ``StructurePage``. Centralised here because the source-to-text
 * mapping is part of the v22.3 perimeter-source contract — drifting
 * it across the two pages would let the same row look different
 * depending on where the user opened it.
 *
 * Rules
 * -----
 * * ``perimeter_m`` IS NULL → no hint, the cell falls into the red
 *   "Bitte eintragen" empty-state badge in the InlineNumericEdit
 *   itself. Tooltip is the missing-state message.
 * * ``manual`` → no hint icon. The user typed it; we trust them
 *   silently and don't pile up annotations.
 * * Every other non-null source → an amber Info badge with a
 *   tooltip that says how the value was sourced.
 *
 * The wording mirrors the German conventions used elsewhere in
 * the UI ("aus Plan abgelesen", "aus Vermassung berechnet", etc.)
 * so a user reading several rows in a table doesn't context-switch.
 */
export function perimeterAnnotation(room: Room): {
  hint?: string;
  tooltip: string;
} {
  if (room.perimeter_m === null) {
    return {
      tooltip:
        "Wandumfang fehlt — bitte aus Plan messen oder schätzen",
    };
  }
  switch (room.perimeter_source) {
    case "labeled":
      return {
        hint: "Aus Plan abgelesen",
        tooltip:
          "Wandumfang aus der Architekten-Beschriftung im Raum übernommen",
      };
    case "computed":
      return {
        hint: "Aus Vermassung berechnet",
        tooltip:
          "Wandumfang von der KI aus der Vermassungskette berechnet",
      };
    case "estimated":
      return {
        hint: "geschätzt — bitte prüfen",
        tooltip:
          "Wandumfang aus Fläche geschätzt — bitte aus Plan prüfen",
      };
    case "vision":
      return {
        hint: "von KI erkannt",
        tooltip: "Wandumfang von der KI aus dem Plan erkannt",
      };
    case "manual":
      return { tooltip: "Wandumfang (manuell eingegeben)" };
    default:
      // Legacy or unknown source — show the value without any
      // annotation. Better than guessing.
      return { tooltip: "Wandumfang" };
  }
}

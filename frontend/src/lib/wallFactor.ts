/**
 * Begründung des Wand-Faktors je Raum (Wandberechnung).
 *
 * Spiegelt nur, was ``backend/app/services/wall_calculator._resolve_factor``
 * rechnet — die Regel selbst wird hier NICHT verändert:
 *   * Treppenhaus-Aufschlag 1,5 × greift ausschließlich über das Häkchen
 *     ``is_staircase`` (nicht über den Raumtyp),
 *   * sonst Höhenzuschlag ab 3,0 m Raumhöhe bzw. über 4,0 m,
 *   * sonst 1,00.
 *
 * Live-Befund 2026-11-24: "Laubengang/Stg verglast" war als Typ
 * ``stiegenhaus`` erkannt, aber ohne Häkchen → Faktor 1,00, während der
 * Seitentext "Treppenhaus-Aufschlag 1,5 ×" nennt. Kein Rechenfehler,
 * aber unbegründet sichtbar. Jetzt steht der Grund in der Zeile, und bei
 * diesem Widerspruch erscheint ein Prüfhinweis.
 */

export interface FactorRoom {
  name?: string | null;
  room_type?: string | null;
  is_staircase?: boolean | null;
  applied_factor?: number | null;
  height_m?: number | null;
}

const STAIR_TYPES = ["stiegenhaus", "treppenhaus", "stairwell"];
const STAIR_NAME_RE = /(stiegenhaus|treppenhaus|stiege|treppe|\bstg\b)/i;

/** Typ oder Name deuten auf ein Treppenhaus, das Häkchen fehlt aber. */
export function looksLikeUnmarkedStaircase(room: FactorRoom): boolean {
  if (room.is_staircase) return false;
  const type = (room.room_type ?? "").toLowerCase();
  return STAIR_TYPES.includes(type) || STAIR_NAME_RE.test(room.name ?? "");
}

export function factorReason(room: FactorRoom): string {
  const f = room.applied_factor;
  if (f === null || f === undefined) return "noch nicht berechnet";
  if (room.is_staircase) return "Treppenhaus-Aufschlag";
  if (f > 1) {
    const h = room.height_m ?? 0;
    return h > 4 ? "Höhenzuschlag (über 4,0 m)" : "Höhenzuschlag (ab 3,0 m)";
  }
  if (looksLikeUnmarkedStaircase(room)) return "ohne Aufschlag: nicht als Treppenhaus markiert";
  return "Standard";
}

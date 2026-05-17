import { useEffect, useRef, useState } from "react";

/**
 * v24.4 — Dropdown + Freitext-Fallback für ``Room.floor_type``.
 *
 * Standard-Liste deckt 9 typische Beläge ab (Slugs in Lockstep mit
 * dem Backend in ``app/services/floor_covering.py``); plus eine
 * "Sonstiges (Freitext)"-Option, die ein Textfeld auffaltet und
 * den getippten Wert als Free-Text speichert. Das Backend lässt
 * unbekannte Strings durch (siehe ``normalise_floor_covering``)
 * und der Mengenermittlungs-PDF rendert sie als eigene Gruppen-
 * zeile in der Aggregation.
 *
 * Verhalten
 * ---------
 *
 *   * ``value`` ist der aktuelle ``room.floor_type``: entweder ein
 *     Slug aus der Liste, ein Free-Text aus früherem "Sonstiges",
 *     oder ``null`` (kein Belag).
 *   * Der User wählt eine Option oder gibt einen Freitext ein.
 *   * Auf "Speichern" wird ``onSave(value)`` mit dem neuen Wert
 *     aufgerufen (Slug oder Free-Text-String oder null beim
 *     "Kein Belag"-Reset).
 *   * "Abbrechen" oder ESC schließt ohne Speichern.
 *
 * Lock-in zum Backend
 * -------------------
 *
 * Slug-Liste + Labels müssen mit dem Backend-Service synchron
 * bleiben. Bei Änderung am einen Ende: das andere mitziehen.
 * Es gibt einen Test ``test_slugs_and_labels_are_consistent`` der
 * Backend-Slug-Liste vs Backend-Label-Map locked; das Frontend
 * vertraut auf die Stabilität dieser Liste.
 */

export interface FloorCoveringOption {
  slug: string;
  label: string;
}

/**
 * Kanonische Belag-Optionen. Exportiert damit das "Mehr…"-Modal-
 * Form (in ``PlanAnalysisPage.tsx``) dieselbe Liste rendern kann
 * ohne sie zu duplizieren.
 */
export const COVERING_OPTIONS: FloorCoveringOption[] = [
  { slug: "parkett", label: "Parkett" },
  { slug: "fliesen", label: "Fliesen" },
  { slug: "laminat", label: "Laminat" },
  { slug: "vinyl", label: "Vinyl" },
  { slug: "teppich", label: "Teppich" },
  { slug: "linoleum", label: "Linoleum" },
  { slug: "naturstein", label: "Naturstein" },
  { slug: "beton", label: "Beton" },
  { slug: "estrich", label: "Estrich" },
];

const SLUGS = new Set(COVERING_OPTIONS.map((o) => o.slug));

interface Props {
  value: string | null;
  onSave: (next: string | null) => void;
  onCancel: () => void;
  isSaving?: boolean;
  ariaLabel?: string;
}

export function FloorCoveringSelect({
  value,
  onSave,
  onCancel,
  isSaving,
  ariaLabel,
}: Props) {
  // Initial-Modus bestimmen:
  //   - value === null → "(kein Belag)" Auswahl
  //   - value ist Slug → Slug-Option gewählt
  //   - value ist Free-Text → "Sonstiges" + Textfeld mit dem Text
  const isKnownSlug = value !== null && SLUGS.has(value);
  const initialSelection = value === null
    ? "__none__"
    : isKnownSlug
      ? value
      : "__sonstiges__";
  const initialFreetext = value !== null && !isKnownSlug ? value : "";

  const [selection, setSelection] = useState<string>(initialSelection);
  const [freetext, setFreetext] = useState<string>(initialFreetext);
  const freetextRef = useRef<HTMLInputElement | null>(null);

  // Wenn der User auf "Sonstiges" wechselt: Fokus ins Textfeld.
  useEffect(() => {
    if (selection === "__sonstiges__") {
      freetextRef.current?.focus();
    }
  }, [selection]);

  // ESC-Handling für "Abbrechen".
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  const handleSave = () => {
    if (selection === "__none__") {
      onSave(null);
      return;
    }
    if (selection === "__sonstiges__") {
      const trimmed = freetext.trim();
      // Freitext leer → äquivalent zu "kein Belag".
      onSave(trimmed === "" ? null : trimmed);
      return;
    }
    // Slug-Option direkt.
    onSave(selection);
  };

  return (
    <div
      className="inline-flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label={ariaLabel ?? "Bodenbelag bearbeiten"}
    >
      <select
        value={selection}
        onChange={(e) => setSelection(e.target.value)}
        disabled={isSaving}
        className="rounded border border-primary bg-background px-1.5 py-0.5 text-xs"
        autoFocus
      >
        <option value="__none__">— Kein Belag —</option>
        {COVERING_OPTIONS.map((o) => (
          <option key={o.slug} value={o.slug}>
            {o.label}
          </option>
        ))}
        <option value="__sonstiges__">Sonstiges (Freitext)…</option>
      </select>

      {selection === "__sonstiges__" && (
        <input
          ref={freetextRef}
          type="text"
          value={freetext}
          onChange={(e) => setFreetext(e.target.value)}
          placeholder="z.B. Designboden Marke X"
          disabled={isSaving}
          maxLength={100}
          className="w-40 rounded border border-primary bg-background px-1.5 py-0.5 text-xs"
        />
      )}

      <button
        type="button"
        onClick={handleSave}
        disabled={isSaving}
        className="rounded bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {isSaving ? "…" : "OK"}
      </button>
      <button
        type="button"
        onClick={onCancel}
        disabled={isSaving}
        className="rounded border px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-50"
      >
        Abbrechen
      </button>
    </div>
  );
}

/**
 * v24.4 — User-facing label für einen ``floor_type``-Wert.
 * Mirror der Backend-Funktion ``display_label`` (in
 * ``backend/app/services/floor_covering.py``).
 *
 *   * Bekannter Slug → deutsches Anzeige-Label.
 *   * Freitext → unverändert durchreichen.
 *   * ``null`` / leer → ``"—"`` (Em-Dash als Tabellen-Empty-State).
 */
export function floorCoveringLabel(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const trimmed = value.trim();
  if (trimmed === "") return "—";
  const option = COVERING_OPTIONS.find((o) => o.slug === trimmed);
  return option ? option.label : trimmed;
}

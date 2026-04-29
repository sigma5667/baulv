import { useRef, useState } from "react";
import { AlertTriangle, Edit2 } from "lucide-react";

/**
 * Click-to-edit cell for a single numeric Room field.
 *
 * Why this exists
 * ---------------
 * The wall-calc table is precisely the place where the user notices
 * "ah, this room has no perimeter / no real height — that's why my
 * gross is 0". Forcing them to open a separate edit modal to fix one
 * number is friction. This component lets them click the value (or the
 * empty-state badge) directly, type a fix, and watch the recalculated
 * wall area redraw — without ever leaving the table.
 *
 * Used both in ``WallCalculationTable`` (the primary edit surface, on
 * ``PlanAnalysisPage``) and in ``RoomNode`` on the manual structure
 * editor — same UX semantics in both places so muscle memory carries.
 *
 * States
 * ------
 * ``ok``      — value present, low-key affordance: pencil icon shows
 *               on hover. Looks like prose, edits like a form.
 * ``missing`` — value is null. Loud red badge ("Bitte eintragen") so
 *               the user can't miss the gap before the calc runs
 *               against zero.
 * ``warning`` — value present but low-confidence (currently only used
 *               for ``ceiling_height_source === "default"``). Amber
 *               "Bitte prüfen" with edit icon — visible call to action
 *               that doesn't block reading the number.
 *
 * Save semantics
 * --------------
 * Comma → dot for German input. Empty input saves ``null`` (so the
 * user can explicitly clear a value). Enter or blur commit. Invalid
 * input (NaN, negative) silently reverts — keeps the cell calm
 * without inventing toast UX for typos.
 *
 * Escape semantics — the part v21 got wrong
 * -----------------------------------------
 * Pressing Escape calls ``setEditing(false)``. React unmounts the
 * input on the next render — but **before** that, the input loses
 * focus, which fires ``onBlur``, which calls ``commit()``. So a naive
 * implementation ends up committing the draft despite the user
 * pressing Escape. The fix is a ref that ``cancel`` flips and that
 * ``commit`` checks first; if cancel was requested, commit returns
 * early. The ref is reset on the next ``startEdit`` so it doesn't
 * carry over between sessions.
 */
export function InlineNumericEdit({
  value,
  unit,
  state,
  missingLabel,
  warningLabel,
  tooltip,
  isSaving,
  onSave,
  digits = 2,
  ariaLabel,
}: {
  value: number | null;
  unit: string;
  state: "ok" | "missing" | "warning";
  missingLabel: string;
  warningLabel: string;
  tooltip: string;
  isSaving: boolean;
  onSave: (next: number | null) => void;
  digits?: number;
  ariaLabel: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>("");

  // True between Escape-keydown and the immediately-following blur.
  // Without this guard the blur handler would commit the draft the
  // user just tried to discard. See "Escape semantics" in the
  // module-level docstring.
  const cancelRequestedRef = useRef(false);

  const startEdit = () => {
    cancelRequestedRef.current = false;
    setDraft(value !== null ? value.toFixed(digits).replace(".", ",") : "");
    setEditing(true);
  };

  const cancel = () => {
    cancelRequestedRef.current = true;
    setEditing(false);
  };

  const commit = () => {
    if (cancelRequestedRef.current) {
      // The user pressed Escape — the unmount-triggered blur
      // shouldn't commit anything. Reset the flag so the next edit
      // session starts clean.
      cancelRequestedRef.current = false;
      return;
    }

    const trimmed = draft.trim();
    if (trimmed === "") {
      // Empty saves null. Allows the user to explicitly clear a value.
      if (value !== null) onSave(null);
      setEditing(false);
      return;
    }
    const parsed = parseFloat(trimmed.replace(",", "."));
    if (Number.isNaN(parsed) || parsed < 0) {
      // Garbage input — revert silently. The original value stays.
      setEditing(false);
      return;
    }
    if (value !== null && Math.abs(parsed - value) < 1e-6) {
      // Same value — skip the round-trip.
      setEditing(false);
      return;
    }
    onSave(parsed);
    setEditing(false);
  };

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1">
        <input
          type="text"
          inputMode="decimal"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          }}
          aria-label={ariaLabel}
          disabled={isSaving}
          className="w-20 rounded border border-primary bg-background px-1.5 py-0.5 font-mono text-xs"
        />
        {unit && (
          <span className="text-[10px] text-muted-foreground">{unit}</span>
        )}
      </span>
    );
  }

  if (state === "missing") {
    return (
      <button
        type="button"
        onClick={startEdit}
        title={tooltip}
        aria-label={ariaLabel}
        className="inline-flex items-center gap-1 rounded border border-destructive/50 bg-destructive/10 px-1.5 py-0.5 text-[11px] font-medium text-destructive transition-colors hover:bg-destructive/20"
      >
        <AlertTriangle className="h-3 w-3" />
        {missingLabel}
      </button>
    );
  }

  if (state === "warning") {
    return (
      <button
        type="button"
        onClick={startEdit}
        title={tooltip}
        aria-label={ariaLabel}
        className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 transition-colors hover:bg-amber-100"
      >
        <span className="font-mono">
          {value !== null
            ? `${value.toFixed(digits).replace(".", ",")}${unit ? " " + unit : ""}`
            : "—"}
        </span>
        <span className="text-[9px] uppercase tracking-wide">
          {warningLabel}
        </span>
        <Edit2 className="h-3 w-3" />
      </button>
    );
  }

  // state === "ok"
  return (
    <button
      type="button"
      onClick={startEdit}
      title={`${tooltip} — Klicken zum Bearbeiten`}
      aria-label={ariaLabel}
      className="group inline-flex items-center gap-1 rounded px-1 transition-colors hover:bg-accent"
    >
      <span className="font-mono text-foreground">
        {value !== null
          ? `${value.toFixed(digits).replace(".", ",")}${unit ? " " + unit : ""}`
          : "—"}
      </span>
      <Edit2 className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-50" />
    </button>
  );
}

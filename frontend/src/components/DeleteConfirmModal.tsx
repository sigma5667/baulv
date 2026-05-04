/**
 * DeleteConfirmModal — type-the-name confirmation for destructive
 * deletes. Used by the v23.5 project-delete flow on Dashboard +
 * ProjectDetailPage; reusable for any entity that wants the same
 * "are you really sure" gate.
 *
 * Design
 * ------
 *
 * Two-factor confirmation: the user must (a) click the destructive
 * button AND (b) type the entity name verbatim into the input
 * field. Without the second factor a misclick on the trash icon →
 * an accidental Enter on a focused button could silently drop the
 * user's project.
 *
 * The destructive button stays disabled until the typed name
 * matches exactly (trimmed). We deliberately do NOT lowercase or
 * normalise — if the project is "Wohnhaus Linzer Straße 42", the
 * user types it exactly. That makes a casual ⌘+V from the title
 * still work, but a half-typed prefix doesn't fire the delete.
 *
 * Cascade warning
 * ---------------
 *
 * The ``cascadeWarning`` prop lists what will *also* be deleted —
 * for projects that's "Alle LVs, Pläne und Räume". We surface it
 * as a separate visual block so the user can't claim "I didn't
 * realise the LVs would go too".
 *
 * Dismissibility
 * --------------
 *
 * Backdrop click and Escape both cancel — this is a *voluntary*
 * destructive action (unlike ``ConsentRefreshModal`` which gates
 * continued app use). Cancel is the default; the destructive
 * button is the off-path.
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Trash2, X } from "lucide-react";

export interface DeleteConfirmModalProps {
  /** What is being deleted — used in the title and as the type-to-
   * confirm string. For a project this is the project name. */
  entityName: string;
  /** Singular German label used in the title, e.g. "Projekt".
   * Falls back to a generic "Eintrag" if not provided. */
  entityLabel?: string;
  /** Optional list of cascading consequences shown as a warning
   * block. e.g. ["Alle LVs", "alle Pläne", "alle Räume"]. */
  cascadeItems?: string[];
  /** Fired when the user confirms (button click or Enter when the
   * destructive button is enabled). */
  onConfirm: () => void;
  /** Fired on backdrop click, Escape key, or "Abbrechen" button. */
  onCancel: () => void;
  /** When true, the destructive button shows a spinner and both
   * buttons are disabled. */
  isLoading?: boolean;
  /** Optional German error message to surface inline (e.g. when the
   * delete request 5xx'd). */
  errorMessage?: string | null;
}

export function DeleteConfirmModal({
  entityName,
  entityLabel = "Eintrag",
  cascadeItems,
  onConfirm,
  onCancel,
  isLoading = false,
  errorMessage,
}: DeleteConfirmModalProps) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the input on mount so the user can start typing
  // immediately. The destructive button is the wrong default focus
  // target — Enter on it would skip the type-to-confirm gate.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Escape cancels. We listen on the document so the listener
  // works even when focus is on a non-button element.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isLoading) {
        onCancel();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel, isLoading]);

  const matches = typed.trim() === entityName.trim();
  const canConfirm = matches && !isLoading;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canConfirm) onConfirm();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-confirm-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={() => {
        if (!isLoading) onCancel();
      }}
    >
      <div
        className="w-full max-w-md rounded-lg border bg-card p-6 shadow-2xl"
        // Stop propagation so clicking inside the modal doesn't
        // trigger the backdrop cancel handler.
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-red-100 p-2">
            <AlertTriangle className="h-5 w-5 text-red-700" />
          </div>
          <div className="flex-1">
            <h2 id="delete-confirm-title" className="text-lg font-semibold">
              {entityLabel} „{entityName}" wirklich löschen?
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Diese Aktion ist <strong>unwiderruflich</strong>.
            </p>
          </div>
          {/* Close (X) — equivalent to "Abbrechen" but lets the user
              dismiss the modal without scrolling to the buttons. */}
          <button
            type="button"
            onClick={onCancel}
            disabled={isLoading}
            aria-label="Schließen"
            className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {cascadeItems && cascadeItems.length > 0 && (
          <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <p className="font-medium">Folgendes wird ebenfalls gelöscht:</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              {cascadeItems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {errorMessage && (
          <div className="mb-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {errorMessage}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="delete-confirm-name"
              className="mb-1 block text-sm font-medium"
            >
              Geben Sie zur Bestätigung „
              <span className="font-mono">{entityName}</span>" ein:
            </label>
            <input
              ref={inputRef}
              id="delete-confirm-name"
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={isLoading}
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-destructive focus:outline-none focus:ring-1 focus:ring-destructive"
              placeholder={entityName}
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={isLoading}
              className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
            >
              Abbrechen
            </button>
            <button
              type="submit"
              disabled={!canConfirm}
              className="flex items-center gap-1.5 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              {isLoading ? "Lösche…" : "Endgültig löschen"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

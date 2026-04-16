import { Component, useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, X, RefreshCw } from "lucide-react";
import {
  subscribeDiagnostics,
  clearDiagnostic,
  clearAllDiagnostics,
  pushDiagnostic,
  type DiagnosticError,
} from "../lib/diagnostics";

/**
 * Floating diagnostic overlay. Renders a fixed-position red panel at
 * the top-right of the viewport listing any uncaught errors. Mounted
 * once at the top of ``App`` so it's visible on every page.
 *
 * Users should not see this in normal operation — if it shows up, a
 * real bug fired. The whole point is to make sure no click ends in
 * "nothing happened".
 */
export function ErrorOverlay() {
  const [errors, setErrors] = useState<DiagnosticError[]>([]);

  useEffect(() => {
    const unsub = subscribeDiagnostics(setErrors);
    return () => {
      unsub();
    };
  }, []);

  if (errors.length === 0) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-[9999] flex flex-col items-center gap-2 p-4">
      {errors.map((err) => (
        <div
          key={err.id}
          role="alert"
          className="pointer-events-auto flex w-full max-w-2xl items-start gap-3 rounded-lg border border-destructive/50 bg-destructive/95 p-3 text-white shadow-lg"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="flex-1 text-sm">
            <p className="font-semibold">
              Ein Fehler ist aufgetreten
              <span className="ml-2 rounded bg-white/20 px-1.5 py-0.5 text-[10px] font-normal uppercase tracking-wide">
                {err.source}
              </span>
            </p>
            <p className="mt-0.5 break-words">{err.message}</p>
            {err.stack && (
              <details className="mt-1 text-xs opacity-90">
                <summary className="cursor-pointer">Technische Details</summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[10px]">
                  {err.stack}
                </pre>
              </details>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="flex items-center gap-1 rounded border border-white/40 px-2 py-1 text-xs hover:bg-white/10"
              title="Seite neu laden"
            >
              <RefreshCw className="h-3 w-3" />
              Neu laden
            </button>
            <button
              type="button"
              onClick={() => clearDiagnostic(err.id)}
              className="flex items-center justify-center rounded border border-white/40 px-2 py-1 text-xs hover:bg-white/10"
              aria-label="Schließen"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        </div>
      ))}
      {errors.length > 1 && (
        <button
          type="button"
          onClick={clearAllDiagnostics}
          className="pointer-events-auto rounded border border-white/40 bg-destructive/95 px-2 py-1 text-xs text-white"
        >
          Alle ausblenden
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// React error boundary — catches render/lifecycle errors so a
// component crash stops being a silent white-screen.
// ---------------------------------------------------------------------------

interface EBState {
  error: Error | null;
}

export class RootErrorBoundary extends Component<
  { children: ReactNode },
  EBState
> {
  state: EBState = { error: null };

  static getDerivedStateFromError(error: Error): EBState {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    pushDiagnostic(
      "error",
      error.message || "React render error",
      (info.componentStack || error.stack) ?? undefined
    );
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-destructive/10 p-6">
          <div className="max-w-lg rounded-lg border border-destructive/40 bg-white p-6 shadow-lg">
            <div className="mb-2 flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5" />
              <h1 className="text-lg font-semibold">
                Ein Fehler ist aufgetreten
              </h1>
            </div>
            <p className="mb-3 text-sm text-muted-foreground">
              Die Anwendung ist auf einen unerwarteten Fehler gestoßen. Bitte
              laden Sie die Seite neu. Wenn das Problem bestehen bleibt,
              kontaktieren Sie bitte den Support.
            </p>
            <pre className="mb-3 max-h-40 overflow-auto rounded bg-muted p-2 text-xs">
              {this.state.error.message}
              {this.state.error.stack
                ? "\n\n" + this.state.error.stack
                : ""}
            </pre>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <RefreshCw className="h-4 w-4" />
              Seite neu laden
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

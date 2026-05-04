/**
 * Global toast system (v23.6).
 *
 * Why a context-based system rather than per-page inline toasts:
 *
 *   * **Survives navigation.** When the user deletes a project from
 *     the detail page, the previous version of the code did a
 *     ``navigate("/app?geloeschtes-projekt=...")`` hack so the
 *     dashboard could pick up the success message via a query
 *     parameter. With a global provider, the toast is in a context
 *     that persists across route changes — the originating page
 *     fires the toast, navigates, and the toast is still on screen
 *     when the destination page mounts.
 *   * **One viewport, one stacking model.** Pre-v23.6 each page
 *     had its own toast widget; two simultaneous actions on
 *     different pages could overlap. Single ``ToastViewport`` keeps
 *     the stack coherent.
 *   * **Type-safe.** ``useToast()`` returns a typed API so call
 *     sites can't accidentally fire an "info" toast where the
 *     UX wants "success". Three kinds: success / error / info.
 *
 * Lifecycle
 * ---------
 *
 *   * ``success`` defaults to a 3 s auto-dismiss (long enough to
 *     read, short enough not to linger).
 *   * ``error`` defaults to 5 s — errors deserve more attention
 *     and the user is more likely to want to read the full message.
 *   * ``info`` defaults to 2 s — usually a status nudge.
 *   * Pass ``{ dismissAfter: null }`` to make a toast sticky until
 *     the user dismisses it manually (rare; reserved for things
 *     like rate-limit errors where a 5 s blink would be
 *     under-served).
 *
 * Accessibility
 * -------------
 *
 *   * The viewport is ``role="region"`` with ``aria-live="polite"``
 *     — screen readers announce new toasts without interrupting
 *     the user's current activity.
 *   * Errors get ``aria-live="assertive"`` because their content
 *     is more time-sensitive (e.g. "save failed — your edit was
 *     not persisted").
 */
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { AlertTriangle, Check, Info, X } from "lucide-react";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  /** ms until auto-dismiss; ``null`` = sticky. */
  dismissAfter: number | null;
}

export interface ToastApi {
  success(message: string, opts?: { dismissAfter?: number | null }): void;
  error(message: string, opts?: { dismissAfter?: number | null }): void;
  info(message: string, opts?: { dismissAfter?: number | null }): void;
  /** Manually dismiss a toast by id. Mostly an internal hook for
   * the close button on each toast, but exported in case a caller
   * needs to dismiss programmatically. */
  dismiss(id: number): void;
}

const ToastContext = createContext<ToastApi | null>(null);

/**
 * Read the toast API. Throws if called outside the provider so
 * misconfigured trees fail loud (a silent no-op would hide real
 * bugs in production).
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error(
      "useToast must be used inside <ToastProvider>. Wrap the tree in main.tsx.",
    );
  }
  return ctx;
}

// Module-level counter for toast ids. Starts fresh on each
// page load — that's fine since we never persist toasts across
// reloads.
let nextId = 0;

const DEFAULT_DURATIONS: Record<ToastKind, number> = {
  success: 3000,
  error: 5000,
  info: 2000,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  // Keep timers in a ref so a re-render doesn't recreate them and
  // duplicate the auto-dismiss for each toast.
  const timersRef = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const handle = timersRef.current.get(id);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      timersRef.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (kind: ToastKind, message: string, dismissAfter: number | null) => {
      nextId += 1;
      const id = nextId;
      setToasts((prev) => [...prev, { id, kind, message, dismissAfter }]);
      if (dismissAfter !== null) {
        const handle = window.setTimeout(() => dismiss(id), dismissAfter);
        timersRef.current.set(id, handle);
      }
    },
    [dismiss],
  );

  // Cleanup timers on unmount so a hot-reload during dev doesn't
  // leak setTimeout callbacks pointing at stale state.
  useEffect(() => {
    return () => {
      timersRef.current.forEach((handle) => window.clearTimeout(handle));
      timersRef.current.clear();
    };
  }, []);

  // Stable API object — re-created on every render is fine in
  // practice (consumers re-read on each render anyway) but we
  // memoise via useCallback for ``push`` and ``dismiss`` so
  // children that capture the API object don't re-render
  // unnecessarily.
  const api: ToastApi = {
    success: (m, opts) =>
      push(
        "success",
        m,
        opts?.dismissAfter !== undefined
          ? opts.dismissAfter
          : DEFAULT_DURATIONS.success,
      ),
    error: (m, opts) =>
      push(
        "error",
        m,
        opts?.dismissAfter !== undefined
          ? opts.dismissAfter
          : DEFAULT_DURATIONS.error,
      ),
    info: (m, opts) =>
      push(
        "info",
        m,
        opts?.dismissAfter !== undefined
          ? opts.dismissAfter
          : DEFAULT_DURATIONS.info,
      ),
    dismiss,
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/**
 * Bottom-right stacking viewport. Newest toast at the top of the
 * stack so the user reads it first. Each toast is a card with an
 * icon + message + close button.
 */
function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      role="region"
      aria-label="Benachrichtigungen"
      className="pointer-events-none fixed bottom-6 right-6 z-50 flex w-[min(360px,calc(100vw-3rem))] flex-col-reverse gap-2"
    >
      {toasts.map((toast) => (
        <ToastCard
          key={toast.id}
          toast={toast}
          onDismiss={() => onDismiss(toast.id)}
        />
      ))}
    </div>
  );
}

const STYLES: Record<
  ToastKind,
  { bg: string; border: string; icon: ReactNode; ariaLive: "polite" | "assertive" }
> = {
  success: {
    bg: "bg-green-50 text-green-900",
    border: "border-green-200",
    icon: <Check className="h-4 w-4 text-green-700" />,
    ariaLive: "polite",
  },
  error: {
    bg: "bg-red-50 text-red-900",
    border: "border-red-200",
    icon: <AlertTriangle className="h-4 w-4 text-red-700" />,
    ariaLive: "assertive",
  },
  info: {
    bg: "bg-blue-50 text-blue-900",
    border: "border-blue-200",
    icon: <Info className="h-4 w-4 text-blue-700" />,
    ariaLive: "polite",
  },
};

function ToastCard({
  toast,
  onDismiss,
}: {
  toast: ToastItem;
  onDismiss: () => void;
}) {
  const style = STYLES[toast.kind];
  return (
    <div
      role="status"
      aria-live={style.ariaLive}
      className={`pointer-events-auto flex items-start gap-2 rounded-md border ${style.border} ${style.bg} px-3 py-2.5 text-sm shadow-lg`}
    >
      <span className="mt-0.5 shrink-0">{style.icon}</span>
      <span className="flex-1 break-words">{toast.message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Benachrichtigung schließen"
        className="shrink-0 rounded p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

/**
 * Diagnostic plumbing — so silent failures become impossible.
 *
 * ``installGlobalErrorHandlers`` wires the browser's top-level error
 * channels (``window.onerror`` via ``addEventListener('error')`` and
 * ``window.addEventListener('unhandledrejection')``) into a
 * subscribable store. Any uncaught exception or rejected promise ends
 * up in ``diagnosticErrors`` and a UI overlay (rendered by
 * ``ErrorOverlay`` — mounted once in ``App``) shows it in a red
 * banner the user cannot miss. The goal is: if anything goes wrong
 * anywhere in the frontend, the user sees *something* instead of a
 * dead button.
 *
 * ``installAxiosLogging`` attaches request/response/error interceptors
 * to the shared axios instance so every single API call logs its
 * method, URL, status, and elapsed time in the console. The user can
 * open DevTools → Console and share a screenshot for support without
 * needing network-panel replay.
 *
 * The overlay is the last line of defense. It should almost never
 * fire — component-level error states (ErrorBanner, per-row inline
 * errors) handle the common cases. If it *does* fire, something is
 * actually broken and we want the user to know.
 */

import type { AxiosInstance } from "axios";

export interface DiagnosticError {
  id: number;
  source: "error" | "unhandledrejection" | "manual";
  message: string;
  stack?: string;
  timestamp: number;
}

let nextId = 1;
const listeners = new Set<(errs: DiagnosticError[]) => void>();
let errors: DiagnosticError[] = [];

function notify() {
  // Cap so a storm doesn't leak memory.
  if (errors.length > 20) errors = errors.slice(-20);
  for (const l of listeners) l(errors);
}

export function subscribeDiagnostics(
  fn: (errs: DiagnosticError[]) => void
): () => void {
  listeners.add(fn);
  fn(errors);
  return () => listeners.delete(fn);
}

export function pushDiagnostic(
  source: DiagnosticError["source"],
  message: string,
  stack?: string
) {
  const entry: DiagnosticError = {
    id: nextId++,
    source,
    message,
    stack,
    timestamp: Date.now(),
  };
  errors = [...errors, entry];
  // Mirror to console so DevTools shows it with full fidelity.
  // eslint-disable-next-line no-console
  console.error("[diag:" + source + "]", message, stack ?? "");
  notify();
}

export function clearDiagnostic(id: number) {
  errors = errors.filter((e) => e.id !== id);
  notify();
}

export function clearAllDiagnostics() {
  errors = [];
  notify();
}

export function installGlobalErrorHandlers() {
  window.addEventListener("error", (ev: ErrorEvent) => {
    const msg = ev.message || "Uncaught error";
    const stack =
      ev.error && ev.error.stack
        ? String(ev.error.stack)
        : `${ev.filename}:${ev.lineno}:${ev.colno}`;
    pushDiagnostic("error", msg, stack);
  });

  window.addEventListener("unhandledrejection", (ev: PromiseRejectionEvent) => {
    const reason = ev.reason;
    let msg = "Unhandled promise rejection";
    let stack: string | undefined;
    if (reason instanceof Error) {
      msg = reason.message || msg;
      stack = reason.stack;
    } else if (typeof reason === "string") {
      msg = reason;
    } else {
      try {
        msg = JSON.stringify(reason);
      } catch {
        /* ignore */
      }
    }
    pushDiagnostic("unhandledrejection", msg, stack);
  });
}

/**
 * Attach request/response/error logging to the shared axios instance.
 * Logs method, URL, status, and duration. On error, logs the status
 * and ``detail`` body so support can diagnose from a console screenshot.
 */
export function installAxiosLogging(api: AxiosInstance) {
  // Piggyback on axios's config object to stash request start time.
  // Typed loosely because axios's InternalAxiosRequestConfig doesn't
  // include our ad-hoc field.
  api.interceptors.request.use((config) => {
    (config as unknown as { _startedAt?: number })._startedAt = Date.now();
    // eslint-disable-next-line no-console
    console.info(
      `[api →] ${(config.method || "GET").toUpperCase()} ${config.url}`
    );
    return config;
  });

  api.interceptors.response.use(
    (response) => {
      const started = (response.config as unknown as { _startedAt?: number })
        ._startedAt;
      const ms = started ? Date.now() - started : null;
      // eslint-disable-next-line no-console
      console.info(
        `[api ←] ${(response.config.method || "GET").toUpperCase()} ${response.config.url} ${response.status}` +
          (ms !== null ? ` (${ms}ms)` : "")
      );
      return response;
    },
    (error) => {
      const cfg = error.config ?? {};
      const started = (cfg as { _startedAt?: number })._startedAt;
      const ms = started ? Date.now() - started : null;
      const method = (cfg.method || "GET").toUpperCase();
      const url = cfg.url ?? "<no url>";
      const status = error.response?.status ?? "NETERR";
      const detail = error.response?.data?.detail;
      // eslint-disable-next-line no-console
      console.error(
        `[api ✗] ${method} ${url} ${status}` +
          (ms !== null ? ` (${ms}ms)` : "") +
          (detail ? ` — ${detail}` : ""),
        error
      );
      return Promise.reject(error);
    }
  );
}

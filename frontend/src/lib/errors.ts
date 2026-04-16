import type { AxiosError } from "axios";

/**
 * Normalize an unknown error (axios or otherwise) into a German
 * user-safe message plus the HTTP status code when available.
 *
 * The backend's policy is that every 4xx/5xx response includes a
 * German ``detail`` field — this helper surfaces it. Falls back to
 * generic German text only when nothing usable was returned.
 */
export interface NormalizedError {
  status: number | null;
  message: string;
}

function looksLikeAxios(e: unknown): e is AxiosError<{ detail?: string }> {
  return (
    typeof e === "object" &&
    e !== null &&
    "isAxiosError" in e &&
    (e as { isAxiosError: unknown }).isAxiosError === true
  );
}

export function normalizeError(e: unknown): NormalizedError {
  if (looksLikeAxios(e)) {
    const status = e.response?.status ?? null;
    const detail = e.response?.data?.detail;
    if (typeof detail === "string" && detail.length > 0) {
      return { status, message: detail };
    }
    if (status === null || e.code === "ERR_NETWORK") {
      return {
        status: null,
        message:
          "Verbindung zum Server fehlgeschlagen. Bitte prüfen Sie Ihre Internetverbindung.",
      };
    }
    if (status >= 500) {
      return {
        status,
        message:
          "Serverfehler. Bitte versuchen Sie es in einem Moment erneut.",
      };
    }
    return { status, message: `Fehler ${status}` };
  }
  if (e instanceof Error && e.message) {
    return { status: null, message: e.message };
  }
  return { status: null, message: "Ein unbekannter Fehler ist aufgetreten." };
}

/** True if this error represents "feature requires a higher plan". */
export function isUpgradeRequired(e: NormalizedError): boolean {
  return e.status === 403;
}

/**
 * Amber "Beta-Modus" banner shown at the very top of the authenticated
 * app when the server has BETA_UNLOCK_ALL_FEATURES=true.
 *
 * Source of truth is the backend: ``features.beta_unlock_active`` on
 * the /auth/me/features response. Not reading an env var here on
 * purpose — the SPA bundle is served from the same origin as the API,
 * so there is no way to flip the banner for a subset of users without
 * also flipping the gates; better to drive both from one server-side
 * signal.
 *
 * Does NOT render anywhere before auth has loaded (features is null)
 * and intentionally returns null when beta is off, so mounting it
 * unconditionally is free.
 */
import { Sparkles } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

export function BetaUnlockBanner() {
  const { features } = useAuth();
  if (!features?.beta_unlock_active) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex shrink-0 items-center justify-center gap-2 border-b border-amber-300 bg-amber-100 px-4 py-1.5 text-xs font-medium text-amber-900"
    >
      <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
      <span>🎉 Beta-Modus: Alle Pro-Funktionen freigeschaltet</span>
    </div>
  );
}

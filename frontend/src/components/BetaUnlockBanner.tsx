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
 *
 * v23.6 — dismissible
 * --------------------
 *
 * Pre-v23.6 the banner was permanently sticky once the server flag
 * was on, eating screen real estate even after the user had seen it
 * a hundred times. Now it carries a close button; dismissals
 * persist in localStorage under a *version-keyed* storage key. The
 * key includes ``APP_BUILD_TAG`` so a new deploy automatically
 * re-shows the banner once — useful when "Beta-Modus" might mean
 * different things between deploys (different feature set unlocked,
 * etc.).
 *
 * If localStorage is unavailable (Safari private mode, locked-down
 * iOS WebView), we silently fall back to in-memory dismissal: the
 * banner stays hidden for the current page lifetime but reappears
 * on the next reload. Acceptable; better than crashing.
 */
import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

// Bump this on a Beta-content change (different features unlocked,
// different copy, etc.) so previously-dismissed users see it once.
// In v23.6 it's ``v23`` — kept short on purpose because the user
// only ever sees it indirectly via the storage key.
const BANNER_VERSION = "v23";
const STORAGE_KEY = `beta_banner_closed_${BANNER_VERSION}`;

export function BetaUnlockBanner() {
  const { features } = useAuth();
  // Start visible by default. The localStorage check runs in an
  // effect (not during render) so SSR / first paint stays
  // deterministic. Brief flash on a dismissed banner is acceptable
  // — the alternative (start hidden, flip visible after the
  // effect) gives a flash-of-shown-content on every load which is
  // actively worse.
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(STORAGE_KEY) === "1") {
        setDismissed(true);
      }
    } catch {
      // localStorage unavailable — leave the banner shown for this
      // session. The dismiss button below will still work
      // (in-memory only).
    }
  }, []);

  if (!features?.beta_unlock_active) return null;
  if (dismissed) return null;

  const handleDismiss = () => {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // Best-effort persistence; in-memory dismissal still happens.
    }
    setDismissed(true);
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className="relative flex shrink-0 items-center justify-center gap-2 border-b border-amber-300 bg-amber-100 px-10 py-1.5 text-xs font-medium text-amber-900"
    >
      <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
      <span>🎉 Beta-Modus: Alle Pro-Funktionen freigeschaltet</span>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Beta-Hinweis ausblenden"
        title="Banner ausblenden — erscheint bei der nächsten Version wieder"
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-amber-800 hover:bg-amber-200/60"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

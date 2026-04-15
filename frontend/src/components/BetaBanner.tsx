import { useEffect, useState } from "react";
import { X, AlertTriangle } from "lucide-react";

const STORAGE_KEY = "baulv_beta_banner_dismissed";

/**
 * Dismissible beta warning banner. Shown on the landing page until the
 * visitor dismisses it; dismissal persists in localStorage so it doesn't
 * reappear on subsequent visits from the same browser.
 */
export function BetaBanner() {
  // Start hidden so we don't flash the banner on every SSR/first paint.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(STORAGE_KEY) !== "1") {
        setVisible(true);
      }
    } catch {
      // If localStorage is unavailable (e.g. Safari private mode) we still
      // show the banner — it's safer to warn visitors than to hide silently.
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // Ignore — non-persistent dismissal is still acceptable.
    }
    setVisible(false);
  };

  return (
    <div className="relative border-b border-yellow-300 bg-yellow-50 text-yellow-900">
      <div className="mx-auto flex max-w-6xl items-start gap-3 px-6 py-3 pr-12 text-sm">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="flex-1">
          <span aria-hidden="true">🚧 </span>
          <strong>Beta-Version</strong> — Diese Software befindet sich in der
          Testphase und ist noch nicht für den produktiven Einsatz freigegeben.
        </span>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Beta-Hinweis ausblenden"
        className="absolute right-3 top-1/2 -translate-y-1/2 rounded p-1 text-yellow-800 hover:bg-yellow-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

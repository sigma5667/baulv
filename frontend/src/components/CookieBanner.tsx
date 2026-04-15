import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Cookie } from "lucide-react";

const STORAGE_KEY = "baulv_cookie_consent";

type Consent = "all" | "necessary";

/**
 * Minimal DSGVO cookie banner.
 *
 * BauLV only uses strictly-necessary cookies (session/auth, CSRF) today —
 * no analytics, no tracking, no third-party marketing pixels. We still show
 * this banner because:
 *
 *   1. It is the user-visible proof that we ask for consent before loading
 *      any non-essential cookies in the future.
 *   2. It discloses the data-processing chain via a link to /datenschutz
 *      (required by Art. 13 DSGVO).
 *
 * The stored value is read at page load; any future code that wants to
 * load a non-essential cookie or script should call `hasFullConsent()`
 * first.
 */
export function hasFullConsent(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "all";
  } catch {
    return false;
  }
}

export function CookieBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored !== "all" && stored !== "necessary") {
        setVisible(true);
      }
    } catch {
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  const save = (choice: Consent) => {
    try {
      localStorage.setItem(STORAGE_KEY, choice);
    } catch {
      // Ignore — the banner will reappear next visit, which is acceptable.
    }
    setVisible(false);
  };

  return (
    <div
      role="dialog"
      aria-live="polite"
      aria-label="Cookie-Hinweis"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-white/95 shadow-lg backdrop-blur"
    >
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-4 text-sm md:flex-row md:items-center">
        <Cookie className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
        <p className="flex-1 text-muted-foreground">
          Diese Website verwendet Cookies, um Ihnen die bestmögliche Nutzung zu
          ermöglichen. Weitere Informationen finden Sie in unserer{" "}
          <Link
            to="/datenschutz"
            className="font-medium text-primary hover:underline"
          >
            Datenschutzerklärung
          </Link>
          .
        </p>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={() => save("necessary")}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            Nur notwendige
          </button>
          <button
            type="button"
            onClick={() => save("all")}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Alle akzeptieren
          </button>
        </div>
      </div>
    </div>
  );
}

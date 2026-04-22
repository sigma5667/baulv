import { useState, useEffect } from "react";
import { Download, X } from "lucide-react";

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

// Bumping the suffix resets the "dismissed" memory for everyone — do that
// if the prompt's copy/UX ever changes enough that prior dismissals should
// no longer apply.
const DISMISSED_KEY = "baulv_pwa_prompt_dismissed_v1";

/**
 * "Install as app" banner.
 *
 * Two UX bugs the previous version produced:
 *
 * 1. **Dismissal didn't persist.** ``dismissed`` lived in component
 *    state, so the moment the user refreshed the page the banner came
 *    back. Users got used to ignoring it, which ate real estate and —
 *    worse — the sidebar's "Abmelden" button sat directly behind the
 *    banner at ``bottom-4 left-4``, making logout unclickable on
 *    ``/app/profile`` and ``/app/subscription``. Dismissal now writes
 *    ``baulv_pwa_prompt_dismissed_v1`` to localStorage and we check it
 *    on every mount.
 *
 * 2. **Position collided with the sidebar.** ``bottom-4 left-4`` is
 *    exactly where the sidebar's bottom actions sit when the sidebar is
 *    open. We now center horizontally at the bottom so it's clear of
 *    both the sidebar (left) and the support chat launcher (right at
 *    ``z-[60]``).
 */
export function PWAInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] =
    useState<BeforeInstallPromptEvent | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(DISMISSED_KEY) === "1";
    } catch {
      // SSR / storage-disabled browsers — fall back to "not dismissed"
      // so the banner still shows once per session.
      return false;
    }
  });

  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  if (!deferredPrompt || dismissed) return null;

  const handleInstall = async () => {
    await deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === "accepted") {
      setDeferredPrompt(null);
      // Treat an accepted install as a permanent dismissal — the user
      // now has the installed app, the prompt has nothing left to offer.
      try {
        localStorage.setItem(DISMISSED_KEY, "1");
      } catch {
        /* ignore storage failures */
      }
    }
  };

  const handleDismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      /* ignore storage failures */
    }
  };

  // Bottom-centered so it's clear of the sidebar (bottom-left, contains
  // the Abmelden button) and the support chat launcher (bottom-right).
  // ``-translate-x-1/2`` with ``left-1/2`` gives a true centered anchor
  // independent of the banner's own width.
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-3 rounded-lg border bg-white px-4 py-3 shadow-lg">
      <Download className="h-5 w-5 shrink-0 text-primary" />
      <div className="text-sm">
        <p className="font-medium">BauLV als App installieren</p>
        <p className="text-muted-foreground">
          Zum Startbildschirm hinzufügen
        </p>
      </div>
      <button
        onClick={handleInstall}
        className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        Installieren
      </button>
      <button
        onClick={handleDismiss}
        aria-label="Hinweis schließen"
        className="text-muted-foreground hover:text-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

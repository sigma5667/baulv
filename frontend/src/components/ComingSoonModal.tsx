/**
 * ComingSoonModal — placeholder for the API-tier "Jetzt buchen"
 * buttons until the Stripe integration is live.
 *
 * The pricing buttons on ``/api-pricing`` open this modal rather
 * than a checkout flow. It surfaces the contact email, an explicit
 * "Early-Access"-Note, and a "mailto:" link the user can click to
 * pre-fill a request. Backdrop-click and Escape both close.
 *
 * Keeping the modal in its own file (rather than inlining on the
 * pricing page) means the developers page can reuse the same
 * surface for the "Enterprise contact" affordance without code
 * duplication.
 */
import { useEffect } from "react";
import { Mail, Sparkles, X } from "lucide-react";

export interface ComingSoonModalProps {
  /** Title shown at the top of the modal — usually mentions which
   * tier the user clicked. */
  title?: string;
  /** Explanatory body text. Falls back to a sensible German default
   * mentioning Stripe + Early-Access if omitted. */
  body?: string;
  /** Pre-filled subject for the contact mailto link. */
  mailSubject?: string;
  onClose: () => void;
}

const CONTACT_EMAIL = "kontakt@baulv.at";

export function ComingSoonModal({
  title = "API-Buchung kommt bald",
  body = (
    "Die Online-Buchung der API-Tarife (Bezahlung über Stripe) ist " +
    "in Vorbereitung. Bis dahin: schreibe uns für Early-Access — wir " +
    "antworten persönlich innerhalb von 24 Stunden und schalten dir " +
    "deinen API-Key manuell frei."
  ),
  mailSubject = "BauLV API — Early-Access-Anfrage",
  onClose,
}: ComingSoonModalProps) {
  // Escape closes. Listener mounted only while the modal is open
  // so the document-level overhead is zero between opens.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const mailto = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(mailSubject)}`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="coming-soon-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-primary/10 p-2">
            <Sparkles className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1">
            <h2 id="coming-soon-title" className="text-lg font-semibold">
              {title}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">{body}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Schließen"
            className="rounded p-1 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <a
          href={mailto}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Mail className="h-4 w-4" />
          Email an {CONTACT_EMAIL}
        </a>

        <p className="mt-3 text-center text-xs text-muted-foreground">
          Wir setzen uns innerhalb von 24 Stunden mit dir in Verbindung.
        </p>
      </div>
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Loader2 } from "lucide-react";
import { useAuth } from "../../hooks/useAuth";
import {
  INDUSTRY_LABELS,
  needsConsentRefresh,
  type IndustrySegment,
  type User,
} from "../../types/user";

/**
 * Modal that fires when an authenticated user's accepted legal-document
 * versions are stale relative to what the server currently serves
 * (v23.2, DSGVO Art. 7 evidence-renewal).
 *
 * Decision logic for "show or not" lives in
 * ``needsConsentRefresh(user)`` from ``types/user.ts``. NULL accepted
 * versions (grandfathered pre-v23.2 accounts) are treated as
 * "no prompt" — those users get covered by a separate retroactive-
 * consent campaign later.
 *
 * UX
 * --
 * - Non-dismissible (no X, no backdrop-click): the user must accept
 *   to continue using the app. This is the deliberate design — DSGVO
 *   needs evidence we *prevented* further use until consent renewed.
 * - Always shows three checkboxes (privacy, terms, marketing) like
 *   the registration form, so the user can also flip their
 *   marketing preference at the same moment if they want.
 * - Submit hits ``POST /auth/me/consent/refresh`` and on success the
 *   updated user reduces ``needsConsentRefresh`` to false; the
 *   parent component re-renders without the modal.
 */
export function ConsentRefreshModal({ user }: { user: User }) {
  const { refreshConsent } = useAuth();
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [marketingOptin, setMarketingOptin] = useState(
    user.marketing_email_opt_in,
  );
  // v23.8 — pre-fill from the user's current state so a user
  // who already opted in (e.g. on registration) doesn't have to
  // re-tick the box on every privacy-policy bump.
  const [analyticsConsent, setAnalyticsConsent] = useState(
    user.analytics_consent,
  );
  const [industrySegment, setIndustrySegment] = useState<
    IndustrySegment | ""
  >(user.industry_segment ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const privacyChanged =
    user.accepted_privacy_version !== null &&
    user.accepted_privacy_version !== user.required_privacy_version;
  const termsChanged =
    user.accepted_terms_version !== null &&
    user.accepted_terms_version !== user.required_terms_version;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!privacyAccepted || !termsAccepted) {
      setError(
        "Bitte bestätigen Sie sowohl Datenschutzerklärung als auch AGB, um BauLV weiter zu nutzen.",
      );
      return;
    }

    setSubmitting(true);
    try {
      await refreshConsent({
        accepted_privacy_version: user.required_privacy_version,
        accepted_terms_version: user.required_terms_version,
        marketing_optin: marketingOptin,
        // v23.8 — analytics state can change as part of the
        // refresh. Industry stays NULL when the user didn't
        // pick one OR analytics is off.
        analytics_consent: analyticsConsent,
        industry_segment:
          analyticsConsent && industrySegment ? industrySegment : null,
      });
      // On success the auth context updates the user in-place and
      // ``needsConsentRefresh`` flips to false, so the modal
      // unmounts on the parent's next render.
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
          "Akzeptieren fehlgeschlagen. Bitte später erneut versuchen.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="consent-refresh-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-lg rounded-lg border bg-card p-6 shadow-2xl">
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-full bg-amber-100 p-2">
            <AlertCircle className="h-5 w-5 text-amber-700" />
          </div>
          <div className="flex-1">
            <h2
              id="consent-refresh-title"
              className="text-lg font-semibold"
            >
              Aktualisierte Rechtsdokumente
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {privacyChanged && termsChanged
                ? "Unsere Datenschutzerklärung und unsere AGB wurden aktualisiert."
                : privacyChanged
                  ? "Unsere Datenschutzerklärung wurde aktualisiert."
                  : "Unsere AGB wurden aktualisiert."}{" "}
              Bitte prüfen Sie die geänderten Dokumente und bestätigen
              Sie erneut, um BauLV weiter zu nutzen.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-3 rounded-md border border-muted bg-muted/20 p-3">
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                required
                checked={privacyAccepted}
                onChange={(e) => setPrivacyAccepted(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">
                Ich habe die aktuelle{" "}
                <Link
                  to="/datenschutz"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  Datenschutzerklärung
                </Link>{" "}
                (Version {user.required_privacy_version}) gelesen und
                akzeptiert.{" "}
                <span className="text-destructive">*</span>
              </span>
            </label>

            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                required
                checked={termsAccepted}
                onChange={(e) => setTermsAccepted(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">
                Ich habe die aktuellen{" "}
                <Link
                  to="/agb"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  AGB
                </Link>{" "}
                (Version {user.required_terms_version}) gelesen und
                akzeptiert. <span className="text-destructive">*</span>
              </span>
            </label>

            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={marketingOptin}
                onChange={(e) => setMarketingOptin(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">
                Ich möchte gelegentlich Newsletter über neue Features
                erhalten. <span className="text-xs">(optional)</span>
              </span>
            </label>

            {/* v23.8 — anonymised-analytics opt-in. Pre-filled from
                the user's existing state so re-acceptance doesn't
                lose the previous choice. */}
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={analyticsConsent}
                onChange={(e) => {
                  setAnalyticsConsent(e.target.checked);
                  if (!e.target.checked) setIndustrySegment("");
                }}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">
                Anonymisierte Nutzungsdaten zur Produkt-Verbesserung.{" "}
                <span className="text-xs">
                  (optional, jederzeit widerrufbar)
                </span>
              </span>
            </label>
            {analyticsConsent && (
              <div className="ml-6">
                <label className="block text-xs text-muted-foreground">
                  Branche{" "}
                  <span className="text-muted-foreground/60">
                    (optional)
                  </span>
                </label>
                <select
                  value={industrySegment}
                  onChange={(e) =>
                    setIndustrySegment(
                      e.target.value as IndustrySegment | "",
                    )
                  }
                  className="mt-1 w-full rounded-md border bg-background px-2 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">— bitte wählen —</option>
                  {(
                    Object.entries(INDUSTRY_LABELS) as [
                      IndustrySegment,
                      string,
                    ][]
                  ).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={
              submitting || !privacyAccepted || !termsAccepted
            }
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Akzeptiere…" : "Akzeptieren und fortfahren"}
          </button>
        </form>
      </div>
    </div>
  );
}

/** Convenience wrapper for ``AppShell`` and similar — renders the
 * modal only when the current user actually needs to refresh.
 * Returns null otherwise so it's safe to drop into the JSX tree
 * unconditionally. */
export function ConsentRefreshGate() {
  const { user } = useAuth();
  if (!user) return null;
  if (!needsConsentRefresh(user)) return null;
  return <ConsentRefreshModal user={user} />;
}

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Building2, UserPlus, Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { fetchLegalVersions, type LegalVersions } from "../api/auth";
import {
  INDUSTRY_LABELS,
  type IndustrySegment,
} from "../types/user";

/**
 * Sign-up page with DSGVO Art. 7-compliant consent capture (v23.2).
 *
 * Three checkboxes, two of them mandatory:
 *
 *   1. Privacy policy acceptance — required.
 *   2. Terms of service acceptance — required.
 *   3. Marketing-email opt-in — optional, default OFF (Art. 7's
 *      "clear affirmative action" — defaults must be unchecked).
 *
 * The page fetches the canonical legal-document versions from
 * ``GET /auth/legal/versions`` on mount and ships them back in the
 * register payload. The backend re-checks them against
 * ``app/legal_versions.py`` and rejects with 409 on mismatch — so a
 * stale tab can't sneak a user in under outdated text.
 */
export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    email: "",
    password: "",
    confirmPassword: "",
    full_name: "",
    company_name: "",
  });
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [marketingOptin, setMarketingOptin] = useState(false);
  // v23.8 — anonymised analytics opt-in (default OFF per DSGVO
  // Art. 7 "clear affirmative action"). When the user ticks the
  // checkbox, the industry-segment dropdown appears below; we
  // only ship a non-null segment to the backend if the user
  // actually chose one (omitted = NULL = "didn't pick").
  const [analyticsConsent, setAnalyticsConsent] = useState(false);
  const [industrySegment, setIndustrySegment] = useState<
    IndustrySegment | ""
  >("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [legal, setLegal] = useState<LegalVersions | null>(null);

  useEffect(() => {
    // Anonymous endpoint — no auth required. We don't block render
    // on this; if it fails, the form still works (fallback labels
    // omit the version + date suffix).
    fetchLegalVersions()
      .then(setLegal)
      .catch(() => {
        // Silently fall back — backend may be temporarily down. The
        // checkboxes still render generic labels.
      });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (form.password.length < 8) {
      setError("Das Passwort muss mindestens 8 Zeichen lang sein.");
      return;
    }
    if (form.password !== form.confirmPassword) {
      setError("Die Passwörter stimmen nicht überein.");
      return;
    }
    if (!privacyAccepted || !termsAccepted) {
      setError(
        "Bitte akzeptieren Sie sowohl die Datenschutzerklärung als auch die AGB.",
      );
      return;
    }
    if (!legal) {
      setError(
        "Die aktuellen Rechtsdokument-Versionen konnten nicht geladen werden. Bitte Seite neu laden.",
      );
      return;
    }

    setLoading(true);
    try {
      await register({
        email: form.email,
        password: form.password,
        full_name: form.full_name,
        company_name: form.company_name || undefined,
        accepted_privacy_version: legal.privacy_version,
        accepted_terms_version: legal.terms_version,
        marketing_optin: marketingOptin,
        // v23.8 — only send a non-null industry when the user
        // both opted in and picked an option. ``"" || null``
        // collapses to null which the backend treats as
        // "user didn't pick".
        analytics_consent: analyticsConsent,
        industry_segment:
          analyticsConsent && industrySegment ? industrySegment : null,
      });
      navigate("/app");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Registrierung fehlgeschlagen.");
    } finally {
      setLoading(false);
    }
  };

  const update = (field: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [field]: e.target.value });

  // Format "Version 1.0 vom 27.04.2026" for the checkbox sublabels.
  // Falls back to just the link text if legal versions haven't loaded.
  const privacySuffix = legal
    ? ` (Version ${legal.privacy_version} vom ${formatDateDe(legal.privacy_date)})`
    : "";
  const termsSuffix = legal
    ? ` (Version ${legal.terms_version} vom ${formatDateDe(legal.terms_date)})`
    : "";

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 py-8">
      <div className="w-full max-w-md rounded-xl border bg-white p-8 shadow-lg">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">
            Erstellen Sie Ihr kostenloses Konto
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">
              Vollständiger Name *
            </label>
            <input
              type="text"
              required
              value={form.full_name}
              onChange={update("full_name")}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Max Mustermann"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Firmenname</label>
            <input
              type="text"
              value={form.company_name}
              onChange={update("company_name")}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Musterbau GmbH"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">E-Mail *</label>
            <input
              type="email"
              required
              value={form.email}
              onChange={update("email")}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="ihre@email.at"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">
              Passwort * (min. 8 Zeichen)
            </label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                required
                value={form.password}
                onChange={update("password")}
                className="w-full rounded-md border px-3 py-2 pr-10 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-2.5 text-muted-foreground hover:text-foreground"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">
              Passwort bestätigen *
            </label>
            <input
              type="password"
              required
              value={form.confirmPassword}
              onChange={update("confirmPassword")}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="••••••••"
            />
          </div>

          {/* Three consent checkboxes (v23.2 — DSGVO Art. 7).
              The two mandatory ones are wired to the submit-disable
              logic via privacyAccepted && termsAccepted. The
              marketing one is optional and defaults to false per
              Art. 7's "clear affirmative action" requirement
              (defaults must be unchecked). */}
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
                Ich habe die{" "}
                <Link
                  to="/datenschutz"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  Datenschutzerklärung
                </Link>
                {privacySuffix} gelesen und akzeptiert. <span className="text-destructive">*</span>
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
                Ich habe die{" "}
                <Link
                  to="/agb"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  AGB
                </Link>
                {termsSuffix} gelesen und akzeptiert. <span className="text-destructive">*</span>
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

            {/* v23.8 — anonymised analytics opt-in. Default OFF
                per DSGVO Art. 7 ("clear affirmative action"). The
                industry dropdown appears only after the user ticks
                the box, since the segment is only meaningful when
                analytics is on. The user can toggle this later in
                /app/settings/datenschutz at any time. */}
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={analyticsConsent}
                onChange={(e) => {
                  setAnalyticsConsent(e.target.checked);
                  if (!e.target.checked) {
                    // Clear the industry choice when the user
                    // un-checks — keeping the dropdown visible
                    // would imply we still record the segment.
                    setIndustrySegment("");
                  }
                }}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">
                Ich bin damit einverstanden, dass anonymisierte
                Nutzungsdaten zur Produkt-Verbesserung gesammelt
                werden.{" "}
                <span className="text-xs">
                  (optional, jederzeit widerrufbar)
                </span>
              </span>
            </label>

            {analyticsConsent && (
              <div className="ml-6 mt-1">
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
              loading || !privacyAccepted || !termsAccepted || legal === null
            }
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            {loading ? "Registrieren..." : "Konto erstellen"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Bereits ein Konto?{" "}
          <Link to="/login" className="font-medium text-primary hover:underline">
            Anmelden
          </Link>
        </p>
      </div>
    </div>
  );
}

/** ISO date string (YYYY-MM-DD) → German short form (DD.MM.YYYY).
 * Falls back to the original string on parse failure rather than
 * crashing the form render. */
function formatDateDe(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${m[3]}.${m[2]}.${m[1]}`;
}

/**
 * Passwort-Vergessen-Seite (DS-3, v23.4).
 *
 * Replaces the old `/password-reset` page that just *displayed* the
 * "if such an account exists…" message without a working backend.
 * Now wired to the functional `POST /api/auth/password-reset` flow:
 * email goes in, generic 200-OK message comes back regardless of
 * whether the account actually exists.
 *
 * The "always show success" behaviour is deliberate — the backend
 * returns the same message in success / no-account / rate-limited
 * cases to avoid leaking account existence to a probing attacker,
 * and the page must mirror that promise. Any error from the API
 * (network, 500) still flips into the success view because the
 * alternative — surfacing the error — would let an attacker
 * distinguish "your input was rejected" from "your input was
 * accepted", which is itself a side channel.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { Building2, Mail } from "lucide-react";
import { requestPasswordReset } from "../api/auth";

export function PasswortVergessenPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      // Intentionally fire-and-forget. We don't differentiate
      // between success and failure on the UI — the backend
      // returns the same generic message either way, and a
      // network error here is treated the same so the response
      // shape doesn't betray account existence.
      await requestPasswordReset(email);
    } catch {
      // Swallow — see comment above. The success view fires
      // regardless to keep the user-facing surface uniform.
    } finally {
      setSent(true);
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-4 py-8">
      <div className="w-full max-w-md rounded-xl border bg-white p-6 shadow-lg sm:p-8">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">
            Passwort zurücksetzen
          </p>
        </div>

        {sent ? (
          <div className="text-center">
            <Mail className="mx-auto h-12 w-12 text-primary" />
            <h3 className="mt-4 text-lg font-medium">E-Mail gesendet</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Falls ein Konto mit dieser E-Mail-Adresse existiert,
              haben wir Ihnen eine E-Mail mit Anweisungen zum
              Zurücksetzen Ihres Passworts gesendet. Der Link ist
              1 Stunde gültig.
            </p>
            <p className="mt-3 text-xs text-muted-foreground">
              Bitte prüfen Sie auch Ihren Spam-Ordner.
            </p>
            <Link
              to="/login"
              className="mt-6 inline-block text-sm font-medium text-primary hover:underline"
            >
              Zurück zur Anmeldung
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Geben Sie Ihre E-Mail-Adresse ein. Wir senden Ihnen
              einen Link, mit dem Sie ein neues Passwort vergeben
              können.
            </p>
            <div>
              <label
                htmlFor="passwort-vergessen-email"
                className="mb-1 block text-sm font-medium"
              >
                E-Mail
              </label>
              <input
                id="passwort-vergessen-email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                placeholder="ihre@email.at"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {loading ? "Senden..." : "Link senden"}
            </button>
            <p className="text-center text-sm">
              <Link to="/login" className="text-primary hover:underline">
                Zurück zur Anmeldung
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}

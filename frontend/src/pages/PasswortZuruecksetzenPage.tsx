/**
 * Passwort-Zuruecksetzen-Seite (DS-3, v23.4).
 *
 * Reads the reset token from `?token=...`, asks the user for a new
 * password (twice), and POSTs to `/auth/password-reset/confirm`.
 *
 * Failure-mode handling
 * ---------------------
 *
 * The backend collapses every "this token can't be used" reason
 * (unknown / expired / already used) into a single 400 with a
 * generic German message. The page surfaces that message verbatim,
 * with a "Neue E-Mail anfordern" link that bounces back to
 * `/passwort-vergessen` so the user has a way out.
 *
 * Password-min-length is checked client-side (8 chars) for instant
 * feedback, but the server enforces it independently — never trust
 * the client to gate-keep an auth-relevant invariant.
 *
 * On success
 * ----------
 *
 * The server has already revoked every session for this user. We
 * navigate to `/login?passwort-zurueckgesetzt=1` so the login page
 * can render a "Passwort wurde zurückgesetzt, bitte erneut anmelden"
 * banner. The user types their new password to log in fresh.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Building2, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { confirmPasswordReset } from "../api/auth";

export function PasswortZuruecksetzenPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Read once per mount. ``useMemo`` so we don't re-derive on every
  // render — the URL doesn't change for the lifetime of this page.
  const token = useMemo(
    () => searchParams.get("token") ?? "",
    [searchParams],
  );

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [missingToken, setMissingToken] = useState(false);

  // Token-presence check up-front. If the user landed here without
  // ?token=... we surface the same generic message as a server-side
  // rejection — saves a round-trip and keeps the failure surface
  // uniform.
  useEffect(() => {
    if (!token) {
      setMissingToken(true);
    }
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError("Das Passwort muss mindestens 8 Zeichen lang sein.");
      return;
    }
    if (password !== confirm) {
      setError("Die beiden Passwörter stimmen nicht überein.");
      return;
    }

    setLoading(true);
    try {
      await confirmPasswordReset({ token, new_password: password });
      // Success — every session for this user has been revoked
      // server-side. Bounce to login with a flag so the login
      // page can render a confirmation banner.
      navigate("/login?passwort-zurueckgesetzt=1", { replace: true });
    } catch (err: any) {
      // The backend returns a single generic message for every
      // token failure mode. Surface it as-is; if the network
      // failed (no `response`), fall back to a sensible default.
      setError(
        err?.response?.data?.detail ??
          "Der Link ist ungültig oder abgelaufen. Bitte fordern Sie eine neue E-Mail an.",
      );
    } finally {
      setLoading(false);
    }
  };

  if (missingToken) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-4 py-8">
        <div className="w-full max-w-md rounded-xl border bg-white p-6 shadow-lg sm:p-8">
          <div className="mb-6 flex flex-col items-center">
            <Link to="/" className="flex items-center gap-2 text-primary mb-2">
              <Building2 className="h-8 w-8" />
              <span className="text-2xl font-bold">BauLV</span>
            </Link>
          </div>
          <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Der Link ist unvollständig oder ungültig. Bitte fordern
            Sie eine neue E-Mail zum Zurücksetzen an.
          </div>
          <Link
            to="/passwort-vergessen"
            className="mt-6 inline-block text-sm font-medium text-primary hover:underline"
          >
            Neue E-Mail anfordern
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-4 py-8">
      <div className="w-full max-w-md rounded-xl border bg-white p-6 shadow-lg sm:p-8">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">
            Neues Passwort vergeben
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
            {error.includes("ungültig") || error.includes("abgelaufen") ? (
              <div className="mt-2">
                <Link
                  to="/passwort-vergessen"
                  className="font-medium underline hover:no-underline"
                >
                  Neue E-Mail anfordern
                </Link>
              </div>
            ) : null}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Wählen Sie ein neues Passwort. Aus Sicherheitsgründen
            werden alle anderen Sitzungen Ihres Kontos beendet.
          </p>
          <div>
            <label
              htmlFor="passwort-neu"
              className="mb-1 block text-sm font-medium"
            >
              Neues Passwort
            </label>
            <div className="relative">
              <input
                id="passwort-neu"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border px-3 py-2 pr-10 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                placeholder="••••••••"
              />
              <button
                type="button"
                aria-label={
                  showPassword ? "Passwort verbergen" : "Passwort anzeigen"
                }
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
            <p className="mt-1 text-xs text-muted-foreground">
              Mindestens 8 Zeichen.
            </p>
          </div>
          <div>
            <label
              htmlFor="passwort-bestaetigung"
              className="mb-1 block text-sm font-medium"
            >
              Passwort bestätigen
            </label>
            <input
              id="passwort-bestaetigung"
              type={showPassword ? "text" : "password"}
              required
              autoComplete="new-password"
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="••••••••"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <ShieldCheck className="h-4 w-4" />
            {loading ? "Speichern..." : "Passwort speichern"}
          </button>
          <p className="text-center text-sm">
            <Link to="/login" className="text-primary hover:underline">
              Zurück zur Anmeldung
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}

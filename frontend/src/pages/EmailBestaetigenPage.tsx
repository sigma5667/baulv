/**
 * E-Mail-Bestätigung für neue Konten (Audit 2026-11).
 *
 * Liest das Token aus `?token=...` und löst es erst auf Button-Klick
 * ein — wie bei der Warteliste, weil Firmen-Mail-Scanner jeden Link
 * abrufen und sonst stellvertretend bestätigen würden.
 *
 * Route liegt AUSSERHALB des Beta-Gates: der Link kommt aus einer
 * E-Mail und muss in jedem Browser funktionieren.
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Building2, MailCheck } from "lucide-react";
import { verifyEmail } from "../api/auth";
import { useAuth } from "../hooks/useAuth";

export function EmailBestaetigenPage() {
  const [searchParams] = useSearchParams();
  const token = useMemo(
    () => searchParams.get("token") ?? "",
    [searchParams],
  );
  const { user, refreshUser } = useAuth();

  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const handleConfirm = async () => {
    setError("");
    setLoading(true);
    try {
      await verifyEmail(token);
      setDone(true);
      if (user) await refreshUser();
    } catch (err: any) {
      setError(
        typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "Der Bestätigungs-Link ist ungültig oder abgelaufen.",
      );
    } finally {
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
            E-Mail-Adresse bestätigen
          </p>
        </div>

        {!token ? (
          <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Der Link ist unvollständig oder ungültig. Nach dem Login
            können Sie eine neue Bestätigungs-Mail anfordern.
          </div>
        ) : done ? (
          <div className="space-y-4">
            <div className="rounded-md bg-green-50 px-4 py-3 text-sm text-green-800">
              E-Mail-Adresse bestätigt. Die KI-Funktionen sind jetzt
              freigeschaltet.
            </div>
            <Link
              to={user ? "/app" : "/login"}
              className="inline-block text-sm font-medium text-primary hover:underline"
            >
              {user ? "Zur App" : "Zum Login"}
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
                {error}
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              Klicken Sie auf den Button, um die E-Mail-Adresse Ihres
              BauLV-Kontos zu bestätigen.
            </p>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <MailCheck className="h-4 w-4" />
              {loading ? "Wird bestätigt..." : "E-Mail-Adresse bestätigen"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

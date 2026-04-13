import { useState } from "react";
import { Link } from "react-router-dom";
import { Building2, Mail } from "lucide-react";
import { requestPasswordReset } from "../api/auth";

export function PasswordResetPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100">
      <div className="w-full max-w-md rounded-xl border bg-white p-8 shadow-lg">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">Passwort zurücksetzen</p>
        </div>

        {sent ? (
          <div className="text-center">
            <Mail className="mx-auto h-12 w-12 text-primary" />
            <h3 className="mt-4 text-lg font-medium">E-Mail gesendet</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Falls ein Konto mit dieser E-Mail-Adresse existiert, haben wir Ihnen eine E-Mail mit
              Anweisungen zum Zurücksetzen Ihres Passworts gesendet.
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
              Geben Sie Ihre E-Mail-Adresse ein und wir senden Ihnen einen Link zum Zurücksetzen
              Ihres Passworts.
            </p>
            <div>
              <label className="mb-1 block text-sm font-medium">E-Mail</label>
              <input
                type="email"
                required
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

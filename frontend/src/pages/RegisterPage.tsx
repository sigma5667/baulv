import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Building2, UserPlus, Eye, EyeOff } from "lucide-react";
import { useAuth } from "../hooks/useAuth";

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
  const [agbAccepted, setAgbAccepted] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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
    if (!agbAccepted) {
      setError(
        "Bitte akzeptieren Sie die AGB und bestätigen Sie, dass Sie die Datenschutzerklärung gelesen haben."
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 py-8">
      <div className="w-full max-w-md rounded-xl border bg-white p-8 shadow-lg">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">Erstellen Sie Ihr kostenloses Konto</p>
        </div>

        {error && (
          <div className="mb-4 rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Vollständiger Name *</label>
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
            <label className="mb-1 block text-sm font-medium">Passwort * (min. 8 Zeichen)</label>
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
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Passwort bestätigen *</label>
            <input
              type="password"
              required
              value={form.confirmPassword}
              onChange={update("confirmPassword")}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="••••••••"
            />
          </div>

          {/* AGB / Datenschutz acceptance — required by DSGVO and Austrian law */}
          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="checkbox"
              required
              checked={agbAccepted}
              onChange={(e) => setAgbAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus:ring-primary"
            />
            <span className="text-muted-foreground">
              Ich akzeptiere die{" "}
              <Link
                to="/agb"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary hover:underline"
              >
                AGB
              </Link>{" "}
              und habe die{" "}
              <Link
                to="/datenschutz"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary hover:underline"
              >
                Datenschutzerklärung
              </Link>{" "}
              gelesen.
            </span>
          </label>

          <button
            type="submit"
            disabled={loading || !agbAccepted}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <UserPlus className="h-4 w-4" />
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

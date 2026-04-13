import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { User as UserIcon, Save } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { updateProfile } from "../api/auth";

export function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const [form, setForm] = useState({
    full_name: user?.full_name ?? "",
    company_name: user?.company_name ?? "",
  });
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: () => updateProfile(form),
    onSuccess: () => {
      refreshUser();
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    },
  });

  const PLAN_LABELS: Record<string, string> = {
    basis: "Basis",
    pro: "Pro",
    enterprise: "Enterprise",
  };

  return (
    <div className="p-6 max-w-2xl">
      <div className="mb-6 flex items-center gap-3">
        <UserIcon className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Profil</h1>
      </div>

      <div className="rounded-lg border bg-card p-6 space-y-6">
        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">E-Mail</label>
          <p className="text-sm">{user?.email}</p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">Aktueller Plan</label>
          <span className="inline-block rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary">
            {PLAN_LABELS[user?.subscription_plan ?? "basis"]}
          </span>
        </div>

        <hr />

        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          <div>
            <label className="mb-1 block text-sm font-medium">Vollständiger Name</label>
            <input
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Firmenname</label>
            <input
              value={form.company_name}
              onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              className="w-full rounded-md border px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          {success && (
            <div className="rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
              Profil erfolgreich aktualisiert.
            </div>
          )}
          {mutation.isError && (
            <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
              Fehler beim Speichern.
            </div>
          )}

          <button
            type="submit"
            disabled={mutation.isPending}
            className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <Save className="h-4 w-4" />
            {mutation.isPending ? "Speichern..." : "Speichern"}
          </button>
        </form>

        <hr />

        <div>
          <label className="mb-1 block text-sm font-medium text-muted-foreground">Mitglied seit</label>
          <p className="text-sm">
            {user?.created_at ? new Date(user.created_at).toLocaleDateString("de-AT") : "–"}
          </p>
        </div>
      </div>
    </div>
  );
}

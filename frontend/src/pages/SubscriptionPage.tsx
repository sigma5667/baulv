import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CreditCard, Check, ArrowRight, ExternalLink } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { createCheckoutSession, createPortalSession } from "../api/stripe";

const PLANS = [
  {
    id: "basis",
    name: "Basis",
    price: "Kostenlos",
    interval: "",
    features: [
      "3 aktive Projekte",
      "ÖNORM-Bibliothek",
      "Manueller LV-Editor",
      "PDF-Export",
    ],
    excluded: [
      "KI-Plananalyse",
      "KI-Positionsgenerierung",
      "KI-Chatassistent",
      "Excel-Export",
      "Angebotsvergleich",
      "Team / Multi-User",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: "Kostenlos",
    interval: "",
    popular: true,
    features: [
      "Unbegrenzte Projekte",
      "Alles aus Basis, plus:",
      "KI-Plananalyse (PDF → Raum-Extraktion)",
      "KI-generierte LV-Positionen",
      "Excel + PDF Export",
      "KI-Chatassistent",
      "Prioritäts-Support",
    ],
    excluded: [
      "Angebotsvergleich / Preisspiegel",
      "Team / Multi-User",
      "API-Zugang",
    ],
  },
  {
    id: "enterprise",
    name: "Enterprise",
    price: "Kostenlos",
    interval: "",
    features: [
      "Alles aus Pro, plus:",
      "Angebotsvergleich / Preisspiegel",
      "Team- und Multi-User-Konten",
      "Individuelle ÖNORM-Konfiguration",
      "API-Zugang",
      "Dedizierter Support",
    ],
    excluded: [],
  },
];

export function SubscriptionPage() {
  const { user, refreshUser } = useAuth();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState<string | null>(null);

  const success = searchParams.get("success") === "true";
  const canceled = searchParams.get("canceled") === "true";

  if (success) {
    refreshUser();
  }

  const handleCheckout = async (planId: string) => {
    if (planId === "enterprise") {
      window.location.href = "mailto:kontakt@baulv.at?subject=Enterprise-Plan%20Anfrage";
      return;
    }
    setLoading(planId);
    try {
      const { checkout_url } = await createCheckoutSession(planId);
      window.location.href = checkout_url;
    } catch (err: any) {
      alert(err.response?.data?.detail || "Fehler beim Erstellen der Checkout-Session.");
    } finally {
      setLoading(null);
    }
  };

  const handlePortal = async () => {
    try {
      const { portal_url } = await createPortalSession();
      window.location.href = portal_url;
    } catch (err: any) {
      alert(err.response?.data?.detail || "Fehler beim Öffnen des Kundenportals.");
    }
  };

  const currentPlan = user?.subscription_plan ?? "basis";

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center gap-3">
        <CreditCard className="h-6 w-6 text-primary" />
        <h1 className="text-2xl font-bold">Abonnement</h1>
      </div>

      {success && (
        <div className="mb-6 rounded-md bg-green-50 px-4 py-3 text-sm text-green-700">
          Ihr Abonnement wurde erfolgreich aktualisiert!
        </div>
      )}
      {canceled && (
        <div className="mb-6 rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-700">
          Der Checkout-Vorgang wurde abgebrochen.
        </div>
      )}

      {/* Current plan info */}
      <div className="mb-8 rounded-lg border bg-card p-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-muted-foreground">Aktueller Plan</p>
            <p className="text-lg font-semibold">
              {PLANS.find((p) => p.id === currentPlan)?.name ?? "Basis"}
            </p>
          </div>
          {user?.stripe_customer_id && (
            <button
              onClick={handlePortal}
              className="flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
            >
              <ExternalLink className="h-4 w-4" />
              Rechnungen & Zahlungen verwalten
            </button>
          )}
        </div>
      </div>

      {/* Plan cards */}
      <div className="grid gap-6 md:grid-cols-3">
        {PLANS.map((plan) => {
          const isCurrent = plan.id === currentPlan;
          return (
            <div
              key={plan.id}
              className={`relative rounded-xl border p-6 ${
                plan.popular ? "border-primary shadow-lg ring-2 ring-primary/20" : ""
              }`}
            >
              {plan.popular && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground">
                  Beliebt
                </span>
              )}

              <h3 className="text-lg font-bold">{plan.name}</h3>
              <div className="mt-2 mb-4">
                {plan.price === "Kostenlos" ? (
                  <span className="text-3xl font-bold text-green-600">Kostenlos</span>
                ) : (
                  <>
                    <span className="text-3xl font-bold">€{plan.price}</span>
                    <span className="text-muted-foreground">/{plan.interval}</span>
                  </>
                )}
              </div>

              <ul className="mb-6 space-y-2">
                {plan.features.map((f) => (
                  <li key={f} className="flex items-start gap-2 text-sm">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                    {f}
                  </li>
                ))}
                {plan.excluded.map((f) => (
                  <li key={f} className="flex items-start gap-2 text-sm text-muted-foreground line-through">
                    <span className="mt-0.5 h-4 w-4 shrink-0 text-center">–</span>
                    {f}
                  </li>
                ))}
              </ul>

              {isCurrent ? (
                <div className="rounded-md bg-muted px-4 py-2.5 text-center text-sm font-medium text-muted-foreground">
                  Aktueller Plan
                </div>
              ) : plan.id === "enterprise" ? (
                <button
                  onClick={() => handleCheckout("enterprise")}
                  className="flex w-full items-center justify-center gap-2 rounded-md border border-primary px-4 py-2.5 text-sm font-medium text-primary hover:bg-primary/5"
                >
                  Kontakt aufnehmen
                  <ArrowRight className="h-4 w-4" />
                </button>
              ) : (
                <button
                  onClick={() => handleCheckout(plan.id)}
                  disabled={loading === plan.id}
                  className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {loading === plan.id ? "Weiterleitung..." : "Jetzt upgraden"}
                  <ArrowRight className="h-4 w-4" />
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

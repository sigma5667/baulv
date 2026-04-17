import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CreditCard,
  ExternalLink,
  Mail,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import { createCheckoutSession, createPortalSession } from "../api/stripe";
import { normalizeError } from "../lib/errors";

// Support contact. Pinned as a constant so the "Stripe not configured"
// fallback and any future "contact us" banner point at the same inbox.
const SUPPORT_EMAIL = "kontakt@baulv.at";

const PLANS = [
  {
    id: "basis",
    name: "Basis",
    price: "49",
    interval: "Monat",
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
    price: "149",
    interval: "Monat",
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
    price: "Auf Anfrage",
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

/**
 * Two kinds of error we want to distinguish visually:
 *
 * - ``unavailable``: Stripe is not configured on the backend (503). We
 *   show a dedicated amber panel with a ``mailto:`` CTA so the user
 *   can still reach us. Fixing this would need an env var on
 *   Railway, not a code change — the UI must not pretend otherwise.
 *
 * - ``error``: anything else (401, 500, network, etc.). Shown as a
 *   standard red banner with the backend's German ``detail``.
 */
type CheckoutState =
  | { kind: "idle" }
  | { kind: "unavailable"; message: string }
  | { kind: "error"; message: string };

export function SubscriptionPage() {
  const { user, refreshUser } = useAuth();
  const [searchParams] = useSearchParams();
  const [loadingPlan, setLoadingPlan] = useState<string | null>(null);
  const [checkoutState, setCheckoutState] = useState<CheckoutState>({
    kind: "idle",
  });

  const success = searchParams.get("success") === "true";
  const canceled = searchParams.get("canceled") === "true";

  if (success) {
    refreshUser();
  }

  const handleCheckout = async (planId: string) => {
    if (planId === "enterprise") {
      window.location.href = `mailto:${SUPPORT_EMAIL}?subject=Enterprise-Plan%20Anfrage`;
      return;
    }
    setLoadingPlan(planId);
    setCheckoutState({ kind: "idle" });
    try {
      const { checkout_url } = await createCheckoutSession(planId);
      window.location.href = checkout_url;
    } catch (err) {
      const norm = normalizeError(err);
      // 503 is what the backend returns when STRIPE_SECRET_KEY or the
      // per-plan price IDs are missing. Show the exact fallback copy
      // the business asked for instead of a cryptic "Fehler 503".
      if (norm.status === 503) {
        setCheckoutState({
          kind: "unavailable",
          message: `Upgrade momentan nicht verfügbar. Bitte kontaktieren Sie uns unter ${SUPPORT_EMAIL}.`,
        });
      } else {
        setCheckoutState({ kind: "error", message: norm.message });
      }
    } finally {
      setLoadingPlan(null);
    }
  };

  const handlePortal = async () => {
    setCheckoutState({ kind: "idle" });
    try {
      const { portal_url } = await createPortalSession();
      window.location.href = portal_url;
    } catch (err) {
      const norm = normalizeError(err);
      if (norm.status === 503) {
        setCheckoutState({
          kind: "unavailable",
          message: `Das Kundenportal ist momentan nicht verfügbar. Bitte kontaktieren Sie uns unter ${SUPPORT_EMAIL}.`,
        });
      } else {
        setCheckoutState({ kind: "error", message: norm.message });
      }
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

      {checkoutState.kind === "unavailable" && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-4"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div className="flex-1 text-sm">
            <p className="font-medium text-amber-900">
              Upgrade momentan nicht verfügbar
            </p>
            <p className="mt-0.5 text-amber-800">{checkoutState.message}</p>
            <a
              href={`mailto:${SUPPORT_EMAIL}?subject=Upgrade-Anfrage`}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700"
            >
              <Mail className="h-3.5 w-3.5" />
              {SUPPORT_EMAIL}
            </a>
          </div>
          <button
            type="button"
            onClick={() => setCheckoutState({ kind: "idle" })}
            className="text-xs text-amber-700 hover:underline"
          >
            Schließen
          </button>
        </div>
      )}

      {checkoutState.kind === "error" && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
          <div className="flex-1 text-sm">
            <p className="font-medium text-destructive">
              Fehler beim Upgrade
            </p>
            <p className="mt-0.5 text-destructive/90">
              {checkoutState.message}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setCheckoutState({ kind: "idle" })}
            className="text-xs text-destructive hover:underline"
          >
            Schließen
          </button>
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
                {plan.price === "Auf Anfrage" ? (
                  <span className="text-2xl font-bold">Auf Anfrage</span>
                ) : (
                  <>
                    <span className="text-3xl font-bold">&euro;{plan.price}</span>
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
                  disabled={loadingPlan === plan.id}
                  className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {loadingPlan === plan.id ? "Weiterleitung..." : "Jetzt upgraden"}
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

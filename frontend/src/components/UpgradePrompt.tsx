import { Link } from "react-router-dom";
import { Lock, ArrowRight } from "lucide-react";

interface UpgradePromptProps {
  feature: string;
  requiredPlan: string;
  onClose?: () => void;
}

const PLAN_LABELS: Record<string, string> = {
  pro: "Pro",
  enterprise: "Enterprise",
};

export function UpgradePrompt({ feature, requiredPlan, onClose }: UpgradePromptProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="rounded-full bg-orange-100 p-2">
            <Lock className="h-5 w-5 text-orange-600" />
          </div>
          <h3 className="text-lg font-semibold">Upgrade erforderlich</h3>
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          <strong>{feature}</strong> ist nur im{" "}
          <strong>{PLAN_LABELS[requiredPlan] ?? requiredPlan}</strong>-Plan verfügbar. Upgraden Sie
          jetzt, um diese Funktion freizuschalten.
        </p>
        <div className="flex gap-3">
          <Link
            to="/app/subscription"
            className="flex flex-1 items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Pläne ansehen
            <ArrowRight className="h-4 w-4" />
          </Link>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded-md border px-4 py-2.5 text-sm font-medium hover:bg-accent"
            >
              Abbrechen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function FeatureGate({
  feature,
  allowed,
  requiredPlan,
  children,
}: {
  feature: string;
  allowed: boolean;
  requiredPlan: string;
  children: React.ReactNode;
}) {
  if (allowed) return <>{children}</>;
  return (
    <div className="relative">
      <div className="pointer-events-none opacity-40">{children}</div>
      <div className="absolute inset-0 flex items-center justify-center">
        <Link
          to="/app/subscription"
          className="flex items-center gap-2 rounded-md bg-primary/90 px-4 py-2 text-sm font-medium text-white shadow-lg hover:bg-primary"
        >
          <Lock className="h-4 w-4" />
          {feature} — Upgrade auf {PLAN_LABELS[requiredPlan] ?? requiredPlan}
        </Link>
      </div>
    </div>
  );
}

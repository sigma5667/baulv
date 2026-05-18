/**
 * Wrapper-Component für alle öffentlich-erreichbaren Routen, die in
 * der geschlossenen Beta-Phase hinter dem Coming-Soon-Schutz liegen
 * (v24.4.2). Routen wie ``/impressum``, ``/datenschutz``, ``/agb``
 * werden bewusst NICHT damit umhüllt — Pflichtseiten nach ECG §5
 * müssen immer öffentlich erreichbar sein, sonst riskieren wir eine
 * Impressum-Abmahnung.
 *
 * Render-Logik:
 *
 *   * Eingeloggte User (gültiges JWT) sehen direkt ``children`` —
 *     der Gate liegt nicht im Weg.
 *   * Während Auth-State oder Beta-State noch lädt, zeigen wir einen
 *     Spinner. Ohne das würde der Gate kurz aufblinken, bevor er
 *     gegen die echte Seite ausgetauscht wird.
 *   * Mit gültigem Beta-Token: ``children``.
 *   * Sonst: ``BetaGatePage``.
 *
 * Wichtig: die Komponente löst keine Navigation aus. Wenn ein User
 * auf ``/login`` landet und entsperrt, bleibt die URL auf ``/login``,
 * und der Wrapper rendert beim nächsten Tick die echte LoginPage.
 */
import type { ReactNode } from "react";

import { useAuth } from "../hooks/useAuth";
import { useBetaUnlock } from "../hooks/useBetaUnlock";
import { BetaGatePage } from "../pages/BetaGatePage";

function Spinner() {
  return (
    <div className="flex h-screen items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
    </div>
  );
}

export function PublicWithGate({ children }: { children: ReactNode }) {
  const { user, isLoading: isAuthLoading } = useAuth();
  const { isChecking: isBetaChecking, isUnlocked } = useBetaUnlock();

  // Eingeloggter User: Gate wird übersprungen. Das gilt selbst für
  // Routes wie ``/api-pricing`` (Marketing-Seiten sehen Logged-In-
  // User normal). Für ``/login`` und ``/register`` würde die
  // übergeordnete Route-Config sie auf ``/app`` umleiten, bevor wir
  // hier landen — der Bypass ist hier nur eine Sicherheits-Stütze.
  if (user) return <>{children}</>;

  // Solange einer der beiden State-Checks unterwegs ist: Spinner.
  // Das vermeidet das Flackern, wo der Gate kurz aufgeht und dann
  // sofort wieder durch die echte Seite ersetzt wird.
  if (isAuthLoading || isBetaChecking) return <Spinner />;

  if (isUnlocked) return <>{children}</>;

  return <BetaGatePage />;
}

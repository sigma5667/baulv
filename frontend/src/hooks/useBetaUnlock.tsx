/**
 * Beta-Gate-State (v24.4.2).
 *
 * Der Hook kapselt die drei Zustände, die das ``PublicWithGate``-
 * Wrapper-Component für seine Render-Entscheidung braucht:
 *
 * * **checking** — Wir haben ein Token im localStorage, aber wir
 *   wollen es noch einmal beim Backend gegen den *aktuellen*
 *   ``BETA_ACCESS_CODE`` validieren, bevor wir den User durchlassen.
 *   Das ist der Code-Rotations-Pfad: ändert Tobi den Code in Railway,
 *   schlägt ``GET /api/beta/status`` mit 401 fehl und wir flippen
 *   zurück auf ``locked``.
 *
 * * **unlocked** — Token gültig, App darf gerendert werden.
 *
 * * **locked** — Kein Token, oder Server hat das vorhandene Token
 *   abgelehnt → ``BetaGatePage`` zeigen.
 *
 * Ein Token aus dem localStorage triggert genau eine Server-
 * Validierung pro Mount-Lifecycle. Wird der Hook in mehreren
 * Komponenten gleichzeitig verwendet (z.B. ``<PublicWithGate>`` an
 * mehreren Routen), entsteht je Mount eine Validierung — das ist
 * akzeptabel, weil React bei einem typischen Page-Load nur einmal
 * die zuständige Route-Komponente mountet.
 */
import { useCallback, useEffect, useState } from "react";

import { getBetaStatus, verifyBetaCode } from "../api/beta";

const TOKEN_KEY = "baulv_beta_session";

type BetaState =
  | { phase: "checking" }
  | { phase: "unlocked" }
  | { phase: "locked" };

export interface UseBetaUnlock {
  /** Server-Validierung gerade in-flight (mit gespeichertem Token). */
  isChecking: boolean;
  /** Token ist bestätigt gültig. */
  isUnlocked: boolean;
  /** Beta-Code zur Validierung schicken. Wirft bei falschem Code,
   * Rate-Limit oder Backend-Fehler — der Aufrufer fängt das ab. */
  submit: (code: string) => Promise<void>;
}

export function useBetaUnlock(): UseBetaUnlock {
  // Initial-State: hat der Browser schon ein Token? Wenn ja, müssen
  // wir es kurz beim Server bestätigen lassen. Wenn nein, sind wir
  // sofort im ``locked``-Zustand — Gate zeigen, kein RTT nötig.
  const [state, setState] = useState<BetaState>(() => {
    return localStorage.getItem(TOKEN_KEY)
      ? { phase: "checking" }
      : { phase: "locked" };
  });

  // Server-Validierung beim Mount. Läuft nur, wenn wir tatsächlich
  // ein Token zum Prüfen haben. Bei 401 (Token expired, Code
  // rotiert, Signatur kaputt) wischen wir das Token und flippen auf
  // ``locked`` — der User sieht dann den Gate.
  useEffect(() => {
    if (state.phase !== "checking") return;
    const stored = localStorage.getItem(TOKEN_KEY);
    if (!stored) {
      setState({ phase: "locked" });
      return;
    }
    let cancelled = false;
    getBetaStatus(stored)
      .then(() => {
        if (cancelled) return;
        setState({ phase: "unlocked" });
      })
      .catch(() => {
        if (cancelled) return;
        // Token rejected → Storage räumen, Gate zeigen. Der User
        // kann einen neuen Code eintippen.
        localStorage.removeItem(TOKEN_KEY);
        setState({ phase: "locked" });
      });
    return () => {
      cancelled = true;
    };
  }, [state.phase]);

  const submit = useCallback(async (code: string) => {
    const { token } = await verifyBetaCode(code);
    localStorage.setItem(TOKEN_KEY, token);
    setState({ phase: "unlocked" });
  }, []);

  return {
    isChecking: state.phase === "checking",
    isUnlocked: state.phase === "unlocked",
    submit,
  };
}

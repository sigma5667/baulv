/**
 * API-Client für den Beta-Gate (v24.4.2).
 *
 * Bewusst ein eigener axios-Instance ohne den globalen Interceptor
 * aus ``./client.ts``: der globale 401-Handler würde sonst beim
 * Ablaufen des Beta-Tokens das ``baulv_token`` (= JWT) mit aus dem
 * localStorage werfen, was den eingeloggten User aus seiner Session
 * verdrängen würde, obwohl die beiden Tokens komplett unabhängig
 * sind. Also: separater Axios-Client, eigene Headers, keine
 * Auth-Interceptors.
 *
 * Die Endpoints sind:
 *
 * * ``POST /api/beta/verify`` → Beta-Code prüfen, bei Match neues
 *   30-Tage-Token zurück.
 * * ``GET /api/beta/status``  → Vorhandenes Token gegen den
 *   *aktuellen* ``BETA_ACCESS_CODE`` revalidieren. Wird beim
 *   Page-Load einmal aufgerufen — so erkennt das Frontend eine
 *   Railway-seitige Code-Rotation.
 */
import axios from "axios";

const betaApi = axios.create({
  baseURL: "/api/beta",
  headers: { "Content-Type": "application/json" },
});

export interface BetaVerifyResponse {
  token: string;
  expires_in: number;
}

/** Tausche einen Beta-Code gegen ein signiertes 30-Tage-Token.
 *
 * Wirft bei 401 (falscher Code), 429 (Rate-Limit) oder 5xx. Der
 * Aufrufer (``useBetaUnlock``) fängt das ab und zeigt eine Toast-
 * Meldung — die Fehlerdetails landen im Browser-Network-Tab.
 */
export async function verifyBetaCode(code: string): Promise<BetaVerifyResponse> {
  const res = await betaApi.post<BetaVerifyResponse>("/verify", { code });
  return res.data;
}

/** Prüfe ob ein gespeichertes Token noch zum aktuellen
 * ``BETA_ACCESS_CODE`` passt.
 *
 * Wirft bei 401 (Token abgelaufen, Code rotiert oder Signatur
 * manipuliert). Der Aufrufer reagiert auf einen Wurf damit, dass
 * er das gespeicherte Token aus dem localStorage wirft und das
 * Beta-Gate erneut zeigt.
 */
export async function getBetaStatus(token: string): Promise<void> {
  await betaApi.get("/status", {
    headers: { "X-Beta-Token": token },
  });
}

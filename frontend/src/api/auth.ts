import api from "./client";
import type {
  User,
  TokenResponse,
  FeatureMatrix,
  UserSessionSummary,
  AuditLogEntry,
} from "../types/user";

export async function registerUser(data: {
  email: string;
  password: string;
  full_name: string;
  company_name?: string;
  // v23.2 — DSGVO Art. 7. Frontend reads the version strings from
  // GET /api/legal/versions before showing the form, then ships
  // them back here on submit. The backend rejects with 409 if they
  // don't match the current canonical pins (stale-tab guard).
  accepted_privacy_version: string;
  accepted_terms_version: string;
  marketing_optin: boolean;
}): Promise<TokenResponse> {
  const res = await api.post("/auth/register", data);
  return res.data;
}

/** Public legal-version pins. Used by the registration form to
 * label the consent checkboxes ("Datenschutzerklärung Version 1.0
 * vom 27.04.2026") and to ship the matching strings back on
 * submit. The /me response carries the same data, so logged-in
 * pages don't need this round-trip. */
export interface LegalVersions {
  privacy_version: string;
  privacy_date: string;
  terms_version: string;
  terms_date: string;
}

export async function fetchLegalVersions(): Promise<LegalVersions> {
  const res = await api.get("/auth/legal/versions");
  return res.data;
}

/** Re-record consent after a privacy/terms update. Used by the
 * ConsentRefreshModal that the SPA shows when /me indicates the
 * user's accepted versions are stale. */
export async function refreshConsent(data: {
  accepted_privacy_version: string;
  accepted_terms_version: string;
  marketing_optin: boolean;
}): Promise<User> {
  const res = await api.post("/auth/me/consent/refresh", data);
  return res.data;
}

export async function loginUser(data: {
  email: string;
  password: string;
}): Promise<TokenResponse> {
  const res = await api.post("/auth/login", data);
  return res.data;
}

export async function fetchMe(): Promise<User> {
  const res = await api.get("/auth/me");
  return res.data;
}

export async function updateProfile(data: {
  full_name?: string;
  company_name?: string;
}): Promise<User> {
  const res = await api.put("/auth/me", data);
  return res.data;
}

export async function fetchFeatures(): Promise<FeatureMatrix> {
  const res = await api.get("/auth/me/features");
  return res.data;
}

export async function fetchUsage(): Promise<{
  project_count: number;
  project_limit: number | null;
}> {
  const res = await api.get("/auth/me/usage");
  return res.data;
}

export async function requestPasswordReset(email: string): Promise<void> {
  await api.post("/auth/password-reset", { email });
}

// ---------------------------------------------------------------------------
// DSGVO compliance
// ---------------------------------------------------------------------------

/**
 * Change the current user's password. The server requires the current
 * password as a re-auth step — a stolen token alone is not enough.
 */
export async function changePassword(data: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  await api.post("/auth/me/password", data);
}

/**
 * Art. 20 DSGVO — download a JSON dump of all personal data BauLV holds
 * about the current user. Triggers a native browser download.
 */
export async function downloadMyDataExport(): Promise<void> {
  const res = await api.get("/auth/me/export", { responseType: "blob" });

  // Try to pull the filename out of the Content-Disposition header;
  // fall back to a sensible default if the header is unavailable (e.g.
  // when running against a mock).
  const disposition = res.headers["content-disposition"] as string | undefined;
  let filename = `baulv-export-${new Date().toISOString().slice(0, 10)}.json`;
  if (disposition) {
    const match = /filename="?([^"]+)"?/i.exec(disposition);
    if (match) filename = match[1];
  }

  const blob = new Blob([res.data], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Art. 17 DSGVO — permanently delete the current user's account and
 * every associated record. The server demands both the password and
 * the literal confirmation phrase "LÖSCHEN".
 */
export async function deleteMyAccount(data: {
  password: string;
  confirmation: string;
}): Promise<void> {
  await api.post("/auth/me/delete", data);
}

// ---------------------------------------------------------------------------
// Privacy settings
// ---------------------------------------------------------------------------

export async function updatePrivacySettings(data: {
  marketing_email_opt_in?: boolean;
}): Promise<User> {
  const res = await api.put("/auth/me/privacy", data);
  return res.data;
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

export async function listMySessions(): Promise<UserSessionSummary[]> {
  const res = await api.get("/auth/me/sessions");
  return res.data;
}

export async function revokeSession(sessionId: string): Promise<void> {
  await api.delete(`/auth/me/sessions/${sessionId}`);
}

export async function revokeOtherSessions(): Promise<void> {
  await api.post("/auth/me/sessions/revoke-others");
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

export async function fetchAuditLog(limit = 50): Promise<AuditLogEntry[]> {
  const res = await api.get("/auth/me/audit-log", { params: { limit } });
  return res.data;
}
